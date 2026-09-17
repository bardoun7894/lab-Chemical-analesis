"""Andon board — what the floor needs to see from across the room.

One query pass over the pipes that are still in play, turned into three
things: how much work sits at each stage, which pipes are stopped and for how
long, and today's yield. Nothing here is new data — it is the same decisions
the stage screens already record, aggregated so a supervisor can read the shop
in three seconds without opening a report.

The age of a stop is computed from the stage timestamp the operator entered
(stage_date + stage_time), falling back to the row's updated_at. Both are
nullable on older rows, so an age is always optional and the board says
"unknown" rather than inventing zero.
"""

from collections import defaultdict
from datetime import datetime, date

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.services import traceability_service as ts


# A pipe stopped longer than this is escalated on the board. Hours.
DEFAULT_ALERT_HOURS = 8

# States that mean the pipe is not moving.
STOPPED_STATES = ("fail", "blocked", "hold")


def _stage_timestamp(stage):
    """When this stage was decided, as best the data allows, or None."""
    if stage is None:
        return None
    if stage.stage_date and stage.stage_time:
        return datetime.combine(stage.stage_date, stage.stage_time)
    if stage.stage_date:
        return datetime.combine(stage.stage_date, datetime.min.time())
    return stage.updated_at or stage.created_at


def _age_hours(when, now=None):
    if when is None:
        return None
    now = now or datetime.utcnow()
    return max(0.0, (now - when).total_seconds() / 3600.0)


def wip_by_stage(pipes_by_id, stages_by_pipe):
    """Pipes sitting at each stage — the last stage each one has decided.

    A pipe that has decided nothing counts at the first stage: it is on the
    floor, waiting to start, and hiding it would understate the queue.
    """
    order = ProductionStage.active_names()
    counts = {name: 0 for name in order}
    if not order:
        return []
    for pipe_id in pipes_by_id:
        rows = stages_by_pipe.get(pipe_id, {})
        current = order[0]
        for name in reversed(order):
            st = rows.get(name)
            if st is not None and (st.decision or "").strip():
                current = name
                break
        counts[current] = counts.get(current, 0) + 1
    return [{"stage": name, "count": counts.get(name, 0)} for name in order]


def stopped_pipes(pipes, stages_by_pipe, alert_hours=DEFAULT_ALERT_HOURS,
                  now=None):
    """Every pipe that is not moving, worst and oldest first.

    A pipe is stopped when its own lab/final decision is a non-conformance, or
    when any of its stages carries one. The reason shown is the most severe
    thing found, with the stage that caused it.
    """
    now = now or datetime.utcnow()
    out = []
    for pipe in pipes:
        state = ts.effective_pipe_state(pipe)
        source = "lab"
        label = pipe.lab_decision or pipe.final_decision_value
        when = None

        # A stage-level stop outranks a clean lab decision, and a stage that
        # merely agrees with the lab still dates the stop — the lab decision
        # carries no timestamp of its own, so without this the age would read
        # "unknown" for every pipe held at a stage.
        for name, st in (stages_by_pipe.get(pipe.id) or {}).items():
            sstate = ts.state_of(st.decision)
            if sstate not in STOPPED_STATES:
                continue
            if state not in STOPPED_STATES or _worse(sstate, state):
                state, source, label = sstate, name, st.decision
                when = _stage_timestamp(st)
            elif when is None and sstate == state:
                when = _stage_timestamp(st)
                if when is not None:
                    source, label = name, st.decision

        if state not in STOPPED_STATES:
            continue

        age = _age_hours(when, now)
        out.append({
            "pipe_id": pipe.id,
            "code": pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
            "ladle_id": pipe.ladle_id,
            "state": state,
            "source": source,
            "decision": label,
            "age_hours": age,
            "overdue": age is not None and age >= alert_hours,
        })

    rank = {"fail": 0, "blocked": 1, "hold": 2}
    out.sort(key=lambda r: (rank.get(r["state"], 9),
                            -(r["age_hours"] or 0)))
    return out


def _worse(a, b):
    rank = {"fail": 0, "blocked": 1, "hold": 2}
    return rank.get(a, 9) < rank.get(b, 9)


def yield_today(day=None):
    """Today's output: produced, accepted, rejected, held, and yield %.

    Counted on production_date, which is the field the floor fills — not
    created_at, which moves when a record is edited.
    """
    day = day or date.today()
    pipes = Pipe.query.filter(Pipe.production_date == day).all()
    tally = {"produced": len(pipes), "pass": 0, "fail": 0,
             "hold": 0, "blocked": 0, "waiting": 0, "none": 0}
    for p in pipes:
        tally[ts.effective_pipe_state(p)] += 1
    decided = tally["pass"] + tally["fail"] + tally["blocked"]
    tally["yield_pct"] = round(tally["pass"] / decided * 100, 1) if decided else None
    tally["day"] = day
    return tally


def board(alert_hours=DEFAULT_ALERT_HOURS, now=None):
    """Everything the wall board shows, in one pass over the open pipes."""
    # Pipes still in play: nothing final recorded, or stopped short of it.
    pipes = (Pipe.query
             .filter(db.or_(Pipe.final_decision_value.is_(None),
                            Pipe.final_decision_value != "ACCEPT"))
             .all())
    pipes_by_id = {p.id: p for p in pipes}

    stages_by_pipe = defaultdict(dict)
    if pipes_by_id:
        rows = (PipeStage.query
                .filter(PipeStage.pipe_id.in_(list(pipes_by_id)))
                .all())
        for st in rows:
            stages_by_pipe[st.pipe_id][st.stage_name] = st

    stopped = stopped_pipes(pipes, stages_by_pipe, alert_hours, now)
    return {
        "wip": wip_by_stage(pipes_by_id, stages_by_pipe),
        "stopped": stopped,
        "overdue": [r for r in stopped if r["overdue"]],
        "today": yield_today(),
        "alert_hours": alert_hours,
        "open_count": len(pipes),
        "generated_at": now or datetime.utcnow(),
    }
