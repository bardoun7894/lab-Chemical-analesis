"""BI service — Phase-2 analytics closing the gap vs the Excel BI dashboard.

All read-only, computed per request. Conventions shared with Phase 1:
- decision compares are case-insensitive (project rule)
- reject % denominators are DECIDED records only; zero-denominator -> None
  (rendered as a no-data marker, never 0%)
- weight metrics use analytics_service.planned_weight() so every report
  reconciles with the weight-saving report
"""

from collections import defaultdict
from datetime import date

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


def _is_reject(decision):
    """Reject per the model's own classifier — catches 'rejected', 'تالف',
    'مرتجع', 'Returned', legacy lowercase, not just exact 'reject'."""
    return PipeStage.classify_decision(decision) == "reject"


def _is_final(decision):
    """Accept or reject (HOLD/WAITING stay out of reject-% denominators)."""
    return PipeStage.classify_decision(decision) in ("accept", "reject")


def _decided(decision):
    return bool((decision or "").strip())


def _effective_decision(pipe):
    """The pipe's operative outcome: final_decision_value, else lab_decision.

    On real production data final_decision_value is filled on a small minority
    of pipes while lab_decision is filled on nearly all of them, so reading the
    final value alone hides most output. The lab pending states (WAITING /
    HOLD / BLOCKED) classify as 'pending', so falling back never turns an
    undecided pipe into an accept or a reject.
    """
    return (pipe.final_decision_value or "").strip() or (
        (pipe.lab_decision or "").strip()
    )


def _pct(part, whole):
    return round(part / whole * 100, 2) if whole else None


# ---------------------------------------------------------------------------
# US1 — YTD comparison
# ---------------------------------------------------------------------------


def _year_month_buckets(year):
    pipes = Pipe.query.filter(
        Pipe.production_date >= date(year, 1, 1),
        Pipe.production_date <= date(year, 12, 31),
    ).all()
    buckets = defaultdict(
        lambda: {
            "count": 0,
            "actual": 0.0,
            "planned": 0.0,
            "decided": 0,
            "rejects": 0,
        }
    )
    for p in pipes:
        if not p.production_date:
            continue
        b = buckets[p.production_date.month]
        b["count"] += 1
        b["actual"] += float(p.actual_weight or 0)
        planned, source = analytics_service.planned_weight(p)
        if source:
            b["planned"] += planned
        if _is_final(p.final_decision_value):
            b["decided"] += 1
            if _is_reject(p.final_decision_value):
                b["rejects"] += 1
    return buckets


def ytd_comparison(year, compare_year):
    """Month-by-month production/weight/reject for two years with deltas."""
    cur = _year_month_buckets(year)
    prev = _year_month_buckets(compare_year)

    def _row(bucket):
        return {
            "count": bucket["count"],
            "actual": round(bucket["actual"], 1),
            "planned": round(bucket["planned"], 1),
            "rejects": bucket["rejects"],
            "reject_pct": _pct(bucket["rejects"], bucket["decided"]),
        }

    months = []
    for m in range(1, 13):
        c = cur.get(
            m, {"count": 0, "actual": 0.0, "planned": 0.0, "decided": 0, "rejects": 0}
        )
        p = prev.get(
            m, {"count": 0, "actual": 0.0, "planned": 0.0, "decided": 0, "rejects": 0}
        )
        months.append(
            {
                "month": m,
                "year": _row(c),
                "compare": _row(p),
                "delta_count": c["count"] - p["count"],
            }
        )
    return {"year": year, "compare_year": compare_year, "months": months}


def stage_reject_matrix(year, compare_year):
    """Reject % per stage × month for both years. None = no decided stages."""
    stages = ProductionStage.active_names()
    lo = date(min(year, compare_year), 1, 1)
    hi = date(max(year, compare_year), 12, 31)
    rows = (
        db.session.query(PipeStage)
        .filter(PipeStage.stage_date >= lo, PipeStage.stage_date <= hi)
        .all()
    )
    agg = defaultdict(lambda: {"decided": 0, "rejects": 0})
    for s in rows:
        if not s.stage_date or not _is_final(s.decision):
            continue
        key = (s.stage_name, s.stage_date.year, s.stage_date.month)
        agg[key]["decided"] += 1
        if _is_reject(s.decision):
            agg[key]["rejects"] += 1

    cells = {}
    for stage in stages:
        for m in range(1, 13):
            cur = agg.get((stage, year, m))
            prev = agg.get((stage, compare_year, m))
            cells[(stage, m)] = {
                "year": _pct(cur["rejects"], cur["decided"]) if cur else None,
                "compare": _pct(prev["rejects"], prev["decided"]) if prev else None,
            }
    return {
        "stages": stages,
        "cells": cells,
        "year": year,
        "compare_year": compare_year,
    }


# ---------------------------------------------------------------------------
# US2 — period compare with alerts
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "reject_pct": 2.0,  # percentage points
    "defect_count": 5,  # absolute
    "produced": None,  # informational by default
    "actual_weight": None,
    "planned_weight": None,
    "saving_kg": None,
}


