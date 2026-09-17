"""Every KPI this system can read for one production day, in one dict.

The dashboard tiles were the only place a KPI existed, so the targets screen
could only offer the five things a tile happened to show. The floor asks about
more than that — how much came through right the first time, how much was
reworked, how much is still sitting undecided, whether the lab is passing the
metal — and every one of those is already computed somewhere in this codebase
for a report. This module gathers them under one key each, so a target and an
alert can exist for anything the plant actually measures.

Keys match `kpi_target_service.KPIS`. A reading that cannot be computed is
None rather than zero: "no pipes were weighed today" is not "the yield was
0%", and only the first of those should be safe from an alert.
"""

from collections import defaultdict
from datetime import date as _date

from app import db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.stage_history import PipeStageHistory
from app.services import analytics_service, bi_service

# Readings that are a rate: meaningful the moment there is enough of a sample,
# whether or not the day is over.
RATE_KEYS = frozenset({
    "yield_pct", "reject_pct", "fty_pct", "first_pass_pct", "rft_pct",
    "rework_pct", "hold_pct", "pending_pct", "defect_pct", "saving_pct",
    "chem_reject_pct", "mech_fail_pct",
})

# Readings that are a total: how much was made, how much was saved. A running
# total is below any sensible target for most of the day, so these are only
# judged once the day is over. Alerting at 09:00 that today's tonnage is under
# target is not information, it is the clock.
TOTAL_KEYS = frozenset({
    "produced", "actual_mt", "saving_mt", "nonconformances",
})

# Below this many decided pipes a rate is noise — one pipe rejected is 100%.
MIN_SAMPLE = 5


def _rework(decision):
    from app.services.analytics_service import is_rework

    return is_rework(decision)


def readings(day=None):
    """`{kpi_key: value_or_None}` for `day`, plus the counts behind them.

    Every value is a plain number the target screen can be compared against;
    the extra `_counts` entry carries the denominators so a caller can decide
    whether the sample is big enough to judge.
    """
    day = day or _date.today()
    iso = day.isoformat()
    filters = {"date_from": iso, "date_to": iso}

    pipes = analytics_service.apply_pipe_filters(Pipe.query, filters).all()
    out = {"_counts": {"pipes": len(pipes), "decided": 0}}
    if not pipes:
        return out

    # The tiles' own numbers, so a target set against the dashboard means the
    # same thing here.
    out.update(bi_service.bi_dashboard(filters)["kpis"])

    decided = [p for p in pipes
               if bi_service._is_final(bi_service._effective_decision(p))]
    out["_counts"]["decided"] = len(decided)

    ids = [p.id for p in pipes]
    live = defaultdict(list)
    history = defaultdict(list)
    defects = set()
    for row in PipeStage.query.filter(PipeStage.pipe_id.in_(ids)).all():
        live[row.pipe_id].append(row.decision)
        if row.has_defect:
            defects.add(row.pipe_id)
    for row in (PipeStageHistory.query
                .filter(PipeStageHistory.pipe_id.in_(ids)).all()):
        history[row.pipe_id].append(row.decision)

    # Right First Time in the sense the existing report uses — no stage was
    # ever sent back for rework and the pipe came out accepted — but read off
    # the operative decision rather than `final_decision_value` alone. That
    # column is filled on seven of the sixty-seven pipes on prod, so keying
    # off it reported RFT as 0% on a day whose FTY was 80%, and a target on
    # that number would have raised a false alert every single morning.
    reworked = 0
    first_time_right = 0
    held = 0
    for pipe in pipes:
        verdicts = live[pipe.id] + history[pipe.id]
        accepted = PipeStage.classify_decision(
            bi_service._effective_decision(pipe)) == "accept"
        if any(_rework(d) for d in verdicts if d):
            reworked += 1
        elif accepted:
            first_time_right += 1
        if (pipe.lab_decision or "").upper() == "HOLD" or any(
                PipeStage.classify_decision(d) == "pending" and (d or "").strip()
                for d in verdicts):
            held += 1

    total = len(pipes)
    # Over the pipes decided today, not over everything registered. On prod's
    # busiest day 17 of 22 pipes were still working their way down the line,
    # so measuring against all of them read RFT as 18% — a number about
    # work-in-progress wearing the name of a quality metric, and a target on
    # it would have fired every day for as long as the plant was busy. The
    # denominator now matches FTY's, so the two can be read side by side.
    out["rft_pct"] = bi_service._pct(first_time_right, len(decided))
    out["rework_pct"] = bi_service._pct(reworked, total)
    out["hold_pct"] = bi_service._pct(held, total)
    out["pending_pct"] = bi_service._pct(total - len(decided), total)
    out["defect_pct"] = bi_service._pct(len(defects), total)

    # The lab's own two gates, which the pipe-level numbers cannot show: a
    # ladle rejected on chemistry never becomes a rejected pipe, it becomes no
    # pipe at all.
    chem = ChemicalAnalysis.query.filter(ChemicalAnalysis.test_date == day).all()
    chem_decided = [c for c in chem if bi_service._is_final(c.decision)]
    out["chem_reject_pct"] = bi_service._pct(
        sum(1 for c in chem_decided if bi_service._is_reject(c.decision)),
        len(chem_decided))

    mech = MechanicalTest.query.filter(MechanicalTest.test_date == day).all()
    mech_decided = [m for m in mech if bi_service._is_final(m.decision)]
    out["mech_fail_pct"] = bi_service._pct(
        sum(1 for m in mech_decided if bi_service._is_reject(m.decision)),
        len(mech_decided))

    out["_counts"].update({
        "chem_decided": len(chem_decided),
        "mech_decided": len(mech_decided),
        # Pipes registered but not yet on the scale. Every weight reading —
        # yield, saving, tonnage — is about these and not about the rest: a
        # morning's pipes waiting to be weighed is not a 0% yield.
        "weighed": sum(1 for p in pipes if (p.actual_weight or 0) > 0),
    })
    return out


