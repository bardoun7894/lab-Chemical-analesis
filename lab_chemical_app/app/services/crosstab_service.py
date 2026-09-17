"""Crosstab engine — the reusable "Pivot By / Result" report.

One fact table (pipe x stage), any set of dimensions as nested column groups
("Pivot By"), any set of dimensions as row keys, and Acc / Rej / % measures in
every cell. Server-side so the browser never has to hold the dataset.

Shape of the fact rows is deliberately flat: every dimension is one key in the
fact dict, so adding a dimension is one entry in DIMENSIONS plus one key here.
"""

from collections import OrderedDict, defaultdict

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.user import User
from app.services import analytics_service
from app.services import shift_service


# ---------------------------------------------------------------------------
# Dimensions — the pickable axes. key -> (english label, arabic label)
# ---------------------------------------------------------------------------

DIMENSIONS = OrderedDict([
    ("stage",       ("Stage",            "المرحلة")),
    # "stage" is every stage the pipe passed through; "current_stage" is the one
    # it is standing at right now. Pivoting on the first answers "how much work
    # went through here", on the second "how much is sitting here".
    ("current_stage", ("Current stage",  "المرحلة الحالية")),
    ("dn",          ("Diameter DN",      "القطر")),
    ("pipe_class",  ("Class",            "النوع")),
    ("machine",     ("Machine",          "الماكينة")),
    ("mold",        ("Mold",             "القالب")),
    ("shift",       ("Shift (casting)",  "الوردية (الصب)")),
    # The shift the stage decision was recorded in, which is a different
    # question from the shift the pipe was cast in.
    ("stage_shift", ("Shift (decision)", "الوردية (القرار)")),
    ("supervisor",  ("Shift engineer",   "مهندس الوردية")),
    ("approver",    ("Approved by",      "المعتمد")),
    ("month",       ("Month",            "الشهر")),
    ("week",        ("Week",             "الأسبوع")),
    ("day",         ("Day",              "اليوم")),
    ("customer",    ("Customer",         "العميل")),
    ("order",       ("Production order", "أمر الإنتاج")),
    ("ladle",       ("Ladle",            "البوتقة")),
    ("reason",      ("Reject reason",    "سبب الرفض")),
    ("defect_type", ("Defect type",      "نوع العيب")),
    ("lab",         ("Lab decision",     "قرار المعمل")),
    ("decision",    ("Stage decision",   "قرار المرحلة")),
])

UNKNOWN = "—"

# Selectable cell measures. `fmt` tells the template how to print the value:
# int, pct (one decimal + %), pct2 (two decimals + %), kg and m (one decimal).
# A measure whose value is None renders as an em dash — that is the difference
# between "no saving" and "saving cannot be computed here".
MEASURES = OrderedDict([
    ("acc",        {"label": ("Accepted", "مقبول"),        "fmt": "int"}),
    ("rej",        {"label": ("Rejected", "مرفوض"),        "fmt": "int"}),
    ("rej_pct",    {"label": ("Reject %", "نسبة الرفض %"), "fmt": "pct"}),
    # Distinct pipes and their total length. Both are de-duplicated per pipe by
    # _add, so a pipe with eight stage rows still counts once and contributes
    # its length once — the answer to "how many pipes, how many metres".
    ("pipes",      {"label": ("Pipes", "عدد المواسير"),    "fmt": "int"}),
    ("length",     {"label": ("Length (m)", "الطول (م)"),  "fmt": "m"}),
    ("planned",    {"label": ("Standard weight (kg)", "الوزن المعياري (كجم)"),
                    "fmt": "kg"}),
    ("weight",     {"label": ("Actual weight (kg)", "الوزن الفعلي (كجم)"),
                    "fmt": "kg"}),
    ("saving_pct", {"label": ("Saving %", "التوفير %"),    "fmt": "pct2"}),
])

DEFAULT_MEASURES = ["acc", "rej", "rej_pct"]

# Measures that only mean anything when a pipe carried both a standard and an
# actual weight — the UI discloses coverage whenever one of these is shown.
WEIGHT_MEASURES = {"planned", "weight", "saving_pct"}