def _period_kpis(d_from, d_to):
    pipes = Pipe.query.filter(
        Pipe.production_date >= d_from, Pipe.production_date <= d_to
    ).all()
    decided = rejects = 0
    actual = planned = 0.0
    for p in pipes:
        actual += float(p.actual_weight or 0)
        w, source = analytics_service.planned_weight(p)
        if source:
            planned += w
        if _is_final(p.final_decision_value):
            decided += 1
            if _is_reject(p.final_decision_value):
                rejects += 1
    pipe_ids = [p.id for p in pipes]
    defects = (
        (
            db.session.query(PipeStage)
            .filter(PipeStage.pipe_id.in_(pipe_ids), PipeStage.has_defect.is_(True))
            .count()
        )
        if pipe_ids
        else 0
    )
    return {
        "produced": len(pipes),
        "actual_weight": round(actual, 1),
        "planned_weight": round(planned, 1),
        "reject_pct": _pct(rejects, decided),
        "defect_count": defects,
        "saving_kg": round(planned - actual, 1),
        "empty": len(pipes) == 0,
    }


def period_compare(a_from, a_to, b_from, b_to, thresholds):
    """KPI deltas between period A and B plus threshold alerts.

    Alert rule: |delta| must be strictly greater than the threshold (breaching
    exactly at the threshold is NOT an alert). Direction = sign of delta.
    """
    a = _period_kpis(a_from, a_to)
    b = _period_kpis(b_from, b_to)
    merged = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    meta = [
        ("produced", "Produced (pipes)", ""),
        ("actual_weight", "Actual weight", "kg"),
        ("planned_weight", "Planned weight (ISO/product)", "kg"),
        ("reject_pct", "Reject %", "pp"),
        ("defect_count", "Defects", ""),
        ("saving_kg", "Saving", "kg"),
    ]
    kpis, alerts = [], []
    for key, label, unit in meta:
        va, vb = a[key], b[key]
        delta = None if (va is None or vb is None) else round(vb - va, 2)
        kpis.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "a": va,
                "b": vb,
                "delta": delta,
                "threshold": merged.get(key),
            }
        )
        th = merged.get(key)
        if th is not None and delta is not None and abs(delta) > th:
            alerts.append(
                {
                    "key": key,
                    "label": label,
                    "unit": unit,
                    "delta": delta,
                    "threshold": th,
                    "direction": "up" if delta > 0 else "down",
                }
            )
    return {
        "kpis": kpis,
        "alerts": alerts,
        "a_empty": a["empty"],
        "b_empty": b["empty"],
        "a_from": a_from,
        "a_to": a_to,
        "b_from": b_from,
        "b_to": b_to,
    }


# ---------------------------------------------------------------------------
# US3 — stage funnel
# ---------------------------------------------------------------------------


def stage_funnel(filters):
    """Pipes reaching each stage (canonical order) with consecutive drops."""
    pipe_ids = [
        row[0]
        for row in analytics_service.apply_pipe_filters(Pipe.query, filters)
        .with_entities(Pipe.id)
        .all()
    ]
    stages = ProductionStage.active_names()
    counts = {name: 0 for name in stages}
    if pipe_ids:
        extra = defaultdict(int)
        rows = (
            db.session.query(
                PipeStage.stage_name, db.func.count(db.distinct(PipeStage.pipe_id))
            )
            .filter(PipeStage.pipe_id.in_(pipe_ids))
            .group_by(PipeStage.stage_name)
            .all()
        )
        for name, n in rows:
            if name in counts:
                counts[name] = n
            else:
                # Stage row whose name isn't in the active list (renamed or
                # deactivated) — keep it visible at the end rather than lose it.
                extra[name] = n
        ordered = stages + sorted(extra)
        counts.update(extra)
    else:
        ordered = stages

    result, prev = [], None
    for name in ordered:
        n = counts[name]
        if prev is None:
            drop_abs, drop_pct = 0, None
        else:
            drop_abs = prev - n
            drop_pct = _pct(prev - n, prev) if prev else None
        result.append(
            {"stage": name, "count": n, "drop_abs": drop_abs, "drop_pct": drop_pct}
        )
        if n > 0:
            prev = n
    max_count = max(counts.values()) if counts else 0
    return {"rows": result, "max": max_count, "filters": filters}


# ---------------------------------------------------------------------------
# US4 — defect heatmap DN × pipe class
# ---------------------------------------------------------------------------


def _dn_class_cells(filters):
    """Group filtered pipes by (diameter, class) -> {cell: pipes}."""
    pipes = analytics_service.apply_pipe_filters(Pipe.query, filters).all()
    cells = defaultdict(list)
    for p in pipes:
        if p.diameter and p.pipe_class:
            cells[(p.diameter, p.pipe_class)].append(p)
    return cells