def scoped_rates(day=None):
    """Reject rate per machine, per stage, per mould and per shift for `day`.

    `{scope: {key: {"decided": n, "rejects": n}}}` — the alert engine turns
    each into a rate and compares it with that scope's own threshold.

    Shift is here as a cut of the day, not as a reporting period: the day is
    still what gets judged, but "the day was 6%" and "the night shift was 14%"
    are different facts and only the second one tells anybody where to go. The
    machine is the CCM caster, which is the machine a pipe records.
    """
    day = day or _date.today()
    iso = day.isoformat()
    pipes = analytics_service.apply_pipe_filters(
        Pipe.query, {"date_from": iso, "date_to": iso}).all()

    machine = defaultdict(lambda: {"decided": 0, "rejects": 0})
    mold = defaultdict(lambda: {"decided": 0, "rejects": 0})
    stage = defaultdict(lambda: {"decided": 0, "rejects": 0})
    shift = defaultdict(lambda: {"decided": 0, "rejects": 0})
    dn = defaultdict(lambda: {"decided": 0, "rejects": 0})

    for pipe in pipes:
        decision = bi_service._effective_decision(pipe)
        if not bi_service._is_final(decision):
            continue
        rejected = bi_service._is_reject(decision)
        code = analytics_service.pipe_machine_code(pipe)
        if code:
            machine[code]["decided"] += 1
            machine[code]["rejects"] += int(rejected)
        if pipe.mold_number:
            mold[pipe.mold_number]["decided"] += 1
            mold[pipe.mold_number]["rejects"] += int(rejected)
        if pipe.shift:
            key = f"وردية {pipe.shift}"
            shift[key]["decided"] += 1
            shift[key]["rejects"] += int(rejected)
        # DN is the diameter the pipe was cast at. A DN running hot is a
        # tooling or spec problem, and it does not show in any of the cuts
        # above — a bad DN spread across every machine reads as normal.
        if pipe.diameter:
            key = f"DN{pipe.diameter}"
            dn[key]["decided"] += 1
            dn[key]["rejects"] += int(rejected)

    ids = [p.id for p in pipes]
    if ids:
        for row in PipeStage.query.filter(PipeStage.pipe_id.in_(ids)).all():
            if not bi_service._is_final(row.decision):
                continue
            stage[row.stage_name]["decided"] += 1
            stage[row.stage_name]["rejects"] += int(
                bi_service._is_reject(row.decision))

    return {"machine": machine, "stage": stage, "mold": mold,
            "shift": shift, "dn": dn}


