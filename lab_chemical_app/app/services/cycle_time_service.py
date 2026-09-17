"""Stage cycle-time service — dwell, waiting, and lead-time analytics.

Derivable entirely from existing PipeStage.stage_date / stage_time — no schema
change. A stage counts as timestamped only when BOTH date and time exist;
intervals needing a missing timestamp are excluded and counted so the report
can show honest coverage.

Definitions
-----------
- dwell(stage)   = timestamp(next stage in canonical order) − timestamp(stage)
- waiting(pair)  = the same interval, aggregated per consecutive stage pair
- lead time      = last − first available timestamp per pipe
"""
from collections import defaultdict
from datetime import datetime
from statistics import mean

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.services import analytics_service


def _timestamp(stage):
    if stage.stage_date is None or stage.stage_time is None:
        return None
    return datetime.combine(stage.stage_date, stage.stage_time)


def _stats(values):
    return {
        "count": len(values),
        "avg": mean(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def stage_dwell(filters):
    """Per-stage dwell stats, per-pair waiting stats, lead times, coverage."""
    pipe_ids = [
        row[0] for row in analytics_service.apply_pipe_filters(Pipe.query, filters)
        .with_entities(Pipe.id).all()
    ]
    empty = {
        "stages": [], "pairs": [],
        "lead_times": {"count": 0, "avg": 0.0, "min": 0.0, "max": 0.0},
        "coverage": {"intervals_possible": 0, "intervals_used": 0,
                     "excluded_missing_ts": 0, "excluded_negative": 0,
                     "fraction": 0.0},
        "filters": filters,
    }
    if not pipe_ids:
        return empty

    stages = (
        db.session.query(PipeStage)
        .filter(PipeStage.pipe_id.in_(pipe_ids))
        .all()
    )
    canonical = ProductionStage.active_names()
    order_index = {name: i for i, name in enumerate(canonical)}

    by_pipe = defaultdict(list)
    for s in stages:
        by_pipe[s.pipe_id].append(s)

    dwells = defaultdict(list)   # stage -> [hours]
    waits = defaultdict(list)    # (from, to) -> [hours]
    leads = []
    used = missing_ts = negative = possible = 0

    for pipe_stages in by_pipe.values():
        ordered = sorted(
            pipe_stages,
            key=lambda s: (order_index.get(s.stage_name, len(order_index)),
                           s.stage_name),
        )
        timestamps = []
        for s in ordered:
            ts = _timestamp(s)
            timestamps.append(ts)

        for i in range(len(ordered) - 1):
            possible += 1
            t1, t2 = timestamps[i], timestamps[i + 1]
            if t1 is None or t2 is None:
                missing_ts += 1
                continue
            hours = (t2 - t1).total_seconds() / 3600.0
            if hours < 0:
                negative += 1
                continue
            used += 1
            dwells[ordered[i].stage_name].append(hours)
            waits[(ordered[i].stage_name, ordered[i + 1].stage_name)].append(hours)

        available = [ts for ts in timestamps if ts is not None]
        if len(available) >= 2:
            lead = (max(available) - min(available)).total_seconds() / 3600.0
            if lead >= 0:
                leads.append(lead)

    stage_rows = [
        {"stage": name, **_stats(vals)} for name, vals in dwells.items()
    ]
    stage_rows.sort(key=lambda r: r["avg"], reverse=True)  # bottleneck first

    pair_rows = [
        {"from": a, "to": b, **_stats(vals)} for (a, b), vals in waits.items()
    ]
    pair_rows.sort(key=lambda r: r["avg"], reverse=True)

    return {
        "stages": stage_rows,
        "pairs": pair_rows,
        "lead_times": _stats(leads),
        "coverage": {
            "intervals_possible": possible,
            "intervals_used": used,
            "excluded_missing_ts": missing_ts,
            "excluded_negative": negative,
            "fraction": (used / possible) if possible else 0.0,
        },
        "stage_list": canonical,
        "filters": filters,
    }
