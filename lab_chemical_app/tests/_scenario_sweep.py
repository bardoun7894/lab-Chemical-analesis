"""
Decision-engine scenario sweep (NOT a unittest — a deterministic harness).

Runs a broad matrix of ladle/mechanical scenarios through the REAL engine
(`assign_mechanical_roles` + `propagate_mechanical_result`) on an isolated
in-memory DB, and prints the actual per-pipe outcomes as JSON. Used to feed an
adversarial-verification workflow that judges each outcome against the HTML
decision spec v1.2.

Run:  .venv/bin/python -m tests._scenario_sweep
"""

import json
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe
from app.services.pipe_decision_service import (
from app.models.permission import seed_default_permissions
    assign_mechanical_roles,
    propagate_mechanical_result,
)

# Arabic decision strings -> human label (for the report)
DT = {
    "LAST_ONLY": "فحص أخيرة فقط",
    "FIRST_LAST": "فحص أولى وأخيرة",
    "FULL_100": "فحص الشحنة 100%",
    "REJECT": "تالف",
}


# Each scenario: id, description, list of ladles.
# A ladle = {dt, n, actions}. An action = {select, result, retest?}.
#   select: "FIRST" | "LAST" | int index (1-based) | "role:ALL@<idx>"
#   result: "ACCEPT" | "REJECT"
SCENARIOS = [
    # ---- LAST_ONLY ----
    {"id": "last_pass", "desc": "LAST_ONLY, 4 pipes, last sample PASS",
     "ladles": [{"dt": "LAST_ONLY", "n": 4, "actions": [{"select": "LAST", "result": "ACCEPT"}]}]},
    {"id": "last_fail", "desc": "LAST_ONLY, 4 pipes, last sample FAIL (spec: all HOLD)",
     "ladles": [{"dt": "LAST_ONLY", "n": 4, "actions": [{"select": "LAST", "result": "REJECT"}]}]},
    {"id": "last_untested", "desc": "LAST_ONLY, 4 pipes, no mechanical recorded yet",
     "ladles": [{"dt": "LAST_ONLY", "n": 4, "actions": []}]},
    {"id": "last_single_pipe_fail", "desc": "LAST_ONLY, 1 pipe (first==last), FAIL",
     "ladles": [{"dt": "LAST_ONLY", "n": 1, "actions": [{"select": "LAST", "result": "REJECT"}]}]},
    {"id": "last_fail_then_retest_pass", "desc": "LAST_ONLY, 4 pipes, last FAIL then RETEST PASS (recovery)",
     "ladles": [{"dt": "LAST_ONLY", "n": 4, "actions": [
         {"select": "LAST", "result": "REJECT"},
         {"select": "LAST", "result": "ACCEPT", "retest": True}]}]},

    # ---- FIRST_LAST ----
    {"id": "fl_both_pass", "desc": "FIRST_LAST, 4 pipes, both samples PASS",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [
         {"select": "FIRST", "result": "ACCEPT"}, {"select": "LAST", "result": "ACCEPT"}]}]},
    {"id": "fl_both_fail", "desc": "FIRST_LAST, 4 pipes, both samples FAIL (spec: all HOLD)",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [
         {"select": "FIRST", "result": "REJECT"}, {"select": "LAST", "result": "REJECT"}]}]},
    {"id": "fl_mixed_first_pass", "desc": "FIRST_LAST, 4 pipes, first PASS last FAIL",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [
         {"select": "FIRST", "result": "ACCEPT"}, {"select": "LAST", "result": "REJECT"}]}]},
    {"id": "fl_partial_first_pass", "desc": "FIRST_LAST, 4 pipes, ONLY first recorded = PASS",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [{"select": "FIRST", "result": "ACCEPT"}]}]},
    {"id": "fl_partial_first_fail", "desc": "FIRST_LAST, 4 pipes, ONLY first recorded = FAIL",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [{"select": "FIRST", "result": "REJECT"}]}]},
    {"id": "fl_partial_last_pass", "desc": "FIRST_LAST, 4 pipes, ONLY last recorded = PASS",
     "ladles": [{"dt": "FIRST_LAST", "n": 4, "actions": [{"select": "LAST", "result": "ACCEPT"}]}]},

    # ---- FULL_100 ----
    {"id": "full_one_pass", "desc": "FULL_100, 3 pipes, pipe#2 PASS (others untested)",
     "ladles": [{"dt": "FULL_100", "n": 3, "actions": [{"select": 2, "result": "ACCEPT"}]}]},
    {"id": "full_one_fail", "desc": "FULL_100, 3 pipes, pipe#2 FAIL = terminal REJECT (others untested)",
     "ladles": [{"dt": "FULL_100", "n": 3, "actions": [{"select": 2, "result": "REJECT"}]}]},
    {"id": "full_all_mixed", "desc": "FULL_100, 3 pipes, p1 PASS / p2 FAIL / p3 PASS",
     "ladles": [{"dt": "FULL_100", "n": 3, "actions": [
         {"select": 1, "result": "ACCEPT"}, {"select": 2, "result": "REJECT"}, {"select": 3, "result": "ACCEPT"}]}]},

    # ---- REJECT (chemical تالف) ----
    {"id": "rejected", "desc": "تالف chemical reject, 3 pipes (terminal BLOCKED/REJECT)",
     "ladles": [{"dt": "REJECT", "n": 3, "actions": []}]},

    # ---- Multi-ladle isolation ----
    {"id": "two_ladles_isolation", "desc": "Two ladles: A=LAST_ONLY last PASS, B=تالف — cascade must not leak across ladles",
     "ladles": [
         {"dt": "LAST_ONLY", "n": 3, "actions": [{"select": "LAST", "result": "ACCEPT"}]},
         {"dt": "REJECT", "n": 3, "actions": []}]},
]