# Ready-made combinations, so the common reports are one click.
PRESETS = OrderedDict([
    ("stage_by_dn",      {"label": ("Stage x DN + Class", "المرحلة × القطر والنوع"),
                          "pivot": ["stage"], "rows": ["dn", "pipe_class"]}),
    ("wip_by_dn",        {"label": ("Current stage x DN + Class",
                                    "المرحلة الحالية × القطر والنوع"),
                          "pivot": ["current_stage"], "rows": ["dn", "pipe_class"]}),
    ("stage_by_machine", {"label": ("Stage x Machine", "المرحلة × الماكينة"),
                          "pivot": ["stage"], "rows": ["machine"]}),
    ("shift_by_stage",   {"label": ("Shift x Stage", "الوردية × المرحلة"),
                          "pivot": ["shift", "stage"], "rows": ["dn"]}),
    ("month_by_dn",      {"label": ("Month x DN", "الشهر × القطر"),
                          "pivot": ["month"], "rows": ["dn"]}),
    ("reason_by_stage",  {"label": ("Stage x Reject reason", "المرحلة × سبب الرفض"),
                          "pivot": ["stage"], "rows": ["reason"]}),
    ("supervisor_stage", {"label": ("Shift engineer x Stage", "مهندس الوردية × المرحلة"),
                          "pivot": ["stage"], "rows": ["supervisor"]}),
])

SORT_MODES = OrderedDict([
    ("total_desc",  ("Total, descending",    "الإجمالي (تنازلي)")),
    ("rejpct_desc", ("Reject %, descending", "نسبة الرفض (تنازلي)")),
    ("label_asc",   ("Label, alphabetical",  "أبجدي")),
])


def dimension_choices(locale="en"):
    idx = 1 if locale == "ar" else 0
    return [{"key": k, "label": v[idx]} for k, v in DIMENSIONS.items()]


def preset_choices(locale="en"):
    idx = 1 if locale == "ar" else 0
    return [
        {"key": k, "label": v["label"][idx], "pivot": v["pivot"], "rows": v["rows"]}
        for k, v in PRESETS.items()
    ]


def measure_choices(locale="en"):
    idx = 1 if locale == "ar" else 0
    return [(key, m["label"][idx]) for key, m in MEASURES.items()]


def sort_choices(locale="en"):
    idx = 1 if locale == "ar" else 0
    return [{"key": k, "label": v[idx]} for k, v in SORT_MODES.items()]


# ---------------------------------------------------------------------------
# Fact table
# ---------------------------------------------------------------------------

def _iso_week(d):
    year, week, _ = d.isocalendar()
    return "%d-W%02d" % (year, week)


def build_facts(filters):
    """One row per (pipe, stage). Pipes with no stage rows still appear once so
    production totals are never silently lost."""
    pipes = analytics_service.apply_pipe_filters(Pipe.query, filters).all()
    if not pipes:
        return []

    by_pipe = defaultdict(list)
    pipe_ids = [p.id for p in pipes]
    # Chunked IN() — SQLite caps host parameters at 999.
    for start in range(0, len(pipe_ids), 500):
        chunk = pipe_ids[start:start + 500]
        rows = db.session.query(PipeStage).filter(PipeStage.pipe_id.in_(chunk)).all()
        for s in rows:
            by_pipe[s.pipe_id].append(s)

    # Resolve approver names in one query rather than per stage row.
    approver_ids = {
        st.approved_by_id
        for rows in by_pipe.values() for st in rows
        if st.approved_by_id
    }
    approver_names = {}
    if approver_ids:
        for u in User.query.filter(User.id.in_(list(approver_ids))).all():
            approver_names[u.id] = (
                (u.full_name or u.full_name_ar or u.username or "").strip()
                or UNKNOWN
            )

    # Where each pipe is standing now: the last stage in the canonical order
    # that carries a decision. Same rule as Pipe.current_stage, computed from
    # the rows already in hand so it costs no extra query per pipe.
    from app.models.stage import ProductionStage

    active_names = ProductionStage.active_names()
    active_rank = {name: i for i, name in enumerate(active_names)}
    first_stage = active_names[0] if active_names else UNKNOWN

    def _current_stage(stage_rows):
        best, best_rank = None, -1
        for st in stage_rows:
            if not st.decision:
                continue
            rank = active_rank.get(st.stage_name)
            if rank is not None and rank > best_rank:
                best, best_rank = st.stage_name, rank
        return best or first_stage

    facts = []
    for p in pipes:
        prod_date = p.production_date
        order = p.production_order
        # Pipe.iso_weight is 0 for every production pipe, so the planned weight
        # comes from the product standard. A pipe with neither is excluded from
        # the saving ratio rather than counted as zero saving.
        _planned, _planned_source = analytics_service.planned_weight(p)
        base = {
            "stage": UNKNOWN,
            "current_stage": _current_stage(by_pipe.get(p.id) or []),
            "dn": "DN%s" % p.diameter if p.diameter else UNKNOWN,
            "pipe_class": p.pipe_class or UNKNOWN,
            "machine": analytics_service.pipe_machine_code(p) or UNKNOWN,
            "mold": p.mold_number or UNKNOWN,
            "shift": "Shift %s" % p.shift if p.shift else UNKNOWN,
            "stage_shift": UNKNOWN,
            "approver": UNKNOWN,
            "supervisor": (p.shift_engineer or "").strip() or UNKNOWN,
            "month": prod_date.strftime("%Y-%m") if prod_date else UNKNOWN,
            "week": _iso_week(prod_date) if prod_date else UNKNOWN,
            "day": prod_date.isoformat() if prod_date else UNKNOWN,
            "customer": ((order.customer_name or "").strip() if order else "") or UNKNOWN,
            "order": ((order.order_number or "") if order else "") or UNKNOWN,
            "ladle": p.ladle_id or UNKNOWN,
            "lab": (p.lab_decision or "WAITING").upper(),
            "reason": UNKNOWN,
            "defect_type": UNKNOWN,
            "decision": UNKNOWN,
            # The pipe id rides along so weight can be counted once per pipe
            # even though this row is duplicated across the pipe's stages.
            "_pipe_id": p.id,
            "_weight": float(p.actual_weight or 0),
            "_planned": _planned,
            "_weighed": bool(_planned_source and p.actual_weight),
            "_length": analytics_service.pipe_metric_value(p, "length"),
            "_class": "pending",
        }
        stages = by_pipe.get(p.id) or []
        if not stages:
            facts.append(base)
            continue

        for s in stages:
            row = dict(base)
            row["stage"] = s.stage_name or UNKNOWN
            row["decision"] = (s.decision or "").strip() or UNKNOWN
            row["_class"] = PipeStage.classify_decision(s.decision)
            if s.machine_id and s.machine:
                row["machine"] = s.machine.machine_code or row["machine"]
            if s.shift_responsible:
                row["supervisor"] = s.shift_responsible.strip()
            sh = shift_service.stage_shift(s)
            if sh:
                row["stage_shift"] = "Shift %s" % sh
            if s.approved_by_id:
                row["approver"] = approver_names.get(s.approved_by_id, UNKNOWN)
            if s.has_defect or row["_class"] == "reject":
                row["reason"] = (s.defect_reason or s.reason or "").strip() or UNKNOWN
                row["defect_type"] = (s.defect_type or "").strip() or UNKNOWN
            facts.append(row)

    return facts


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _blank():
    return {"count": 0, "acc": 0, "rej": 0, "pend": 0,
            "weight": 0.0, "planned": 0.0, "length": 0.0,
            "weighed": 0, "pipes": 0,
            # Pipe ids already counted in this cell. build_facts emits one row
            # per (pipe, stage), so without this a pipe with eight stages would
            # contribute its weight eight times.
            "_seen": set()}