def _rate_over(date_from, date_to):
    """Decided pipes and rejects between two dates, inclusive."""
    pipes = analytics_service.apply_pipe_filters(
        Pipe.query, {"date_from": date_from.isoformat(),
                     "date_to": date_to.isoformat()}).all()
    decided = rejects = 0
    for pipe in pipes:
        decision = bi_service._effective_decision(pipe)
        if not bi_service._is_final(decision):
            continue
        decided += 1
        rejects += int(bi_service._is_reject(decision))
    pct = round(rejects / decided * 100, 2) if decided else None
    return {"decided": decided, "rejects": rejects, "pct": pct}


def day_reject_rate(day=None):
    """The plant's reject rate for one production day."""
    day = day or _date.today()
    out = _rate_over(day, day)
    out["label"] = day.isoformat()
    return out


def month_reject_rate(day=None):
    """The plant's reject rate for the calendar month containing `day`.

    Month to date, not a rolling 30 days: the plant reports by calendar month,
    and a rolling window would move the number under a reader who compared it
    with last week's copy of the same screen.
    """
    day = day or _date.today()
    first = day.replace(day=1)
    out = _rate_over(first, day)
    out["label"] = day.strftime("%Y-%m")
    return out


def reject_trend(days=30, end=None):
    """Reject rate per day for the last `days` days, oldest first.

    One query per period would be `days` round trips; this walks the window
    once and buckets by production date instead.
    """
    from datetime import timedelta

    end = end or _date.today()
    start = end - timedelta(days=days - 1)
    pipes = analytics_service.apply_pipe_filters(
        Pipe.query, {"date_from": start.isoformat(),
                     "date_to": end.isoformat()}).all()

    by_day = defaultdict(lambda: {"decided": 0, "rejects": 0})
    by_month = defaultdict(lambda: {"decided": 0, "rejects": 0})
    for pipe in pipes:
        decision = bi_service._effective_decision(pipe)
        if not bi_service._is_final(decision) or not pipe.production_date:
            continue
        rejected = int(bi_service._is_reject(decision))
        d = by_day[pipe.production_date.isoformat()]
        d["decided"] += 1
        d["rejects"] += rejected
        m = by_month[pipe.production_date.strftime("%Y-%m")]
        m["decided"] += 1
        m["rejects"] += rejected

    def _rows(buckets):
        rows = []
        for key in sorted(buckets):
            tally = buckets[key]
            decided = tally["decided"]
            rows.append({
                "key": key,
                "decided": decided,
                "rejects": tally["rejects"],
                "pct": round(tally["rejects"] / decided * 100, 2) if decided else None,
                "judged": decided >= MIN_SAMPLE,
            })
        return rows

    return {"by_day": _rows(by_day), "by_month": _rows(by_month)}


def scoped_table(day=None, targets=None):
    """The same cuts as rows a screen can render, worst rate first.

    Every scope, not only the ones over their threshold: a shift at 9% against
    a 10% target raises nothing and is exactly what a supervisor wants to see
    before it becomes an alert.
    """
    from app.services import kpi_target_service

    targets = targets if targets is not None else kpi_target_service.load()
    threshold = {
        "machine": targets.get("machine_reject_pct"),
        "stage": targets.get("stage_reject_pct"),
        "mold": targets.get("mold_reject_pct"),
        "shift": targets.get("shift_reject_pct"),
        "dn": targets.get("dn_reject_pct"),
    }
    out = {}
    for scope, groups in scoped_rates(day).items():
        rows = []
        for key, tally in groups.items():
            decided = tally["decided"]
            pct = round(tally["rejects"] / decided * 100, 2) if decided else None
            limit = threshold.get(scope)
            rows.append({
                "key": key,
                "decided": decided,
                "rejects": tally["rejects"],
                "pct": pct,
                "target": limit,
                # None where there is no target or too small a sample to judge
                # — the row still shows, it just carries no verdict.
                "over": (None if pct is None or limit is None
                         or decided < MIN_SAMPLE else pct > limit),
                "judged": decided >= MIN_SAMPLE,
            })
        out[scope] = sorted(rows, key=lambda r: -(r["pct"] or 0))
    return out
