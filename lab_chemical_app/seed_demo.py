"""
DEMO data seeder for the lab decision engine — visualise every decision scenario
in the live website. ALL rows are tagged with ladle_id prefix 'DEMO-' so they are
trivially identifiable and removable.

Usage (inside the prod container, writes to the real DB):
    python seed_demo.py seed     # wipe existing DEMO-* then create all scenarios
    python seed_demo.py clean    # remove ALL DEMO-* rows (chemical, pipes, mech, stages)
    python seed_demo.py list     # print DEMO ladles + resulting pipe decisions

Safe: only ever touches rows whose ladle_id starts with 'DEMO-'.
"""

import sys
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.services.pipe_decision_service import (
    assign_mechanical_roles,
    propagate_mechanical_result,
)

PREFIX = "DEMO-"

DT = {
    "LAST_ONLY": "فحص أخيرة فقط",
    "FIRST_LAST": "فحص أولى وأخيرة",
    "FULL_100": "فحص الشحنة 100%",
    "REJECT": "تالف",
}

# 17 demo ladles (the 16 sweep scenarios; isolation = 2 ladles).
# ladle: { db, dt, n, actions, note}. action: {select, result, retest?}
LADLES = [
    {"id": "DEMO-01", "dt": "LAST_ONLY", "n": 4, "note": "أخيرة فقط — العينة نجحت ⇒ الكل ACCEPT",
     "actions": [{"select": "LAST", "result": "ACCEPT"}]},
    {"id": "DEMO-02", "dt": "LAST_ONLY", "n": 4, "note": "أخيرة فقط — العينة رسبت ⇒ الكل HOLD (الإصلاح الجديد)",
     "actions": [{"select": "LAST", "result": "REJECT"}]},
    {"id": "DEMO-03", "dt": "LAST_ONLY", "n": 4, "note": "أخيرة فقط — لم تُختبر بعد ⇒ الكل WAITING",
     "actions": []},
    {"id": "DEMO-04", "dt": "LAST_ONLY", "n": 1, "note": "أخيرة فقط — ماسورة واحدة (هي الأخيرة) رسبت ⇒ HOLD",
     "actions": [{"select": "LAST", "result": "REJECT"}]},
    {"id": "DEMO-05", "dt": "LAST_ONLY", "n": 4, "note": "أخيرة فقط — رسبت ثم أعيد الاختبار ونجحت ⇒ تعافى الكل ACCEPT",
     "actions": [{"select": "LAST", "result": "REJECT"}, {"select": "LAST", "result": "ACCEPT", "retest": True}]},

    {"id": "DEMO-06", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — كلاهما نجح ⇒ الكل ACCEPT",
     "actions": [{"select": "FIRST", "result": "ACCEPT"}, {"select": "LAST", "result": "ACCEPT"}]},
    {"id": "DEMO-07", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — كلاهما رسب ⇒ الكل HOLD",
     "actions": [{"select": "FIRST", "result": "REJECT"}, {"select": "LAST", "result": "REJECT"}]},
    {"id": "DEMO-08", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — الأولى نجحت والأخيرة رسبت ⇒ الأولى ACCEPT والباقي HOLD",
     "actions": [{"select": "FIRST", "result": "ACCEPT"}, {"select": "LAST", "result": "REJECT"}]},
    {"id": "DEMO-09", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — سُجّلت الأولى فقط (نجحت) ⇒ الأولى ACCEPT والباقي WAITING",
     "actions": [{"select": "FIRST", "result": "ACCEPT"}]},
    {"id": "DEMO-10", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — سُجّلت الأولى فقط (رسبت) ⇒ الأولى HOLD والباقي WAITING",
     "actions": [{"select": "FIRST", "result": "REJECT"}]},
    {"id": "DEMO-11", "dt": "FIRST_LAST", "n": 4, "note": "أولى وأخيرة — سُجّلت الأخيرة فقط (نجحت) ⇒ الأخيرة ACCEPT والباقي WAITING",
     "actions": [{"select": "LAST", "result": "ACCEPT"}]},

    {"id": "DEMO-12", "dt": "FULL_100", "n": 3, "note": "100% — ماسورة #2 نجحت (الباقي لم يُختبر) ⇒ #2 ACCEPT والباقي WAITING",
     "actions": [{"select": 2, "result": "ACCEPT"}]},
    {"id": "DEMO-13", "dt": "FULL_100", "n": 3, "note": "100% — ماسورة #2 رسبت ⇒ #2 REJECT نهائي (الباقي WAITING)",
     "actions": [{"select": 2, "result": "REJECT"}]},
    {"id": "DEMO-14", "dt": "FULL_100", "n": 3, "note": "100% — نجاح/رسوب/نجاح ⇒ ACCEPT / REJECT نهائي / ACCEPT",
     "actions": [{"select": 1, "result": "ACCEPT"}, {"select": 2, "result": "REJECT"}, {"select": 3, "result": "ACCEPT"}]},

    {"id": "DEMO-15", "dt": "REJECT", "n": 3, "note": "تالف كيميائياً ⇒ الكل BLOCKED + REJECT نهائي",
     "actions": []},

    {"id": "DEMO-16", "dt": "LAST_ONLY", "n": 3, "note": "عزل اللادل (أ): أخيرة فقط نجحت ⇒ الكل ACCEPT",
     "actions": [{"select": "LAST", "result": "ACCEPT"}]},
    {"id": "DEMO-17", "dt": "REJECT", "n": 3, "note": "عزل اللادل (ب): تالف ⇒ الكل BLOCKED (لا يؤثر على اللادل أ)",
     "actions": []},
]