def _pick(pipes, select):
    if select == "FIRST":
        return next(p for p in pipes if p.mechanical_test_role == "FIRST")
    if select == "LAST":
        return next(p for p in pipes if p.mechanical_test_role == "LAST")
    # integer 1-based index by arrange_pipe
    return next(p for p in pipes if p.arrange_pipe == int(select))


def run_one(scn):
    app = create_app("testing")
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    seed_default_permissions()
    furnace = Furnace(furnace_code="F1", furnace_name="F1")
    db.session.add(furnace)
    db.session.commit()

    out_ladles = []
    for li, lad in enumerate(scn["ladles"], start=1):
        ladle_id = f"{scn['id']}-L{li}"
        ca = ChemicalAnalysis(
            test_date=date(2026, 4, 14), furnace=furnace,
            ladle_no=li, ladle_id=ladle_id, decision=DT[lad["dt"]],
        )
        db.session.add(ca)
        db.session.commit()
        pipes = []
        for i in range(1, lad["n"] + 1):
            p = Pipe(production_date=date(2026, 4, 14), ladle_id=ladle_id,
                     pipe_code=f"{ladle_id}-P{i}", no_code=f"{ladle_id}N{i}",
                     arrange_pipe=i, diameter=300, pipe_class="K9")
            db.session.add(p)
            pipes.append(p)
        db.session.commit()
        assign_mechanical_roles(ladle_id)
        db.session.commit()

        for act in lad["actions"]:
            target = _pick(pipes, act["select"])
            if act.get("retest"):
                for t in MechanicalTest.query.filter_by(
                        ladle_id=ladle_id, pipe_code=target.pipe_code, status="ACTIVE").all():
                    t.status = "SUPERSEDED"
                db.session.commit()
            mt = MechanicalTest(test_date=date(2026, 4, 15), ladle_id=ladle_id,
                                pipe=target, pipe_code=target.pipe_code,
                                decision=act["result"], status="ACTIVE")
            db.session.add(mt)
            db.session.commit()
            propagate_mechanical_result(mt)

        for p in pipes:
            db.session.refresh(p)
        out_ladles.append({
            "ladle_id": ladle_id,
            "decision_type": lad["dt"],
            "decision_ar": DT[lad["dt"]],
            "pipes": [{
                "pipe_code": p.pipe_code,
                "arrange_pipe": p.arrange_pipe,
                "role": p.mechanical_test_role,
                "lab_decision": p.lab_decision,
                "final_decision_value": p.final_decision_value,
                "cascade_from": p.cascade_from,
            } for p in pipes],
        })

    ctx.pop()
    return {"id": scn["id"], "desc": scn["desc"], "ladles": out_ladles}


def main():
    results = [run_one(s) for s in SCENARIOS]
    out_path = "/tmp/scenario_sweep.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"WROTE {len(results)} scenarios -> {out_path}")


if __name__ == "__main__":
    main()