def _add(acc, fact):
    acc["count"] += 1
    pipe_id = fact.get("_pipe_id")
    if pipe_id is None or pipe_id not in acc["_seen"]:
        if pipe_id is not None:
            acc["_seen"].add(pipe_id)
        acc["pipes"] += 1
        acc["weight"] += fact["_weight"]
        acc["planned"] += fact["_planned"]
        acc["length"] += fact["_length"]
        if fact.get("_weighed"):
            acc["weighed"] += 1
    cls = fact["_class"]
    if cls == "accept":
        acc["acc"] += 1
    elif cls == "reject":
        acc["rej"] += 1
    else:
        acc["pend"] += 1


def _finish(acc):
    decided = acc["acc"] + acc["rej"]
    acc["decided"] = decided
    acc["rej_pct"] = round(acc["rej"] * 100.0 / decided, 1) if decided else 0.0
    acc["acc_pct"] = round(acc["acc"] * 100.0 / decided, 1) if decided else 0.0
    acc["weight"] = round(acc["weight"], 1)
    acc["planned"] = round(acc["planned"], 1)
    acc["length"] = round(acc["length"], 1)
    # Saving only means anything over pipes that had a planned weight to
    # compare against; None (not 0) says "not measurable here".
    acc["saving_kg"] = round(acc["planned"] - acc["weight"], 1) if acc["planned"] else None
    acc["saving_pct"] = (
        round((acc["planned"] - acc["weight"]) * 100.0 / acc["planned"], 2)
        if acc["planned"] else None
    )
    # The id set is a working detail, and is not JSON-serialisable for the
    # dashboard payload.
    acc.pop("_seen", None)
    return acc


def _reject_pct(acc):
    decided = acc["acc"] + acc["rej"]
    return acc["rej"] * 100.0 / decided if decided else 0.0


def _header_levels(leaf_keys, depth):
    """Turn ordered leaf tuples into one header row per pivot dimension, with
    colspans, so nested pivot dimensions render as merged group headers."""
    levels = []
    for lvl in range(depth):
        row = []
        for leaf in leaf_keys:
            label = leaf[lvl]
            prefix = leaf[:lvl]
            if row and row[-1]["label"] == label and row[-1]["prefix"] == prefix:
                row[-1]["span"] += 1
            else:
                row.append({"label": label, "span": 1, "prefix": prefix})
        levels.append(row)
    return levels