# cosmetic chemistry so chemical pages aren't blank
CHEM = dict(carbon=3.62, silicon=2.45, magnesium=0.045, manganese=0.21,
            sulfur=0.011, phosphorus=0.031, copper=0.28, chromium=0.04,
            carbon_equivalent=4.31)


def _pick(pipes, select):
    if select == "FIRST":
        return next(p for p in pipes if p.mechanical_test_role == "FIRST")
    if select == "LAST":
        return next(p for p in pipes if p.mechanical_test_role == "LAST")
    return next(p for p in pipes if p.arrange_pipe == int(select))


def _demo_ladle_ids():
    return [l["id"] for l in LADLES]


def clean():
    ids = [r.ladle_id for r in ChemicalAnalysis.query.filter(
        ChemicalAnalysis.ladle_id.like(PREFIX + "%")).all()]
    # also catch any orphan pipes/tests by prefix
    pipe_ids = [p.id for p in Pipe.query.filter(Pipe.ladle_id.like(PREFIX + "%")).all()]
    n_stage = 0
    if pipe_ids:
        n_stage = PipeStage.query.filter(PipeStage.pipe_id.in_(pipe_ids)).delete(
            synchronize_session=False)
    n_mech = MechanicalTest.query.filter(MechanicalTest.ladle_id.like(PREFIX + "%")).delete(
        synchronize_session=False)
    n_pipe = Pipe.query.filter(Pipe.ladle_id.like(PREFIX + "%")).delete(
        synchronize_session=False)
    n_chem = ChemicalAnalysis.query.filter(ChemicalAnalysis.ladle_id.like(PREFIX + "%")).delete(
        synchronize_session=False)
    db.session.commit()
    print(f"CLEAN removed: {n_chem} ladles, {n_pipe} pipes, {n_mech} mech tests, {n_stage} stages "
          f"(ladles: {', '.join(sorted(ids)) or 'none'})")


def _furnace():
    f = Furnace.query.filter_by(furnace_code="DEMO-F").first()
    if not f:
        f = Furnace(furnace_code="DEMO-F", furnace_name="Demo Furnace")
        db.session.add(f)
        db.session.commit()
    return f


def seed():
    clean()
    furnace = _furnace()
    today = date.today()
    for li, lad in enumerate(LADLES, start=1):
        lid = lad["id"]
        ca = ChemicalAnalysis(
            test_date=today, furnace=furnace, ladle_no=900 + li, ladle_id=lid,
            decision=DT[lad["dt"]], engineer_notes=lad["note"], notes="DEMO seed", **CHEM,
        )
        db.session.add(ca)
        db.session.commit()
        pipes = []
        for i in range(1, lad["n"] + 1):
            p = Pipe(production_date=today, ladle_id=lid, pipe_code=f"{lid}-P{i}",
                     no_code=f"{lid}N{i}", arrange_pipe=i, diameter=300, pipe_class="K9",
                     manufacturing_order="DEMO")
            db.session.add(p)
            pipes.append(p)
        db.session.commit()
        assign_mechanical_roles(lid)
        db.session.commit()
        for act in lad["actions"]:
            target = _pick(pipes, act["select"])
            if act.get("retest"):
                for t in MechanicalTest.query.filter_by(
                        ladle_id=lid, pipe_code=target.pipe_code, status="ACTIVE").all():
                    t.status = "SUPERSEDED"
                db.session.commit()
            passed = act["result"] == "ACCEPT"
            mt = MechanicalTest(
                test_date=today, ladle_id=lid, pipe=target, pipe_code=target.pipe_code,
                diameter=300, decision=act["result"], status="ACTIVE",
                tensile_strength=45.0 if passed else 28.0,
                elongation=12.0 if passed else 3.0,
                nodularity_percent=90.0 if passed else 60.0,
                retest_reason="DEMO retest" if act.get("retest") else None,
            )
            db.session.add(mt)
            db.session.commit()
            propagate_mechanical_result(mt)
    print(f"SEED created {len(LADLES)} DEMO ladles.")
    _list()


def _list():
    print("\nDEMO ladles and resulting pipe decisions:")
    for lid in _demo_ladle_ids():
        ca = ChemicalAnalysis.query.filter_by(ladle_id=lid).first()
        if not ca:
            continue
        pipes = Pipe.query.filter_by(ladle_id=lid).order_by(Pipe.arrange_pipe).all()
        tags = [f"{p.arrange_pipe}:{p.mechanical_test_role or '-'}={p.lab_decision}" for p in pipes]
        print(f"  {lid} [{ca.decision}] {ca.engineer_notes}")
        print(f"       {tags}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    app = create_app()
    with app.app_context():
        if cmd == "seed":
            seed()
        elif cmd == "clean":
            clean()
        elif cmd == "list":
            _list()
        else:
            print("usage: python seed_demo.py [seed|clean|list]")