def defect_heatmap(filters, metric="count"):
    """Cells: defects and pipes per (DN, class). % metric = defects/pipes."""
    cells = _dn_class_cells(filters)
    out = {}
    max_value = 0.0
    for (dn, cls), pipes in cells.items():
        ids = [p.id for p in pipes]
        defects = (
            db.session.query(PipeStage)
            .filter(PipeStage.pipe_id.in_(ids), PipeStage.has_defect.is_(True))
            .count()
        )
        value = float(defects) if metric == "count" else _pct(defects, len(pipes))
        if value is not None and value > max_value:
            max_value = value
        out[(dn, cls)] = {"defects": defects, "pipes": len(pipes), "value": value}
    dns = sorted({dn for dn, _c in cells})
    classes = sorted({c for _dn, c in cells})
    return {
        "cells": out,
        "dns": dns,
        "classes": classes,
        "max": max_value,
        "metric": metric,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# US5 — saving matrix DN × pipe class
# ---------------------------------------------------------------------------


def saving_matrix(filters):
    """Planned/actual/saving per (DN, class) using the planned_weight fallback."""
    cells = _dn_class_cells(filters)
    out = {}
    excluded = 0
    totals = {"planned": 0.0, "actual": 0.0, "saving": 0.0, "count": 0}
    for (dn, cls), pipes in cells.items():
        planned = actual = 0.0
        count = 0
        for p in pipes:
            w, source = analytics_service.planned_weight(p)
            if not source:
                excluded += 1
                continue
            planned += w
            actual += float(p.actual_weight or 0)
            count += 1
        saving = round(planned - actual, 1)
        out[(dn, cls)] = {
            "planned": round(planned, 1),
            "actual": round(actual, 1),
            "saving": saving,
            "count": count,
            "saving_pct": _pct(saving, planned),
        }
        totals["planned"] += planned
        totals["actual"] += actual
        totals["count"] += count
    totals["saving"] = round(totals["planned"] - totals["actual"], 1)
    totals["planned"] = round(totals["planned"], 1)
    totals["actual"] = round(totals["actual"], 1)
    dns = sorted({dn for dn, _c in cells})
    classes = sorted({c for _dn, c in cells})
    return {
        "cells": out,
        "dns": dns,
        "classes": classes,
        "totals": totals,
        "excluded_no_weight": excluded,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# US6 — data quality
# ---------------------------------------------------------------------------


def data_quality():
    """Field fill rates, per-stage timestamp coverage, score, ranked gaps."""
    pipes = Pipe.query.all()
    n = len(pipes)

    def _pipe_field(key, label, pred, blocks):
        filled = sum(1 for p in pipes if pred(p))
        return {
            "key": key,
            "label": label,
            "filled": filled,
            "total": n,
            "pct": _pct(filled, n) or 0.0,
            "blocks": blocks,
        }

    fields = [
        _pipe_field(
            "actual_weight",
            "Actual weight",
            lambda p: p.actual_weight is not None,
            "Weight Saving, SPC weight modes",
        ),
        _pipe_field(
            "iso_weight",
            "ISO weight (> 0)",
            lambda p: bool(p.iso_weight and p.iso_weight > 0),
            "Weight Saving (falls back to product standard)",
        ),
        _pipe_field(
            "product_id",
            "Product link",
            lambda p: p.product_id is not None,
            "Weight-saving product fallback, DN analytics",
        ),
        _pipe_field(
            "production_order_id",
            "Production order link",
            lambda p: p.production_order_id is not None,
            "Order filters, customer reports",
        ),
        _pipe_field(
            "shift_engineer",
            "Shift engineer",
            lambda p: bool(p.shift_engineer),
            "Engineer attribution reports",
        ),
        _pipe_field(
            "final_decision",
            "Final decision",
            lambda p: _decided(p.final_decision_value),
            "Reject %, P-charts, YTD",
        ),
    ]

    stages = PipeStage.query.all()
    m = len(stages)
    for key, label, pred, blocks in [
        (
            "stage_date",
            "Stage date",
            lambda s: s.stage_date is not None,
            "Cycle time, YTD stage matrix",
        ),
        (
            "stage_time",
            "Stage time",
            lambda s: s.stage_time is not None,
            "Stage Cycle Time report (needs date AND time)",
        ),
        (
            "stage_machine",
            "Stage machine",
            lambda s: s.machine_id is not None,
            "Machine performance, machine filters",
        ),
    ]:
        filled = sum(1 for s in stages if pred(s))
        fields.append(
            {
                "key": key,
                "label": label,
                "filled": filled,
                "total": m,
                "pct": _pct(filled, m) or 0.0,
                "blocks": blocks,
            }
        )

    per_stage = []
    by_stage = defaultdict(lambda: {"total": 0, "date": 0, "time": 0})
    for s in stages:
        b = by_stage[s.stage_name]
        b["total"] += 1
        if s.stage_date:
            b["date"] += 1
        if s.stage_time:
            b["time"] += 1
    for name in sorted(by_stage):
        b = by_stage[name]
        per_stage.append(
            {
                "stage": name,
                "total": b["total"],
                "date_pct": _pct(b["date"], b["total"]) or 0.0,
                "time_pct": _pct(b["time"], b["total"]) or 0.0,
            }
        )

    rated = [f for f in fields if f["total"] > 0]
    score = round(sum(f["pct"] for f in rated) / len(rated), 1) if rated else 100.0
    gaps = sorted((f for f in rated if f["pct"] < 100), key=lambda f: f["pct"])
    return {
        "fields": fields,
        "per_stage": per_stage,
        "score": score,
        "gaps": gaps,
        "pipe_total": n,
        "stage_total": m,
    }


# ---------------------------------------------------------------------------
# US7 — auto diagnosis (deterministic rules, no AI)
# ---------------------------------------------------------------------------

SEVERITY_WEIGHTS = {
    "reject_spike": 25,
    "negative_saving": 20,
    "stage_concentration": 15,
    "machine_concentration": 15,
    "undecided_pipes": 15,
    "stage_time_gap": 10,
}

MIN_DECIDED = 10  # reject-rate rules never fire on tiny samples
MIN_REJECTS = 5


def _month_window(today):
    first = today.replace(day=1)
    prev_end = first.fromordinal(first.toordinal() - 1)
    trail_start = prev_end.replace(day=1)
    trail_start = trail_start.fromordinal(trail_start.toordinal() - 1).replace(day=1)
    trail_start = trail_start.fromordinal(trail_start.toordinal() - 1).replace(day=1)
    return first, trail_start


def diagnose(today=None):
    """Run the rule set. Returns {score, problems[], checked_at}."""
    today = today or date.today()
    problems = []

    month_start, trail_start = _month_window(today)

    def _reject_stats(d_from, d_to):
        pipes = Pipe.query.filter(
            Pipe.production_date >= d_from, Pipe.production_date <= d_to
        ).all()
        decided = [p for p in pipes if _is_final(p.final_decision_value)]
        rejects = [p for p in decided if _is_reject(p.final_decision_value)]
        return pipes, decided, rejects

    cur_pipes, cur_decided, cur_rejects = _reject_stats(month_start, today)
    # Trailing window ends the day BEFORE this month starts — the boundary
    # day belongs to exactly one window.
    _tp, trail_decided, trail_rejects = _reject_stats(
        trail_start, month_start.fromordinal(month_start.toordinal() - 1)
    )

    # --- rule: reject_spike (current month vs trailing 3 months) -----------
    if len(cur_decided) >= MIN_DECIDED and len(trail_decided) >= MIN_DECIDED:
        pct_now = len(cur_rejects) / len(cur_decided) * 100
        pct_trail = len(trail_rejects) / len(trail_decided) * 100
        if pct_now > pct_trail * 1.5 and (pct_now - pct_trail) >= 5:
            hint = _reject_rca_hint(cur_rejects)
            problems.append(
                {
                    "rule": "reject_spike",
                    "severity": "high",
                    "title": f"Reject % spiked to {pct_now:.1f}% this month "
                    f"(trailing 3-month avg {pct_trail:.1f}%)",
                    "detail": f"{len(cur_rejects)} rejects of {len(cur_decided)} "
                    f"decided pipes this month.",
                    "rca_hint": hint,
                    "drill_url": "/reports/defect-analysis",
                }
            )

    # --- rule: stage_concentration (one stage > 40% of this month's rejects)
    # Classified in Python — stored stage decisions are display strings
    # ('تالف', 'rejected', ...) that a SQL lower()=='reject' would miss.
    window_stages = (
        db.session.query(PipeStage)
        .filter(PipeStage.stage_date >= month_start, PipeStage.stage_date <= today)
        .all()
    )
    stage_reject_counts = defaultdict(int)
    for s in window_stages:
        if _is_reject(s.decision):
            stage_reject_counts[s.stage_name] += 1
    stage_rejects = sorted(stage_reject_counts.items(), key=lambda r: -r[1])
    total_stage_rejects = sum(n for _s, n in stage_rejects)
    if total_stage_rejects >= MIN_REJECTS:
        top_name, top_n = max(stage_rejects, key=lambda r: r[1])
        share = top_n / total_stage_rejects * 100
        if share > 40:
            problems.append(
                {
                    "rule": "stage_concentration",
                    "severity": "medium",
                    "title": f"Stage '{top_name}' drives {share:.0f}% of this month's rejects",
                    "detail": f"{top_n} of {total_stage_rejects} rejected stage "
                    f"decisions this month.",
                    "rca_hint": f"stage: {top_name} — check its recent inputs "
                    f"(machine, shift, material batch)",
                    "drill_url": "/reports/stage-performance",
                }
            )

    # --- rule: machine_concentration (>60% of defects on one machine) ------
    machine_defects = (
        db.session.query(PipeStage.machine_id, db.func.count())
        .filter(
            PipeStage.has_defect.is_(True),
            PipeStage.machine_id.isnot(None),
            PipeStage.stage_date >= month_start,
            PipeStage.stage_date <= today,
        )
        .group_by(PipeStage.machine_id)
        .all()
    )
    total_md = sum(n for _m, n in machine_defects)
    if total_md >= MIN_REJECTS:
        top_mid, top_n = max(machine_defects, key=lambda r: r[1])
        share = top_n / total_md * 100
        if share > 60:
            from app.models.chemical import Machine

            machine = db.session.get(Machine, top_mid)
            code = machine.machine_code if machine else str(top_mid)
            problems.append(
                {
                    "rule": "machine_concentration",
                    "severity": "medium",
                    "title": f"Machine {code} holds {share:.0f}% of this month's defects",
                    "detail": f"{top_n} of {total_md} defects with a recorded machine.",
                    "rca_hint": f"machine: {code} — inspect tooling/setup before "
                    f"blaming material",
                    "drill_url": "/reports/machine-performance",
                }
            )

    # --- rule: undecided_pipes (>15% of this month's pipes, no decision) ---
    if len(cur_pipes) >= MIN_DECIDED:
        undecided = len(cur_pipes) - len(cur_decided)
        pct = undecided / len(cur_pipes) * 100
        if pct > 15:
            problems.append(
                {
                    "rule": "undecided_pipes",
                    "severity": "medium",
                    "title": f"{pct:.0f}% of this month's pipes have no final decision",
                    "detail": f"{undecided} of {len(cur_pipes)} pipes undecided — "
                    f"they vanish from reject % and P-charts.",
                    "rca_hint": "data flow: chase stage decisions through the "
                    "Stage Console",
                    "drill_url": "/reports/non-conformance",
                }
            )

    # --- rule: stage_time_gap (<20% of dated stage rows have a time) -------
    dated = PipeStage.query.filter(PipeStage.stage_date.isnot(None)).all()
    if len(dated) >= 10:
        timed = sum(1 for s in dated if s.stage_time)
        pct = timed / len(dated) * 100
        if pct < 20:
            problems.append(
                {
                    "rule": "stage_time_gap",
                    "severity": "low",
                    "title": f"Stage time recorded on only {pct:.1f}% of stage entries",
                    "detail": f"{timed} of {len(dated)} dated stage rows have a "
                    f"time — cycle-time analytics are blind without it.",
                    "rca_hint": "data entry: record the time, not just the date, "
                    "at each stage",
                    "drill_url": "/reports/stage-cycle-time",
                }
            )

    # --- rule: negative_saving (planned < actual over weighed pipes) --------
    weighed = 0
    planned_total = actual_total = 0.0
    for p in Pipe.query.all():
        w, source = analytics_service.planned_weight(p)
        if not source:
            continue
        weighed += 1
        planned_total += w
        actual_total += float(p.actual_weight or 0)
    if weighed >= 5 and planned_total < actual_total:
        problems.append(
            {
                "rule": "negative_saving",
                "severity": "high",
                "title": f"Actual weight exceeds planned by "
                f"{actual_total - planned_total:.0f} kg overall",
                "detail": f"{weighed} weighed pipes: planned "
                f"{planned_total:.0f} vs actual {actual_total:.0f} kg.",
                "rca_hint": "process: check mold wear / coating thickness on the "
                "heaviest DN",
                "drill_url": "/reports/weight-saving",
            }
        )

    score = max(0, 100 - sum(SEVERITY_WEIGHTS[p["rule"]] for p in problems))
    return {"score": score, "problems": problems, "checked_at": today}


# ---------------------------------------------------------------------------
# In-app replica of the CCM BI dashboard (v44) — same KPIs/groupings, live data
# ---------------------------------------------------------------------------


def bi_dashboard(filters):
    """Aggregate payload for the one-page BI dashboard.

    KPI formulas mirror the Excel dashboard:
    - produced   = pipe count
    - yield %    = total actual / total planned weight (planned_weight fallback)
    - reject %   = rejects / decided pipes (final decision, classified)
    - actual MT  = sum actual_weight / 1000
    - saving MT  = (planned − actual) / 1000
    Every group carries BOTH count and weight so the UI can toggle عدد/وزن
    client-side without a round-trip.
    """
    pipes = analytics_service.apply_pipe_filters(Pipe.query, filters).all()

    planned_kg = actual_kg = actual_weighed_kg = 0.0
    decided = rejects = 0
    for p in pipes:
        actual_kg += float(p.actual_weight or 0)
        w, source = analytics_service.planned_weight(p)
        if source:
            planned_kg += w
            # Yield/saving must compare the SAME pipe set on both sides —
            # pipes without a planned weight would inflate actual-only.
            actual_weighed_kg += float(p.actual_weight or 0)
        if _is_final(p.final_decision_value):
            decided += 1
            if _is_reject(p.final_decision_value):
                rejects += 1

    kpis = {
        "produced": len(pipes),
        "planned_kg": round(planned_kg, 1),
        "actual_kg": round(actual_kg, 1),
        "yield_pct": _pct(actual_weighed_kg, planned_kg),
        "reject_pct": _pct(rejects, decided),
        "decided": decided,
        "rejects": rejects,
        "actual_mt": round(actual_kg / 1000, 3),
        "planned_mt": round(planned_kg / 1000, 3),
        "saving_mt": round((planned_kg - actual_weighed_kg) / 1000, 3),
    }

    def _group(key_fn):
        agg = defaultdict(lambda: {"count": 0, "weight": 0.0, "length": 0.0})
        order = []
        for p in pipes:
            key = key_fn(p)
            if key not in agg:
                order.append(key)
            agg[key]["count"] += 1
            agg[key]["weight"] += float(p.actual_weight or 0)
            agg[key]["length"] += analytics_service.pipe_metric_value(p, "length")
        return [
            {
                "key": k,
                "count": agg[k]["count"],
                "weight": round(agg[k]["weight"], 1),
                "length": round(agg[k]["length"], 1),
            }
            for k in order
        ]

    groups = {
        "month": _group(
            lambda p: (
                p.production_date.strftime("%Y-%m") if p.production_date else "Unknown"
            )
        ),
        "shift": _group(lambda p: f"Shift {p.shift}" if p.shift else "Unknown shift"),
        "machine": _group(
            lambda p: analytics_service.pipe_machine_code(p) or "Unknown"
        ),
        "dn": _group(lambda p: f"DN{p.diameter}" if p.diameter else "Unknown DN"),
        "mold": _group(lambda p: p.mold_number or "Unknown mold"),
    }
    groups["month"].sort(key=lambda r: r["key"])
    for name in ("shift", "machine", "dn", "mold"):
        groups[name].sort(key=lambda r: -r["count"])

    # --- defect reasons top 10 (stage defect_reason, then defect_type) ------
    pipe_ids = [p.id for p in pipes]
    reasons = []
    stage_rows = []
    if pipe_ids:
        stage_rows = (
            db.session.query(PipeStage).filter(PipeStage.pipe_id.in_(pipe_ids)).all()
        )
    reason_counts = defaultdict(int)
    for s in stage_rows:
        if s.has_defect:
            label = (s.defect_reason or s.defect_type or "غير محدد").strip()
            reason_counts[label] += 1
    reasons = [
        {"reason": r, "count": n}
        for r, n in sorted(reason_counts.items(), key=lambda x: -x[1])[:10]
    ]

    # --- stage funnel + reject-by-stage ------------------------------------
    funnel = stage_funnel(filters)["rows"]
    stage_reject_counts = defaultdict(int)
    stage_decided_counts = defaultdict(int)
    for s in stage_rows:
        if _is_final(s.decision):
            stage_decided_counts[s.stage_name] += 1
            if _is_reject(s.decision):
                stage_reject_counts[s.stage_name] += 1
    reject_by_stage = [
        {"stage": name, "rejects": n, "decided": stage_decided_counts[name]}
        for name, n in sorted(stage_reject_counts.items(), key=lambda x: -x[1])
    ]

    # --- daily trend ---------------------------------------------------------
    daily = defaultdict(lambda: {"count": 0, "weight": 0.0, "length": 0.0})
    for p in pipes:
        if not p.production_date:
            continue
        key = p.production_date.isoformat()
        daily[key]["count"] += 1
        daily[key]["weight"] += float(p.actual_weight or 0)
        daily[key]["length"] += analytics_service.pipe_metric_value(p, "length")
    trend = [
        {
            "key": k,
            "count": daily[k]["count"],
            "weight": round(daily[k]["weight"], 1),
            "length": round(daily[k]["length"], 1),
        }
        for k in sorted(daily)
    ]

    return {
        "kpis": kpis,
        "groups": groups,
        "reasons": reasons,
        "stage": {"funnel": funnel, "reject_by_stage": reject_by_stage},
        "trends": {"daily": trend},
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Daily report — parity with CCM_BI_Dashboard_v52.html (buildDailyReport)
# ---------------------------------------------------------------------------

_AR_MONTHS = (
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)


def _daily_stats(pipes):
    """Port of v52 calcStats — decided-only denominators, v52 thresholds.

    `produced` counts every pipe cast on the date; `n` counts only those with
    an accept/reject outcome and is the reject-% denominator. The two diverge
    whenever output is still awaiting a decision.
    """
    valid = [p for p in pipes if _is_final(_effective_decision(p))]
    rej_ids = {p.id for p in valid if _is_reject(_effective_decision(p))}
    rejects = [p for p in valid if p.id in rej_ids]

    iso = sum(float(p.iso_weight or 0) for p in valid)
    act = sum(float(p.actual_weight or 0) for p in valid)

    def _breakdown(key_fn):
        agg = defaultdict(lambda: {"total": 0, "rejects": 0})
        for p in valid:
            key = key_fn(p)
            agg[key]["total"] += 1
            if p.id in rej_ids:
                agg[key]["rejects"] += 1
        return agg

    by_machine = _breakdown(
        lambda p: analytics_service.pipe_machine_code(p) or "؟"
    )
    by_sup = _breakdown(lambda p: (p.shift_engineer or "").strip() or "؟")
    by_shift = _breakdown(lambda p: p.shift if p.shift not in (None, "") else "؟")

    # --- shift × DN × class detail, with decision-maker from stage approvals
    detail = {}
    for p in valid:
        shift = p.shift if p.shift not in (None, "") else "؟"
        dn = p.diameter if p.diameter not in (None, "") else "؟"
        cls = (p.pipe_class or "").strip() or "؟"
        key = (shift, dn, cls)
        g = detail.setdefault(
            key, {"shift": shift, "dn": dn, "cls": cls,
                  "total": 0, "rejects": 0, "approvers": defaultdict(int)}
        )
        g["total"] += 1
        if p.id in rej_ids:
            g["rejects"] += 1
    has_approver = False
    if detail:
        stage_rows = (
            db.session.query(PipeStage)
            .filter(PipeStage.pipe_id.in_([p.id for p in valid]))
            .filter(PipeStage.approved_by_id.isnot(None))
            .all()
        )
        pipe_to_key = {}
        for p in valid:
            shift = p.shift if p.shift not in (None, "") else "؟"
            dn = p.diameter if p.diameter not in (None, "") else "؟"
            cls = (p.pipe_class or "").strip() or "؟"
            pipe_to_key[p.id] = (shift, dn, cls)
        user_ids = {s.approved_by_id for s in stage_rows}
        users = {
            u.id: (u.full_name or u.full_name_ar or u.username or str(u.id))
            for u in User.query.filter(User.id.in_(user_ids)).all()
        } if user_ids else {}
        for s in stage_rows:
            key = pipe_to_key.get(s.pipe_id)
            if key is None:
                continue
            name = users.get(s.approved_by_id)
            if name:
                has_approver = True
                detail[key]["approvers"][name] += 1

    reason_counts = defaultdict(int)
    for p in rejects:
        reason = (p.final_decision_reason or "").strip()
        if reason and reason.lower() != "nan":
            reason_counts[reason] += 1
    top_reasons = sorted(reason_counts.items(), key=lambda x: -x[1])[:5]

    mold_counts = defaultdict(int)
    for p in rejects:
        mold = (p.mold_number or "").strip()
        if mold and mold.lower() != "nan":
            mold_counts[mold] += 1
    top_molds = sorted(mold_counts.items(), key=lambda x: -x[1])[:3]

    return {
        "produced": len(pipes),
        "n": len(valid),
        "rejects": len(rejects),
        "rej_pct": (len(rejects) / len(valid) * 100) if valid else 0.0,
        "iso_kg": round(iso, 1),
        "act_kg": round(act, 1),
        "sav_pct": ((iso - act) / iso * 100) if iso > 0 else 0.0,
        "sav_kg": round(iso - act, 1),
        "by_machine": by_machine,
        "by_sup": by_sup,
        "by_shift": by_shift,
        "shift_detail": detail,
        "has_approver": has_approver,
        "top_reasons": top_reasons,
        "top_molds": top_molds,
    }


def daily_report(report_date=None):
    """v52 daily report: today vs yesterday vs last-6-production-days."""
    all_dates = [
        d for (d,) in db.session.query(Pipe.production_date)
        .filter(Pipe.production_date.isnot(None))
        .distinct().order_by(Pipe.production_date)
        .all()
    ]
    if not all_dates:
        return None

    today = report_date if report_date in all_dates else all_dates[-1]
    prev = [d for d in all_dates if d < today]
    yesterday = prev[-1] if prev else None
    week_dates = prev[-6:]

    window = {today, yesterday, *week_dates} - {None}
    pipes = Pipe.query.filter(Pipe.production_date.in_(window)).all()

    t = _daily_stats([p for p in pipes if p.production_date == today])
    y = (
        _daily_stats([p for p in pipes if p.production_date == yesterday])
        if yesterday else None
    )
    wk = (
        _daily_stats([p for p in pipes if p.production_date in week_dates])
        if week_dates else None
    )
    week_avg = None
    if wk:
        week_avg = {
            # Output per day is a production rate, so it counts every pipe
            # cast — not only the ones that already have an outcome.
            "n_per_day": round(wk["produced"] / len(week_dates)),
            "rej_pct": wk["rej_pct"],
        }

    # v52 status thresholds (4% / 7%)
    if t["n"] == 0 and t["produced"] > 0:
        # Output with no outcome yet — a 0% reject rate here means "not judged",
        # not "good", so don't paint the day green.
        status = {"color": "#64748b", "text": "بانتظار القرار ⏳"}
    elif t["rej_pct"] > 7:
        status = {"color": "#dc2626", "text": "يحتاج تدخل فوري 🔴"}
    elif t["rej_pct"] > 4:
        status = {"color": "#d97706", "text": "متابعة مستمرة 🟡"}
    else:
        status = {"color": "#16a34a", "text": "جيد ✅"}

    def _machine_rows(today_agg, yest_agg):
        rows = []
        for key, g in sorted(
            today_agg.items(), key=lambda kv: kv[1]["rejects"] / kv[1]["total"]
            if kv[1]["total"] else 0, reverse=True
        ):
            pct = g["rejects"] / g["total"] * 100 if g["total"] else 0.0
            yg = yest_agg.get(key) if yest_agg else None
            rows.append({
                "key": key, "total": g["total"], "rejects": g["rejects"],
                "pct": round(pct, 1),
                "y_pct": (round(yg["rejects"] / yg["total"] * 100, 1)
                          if yg and yg["total"] else None),
            })
        return rows

    machine_rows = _machine_rows(t["by_machine"], y and y["by_machine"])
    sup_rows = _machine_rows(t["by_sup"], y and y["by_sup"])

    def _shift_sort_key(shift):
        """Type-safe shift ordering: numeric shifts first, NULL/'؟' last."""
        if isinstance(shift, (int, float)):
            return (0, shift)
        return (1, 0)

    shift_rows = sorted(
        _machine_rows(t["by_shift"], None),
        key=lambda r: _shift_sort_key(r["key"]),
    )

    shift_detail_rows = []
    for (shift, _, _), g in sorted(
        t["shift_detail"].items(),
        key=lambda kv: (_shift_sort_key(kv[0][0]), -kv[1]["total"]),
    ):
        pct = g["rejects"] / g["total"] * 100 if g["total"] else 0.0
        approvers = sorted(g["approvers"].items(), key=lambda x: -x[1])
        shift_detail_rows.append({
            "shift": shift, "dn": g["dn"], "cls": g["cls"], "total": g["total"],
            "rejects": g["rejects"], "pct": round(pct, 1),
            "approver": (approvers[0][0] + (f" +{len(approvers) - 1}"
                         if len(approvers) > 1 else "")) if approvers else "—",
        })

    # --- alerts (v52 renderDailyHTML 5994-5998)
    alerts = []
    for key, g in t["by_machine"].items():
        pct = g["rejects"] / g["total"] * 100 if g["total"] else 0.0
        if pct >= 7:
            alerts.append(f"🔴 ماكينة {key} رفض {pct:.1f}% — تدخل فوري")
        elif pct >= 4:
            alerts.append(f"🟡 ماكينة {key} رفض {pct:.1f}% — متابعة")
    if t["sav_pct"] < 0:
        alerts.append(
            f"🔴 Saving سالب {t['sav_pct']:.2f}% — مراجعة أوزان الصب فوراً")
    if t["rej_pct"] >= 7:
        alerts.append(
            f"🔴 معدل الرفض الكلي {t['rej_pct']:.2f}% تجاوز الحد المسموح (7%)")
    if t["top_reasons"] and t["top_reasons"][0][1] >= 5:
        alerts.append(
            f"⚠️ السبب \"{t['top_reasons'][0][0]}\" تكرر "
            f"{t['top_reasons'][0][1]} مرات اليوم")

    # --- auto executive summary (v52 5856-5866)
    arabic_date = (
        f"{today.day} {_AR_MONTHS[today.month]} {today.year}"
    )
    worst = machine_rows[0] if machine_rows and machine_rows[0]["pct"] > 0 else None
    best = (machine_rows[-1] if machine_rows else None)
    top_reason = t["top_reasons"][0] if t["top_reasons"] else None

    summary = f"تقرير يوم {arabic_date}: "
    summary += f"الإنتاج الكلي {t['produced']} ماسورة"
    if t["produced"] != t["n"]:
        summary += f" (تم البت في {t['n']})"
    summary += f"، معدل الرفض {t['rej_pct']:.2f}%"
    if y:
        summary += (f" ({'تحسّن' if t['rej_pct'] < y['rej_pct'] else 'ارتفع'}"
                    f" عن أمس {y['rej_pct']:.2f}%)")
    summary += ". "
    if worst:
        summary += f"أعلى رفض: {worst['key']} بمعدل {worst['pct']:.1f}%. "
    if top_reason:
        summary += f"السبب الأول: \"{top_reason[0]}\" ({top_reason[1]} حالة). "
    summary += f"Saving: {t['sav_pct']:.2f}%."

    return {
        "date": today.isoformat(),
        "arabic_date": arabic_date,
        "yesterday": yesterday.isoformat() if yesterday else None,
        "status": status,
        "today": t,
        "yest": y,
        "week_avg": week_avg,
        "machine_rows": machine_rows,
        "sup_rows": sup_rows,
        "shift_rows": shift_rows,
        "shift_detail_rows": shift_detail_rows,
        "top_reasons": [
            {"reason": r, "count": n,
             "pct": round(n / t["rejects"] * 100, 1) if t["rejects"] else 0.0}
            for r, n in t["top_reasons"]
        ],
        "top_molds": [{"mold": m, "count": n} for m, n in t["top_molds"]],
        "summary": summary,
        "alerts": alerts,
        "signatures": ["مسؤول الإنتاج", "مراقب الجودة", "مدير التشغيل"],
    }


def _reject_rca_hint(rejects):
    """Point at the dominant dimension of the current rejects."""
    if not rejects:
        return None
    rows = (
        db.session.query(PipeStage)
        .filter(PipeStage.pipe_id.in_([p.id for p in rejects]))
        .all()
    )
    stage_counts = defaultdict(int)
    for s in rows:
        if _is_reject(s.decision):
            stage_counts[s.stage_name] += 1
    if stage_counts:
        name, n = max(stage_counts.items(), key=lambda r: r[1])
        total = sum(stage_counts.values())
        return f"stage: {name} ({n / total * 100:.0f}% of stage rejects)"
    dns = defaultdict(int)
    for p in rejects:
        dns[p.diameter or 0] += 1
    top_dn, n = max(dns.items(), key=lambda r: r[1])
    label = f"DN{top_dn}" if top_dn else "unknown DN"
    return (
        f"no stage reject data recorded; concentration by dimension: "
        f"stage data missing, top DN: {label} ({n} pipes)"
    )