def crosstab(filters, pivot_dims, row_dims, sort="total_desc", row_limit=20,
             display="full", facts=None, measures=None):
    """Build the crosstab. Returns everything the template needs, already sorted
    and truncated, plus the truncation counts so the UI never lies about scope.

    ``measures`` picks which values each cell shows; unknown keys are dropped
    and an empty selection falls back to the accept/reject/reject-% default
    rather than rendering blank cells.
    """
    measures = [m for m in (measures or []) if m in MEASURES] or list(DEFAULT_MEASURES)
    pivot_dims = [d for d in (pivot_dims or []) if d in DIMENSIONS]
    # A dimension used as a column cannot also be a row — it would be constant.
    row_dims = [d for d in (row_dims or []) if d in DIMENSIONS and d not in pivot_dims]
    if not row_dims:
        row_dims = ["dn"] if "dn" not in pivot_dims else ["stage"]

    facts = build_facts(filters) if facts is None else facts

    grid = defaultdict(lambda: defaultdict(_blank))   # row_key -> leaf_key -> acc
    row_tot = defaultdict(_blank)
    col_tot = defaultdict(_blank)
    grand = _blank()
    leaf_order = OrderedDict()
    row_order = OrderedDict()

    for f in facts:
        r_key = tuple(f.get(d, UNKNOWN) for d in row_dims)
        c_key = tuple(f.get(d, UNKNOWN) for d in pivot_dims) if pivot_dims else ()
        row_order.setdefault(r_key, None)
        leaf_order.setdefault(c_key, None)
        _add(grid[r_key][c_key], f)
        _add(row_tot[r_key], f)
        _add(col_tot[c_key], f)
        _add(grand, f)

    leaf_keys = sorted(leaf_order.keys())
    all_rows = list(row_order.keys())

    if sort == "label_asc":
        all_rows.sort()
    elif sort == "rejpct_desc":
        all_rows.sort(key=lambda r: (-_reject_pct(row_tot[r]), -row_tot[r]["count"]))
    else:
        all_rows.sort(key=lambda r: (-row_tot[r]["count"], r))

    total_rows = len(all_rows)
    shown = all_rows if not row_limit else all_rows[:row_limit]

    rows = []
    for r in shown:
        rows.append({
            "labels": list(r),
            "cells": [_finish(dict(grid[r].get(c) or _blank())) for c in leaf_keys],
            "total": _finish(dict(row_tot[r])),
        })

    # Statistics describe the cells actually on screen — the truncated rows and
    # the selected measures — so the numbers move with the selection instead of
    # describing a dataset the reader cannot see.
    stats = []
    for m in measures:
        values = [c[m] for r in rows for c in r["cells"] if c.get(m) is not None]
        if not values:
            continue
        summary = analytics_service.stat_summary(values)
        stats.append({
            "measure": m,
            "label": MEASURES[m]["label"],
            "fmt": MEASURES[m]["fmt"],
            "n": summary["count"],
            "avg": summary["avg"],
            "min": summary["min"],
            "max": summary["max"],
            "std": summary["std"],
            # A total only means something for counts and weights; averaging or
            # summing percentages across cells of different sizes would be a
            # lie, so it is withheld rather than printed.
            "total": summary["total"] if MEASURES[m]["fmt"] in ("int", "kg", "m") else None,
        })

    return {
        "pivot_dims": pivot_dims,
        "row_dims": row_dims,
        "pivot_labels": [DIMENSIONS[d][0] for d in pivot_dims],
        "row_labels": [DIMENSIONS[d][0] for d in row_dims],
        "pivot_labels_ar": [DIMENSIONS[d][1] for d in pivot_dims],
        "row_labels_ar": [DIMENSIONS[d][1] for d in row_dims],
        "header_levels": _header_levels(leaf_keys, len(pivot_dims)),
        "leaf_keys": [list(k) for k in leaf_keys],
        "rows": rows,
        "col_totals": [_finish(dict(col_tot.get(c) or _blank())) for c in leaf_keys],
        "grand_total": _finish(dict(grand)),
        "measures": measures,
        "stats": stats,
        "measure_meta": {k: MEASURES[k] for k in measures},
        "shows_weight": any(m in WEIGHT_MEASURES for m in measures),
        "display": display if display in ("full", "compact") else "full",
        "sort": sort,
        "row_limit": row_limit,
        "total_rows": total_rows,
        "shown_rows": len(rows),
        "hidden_rows": total_rows - len(rows),
        "fact_count": len(facts),
    }
