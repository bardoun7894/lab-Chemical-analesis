"""Panel data for the CCM BI hub.

Everything here is derived from ONE pass over the crosstab fact table
(pipe x stage), so adding a panel costs no extra query. Panels that already
have a dedicated report (SPC, YTD, diagnosis, data quality, saving matrix)
are not duplicated — the hub links to those.
"""

from collections import OrderedDict, defaultdict

from app.models.stage import ProductionStage
from app.services import crosstab_service

UNKNOWN = crosstab_service.UNKNOWN

# Stage panels the client names explicitly. code -> (english, arabic)
STAGE_PANELS = OrderedDict([
    ("hydrotest", ("Hydrotest", "الاختبار الهيدروليكي")),
    ("annealing", ("Heat treatment", "المعالجة الحرارية")),
    ("coating",   ("Coating", "الطلاء")),
    ("lab",       ("Lab approval", "اعتماد المعمل")),
    ("ccm",       ("Casting (CCM)", "الصب المستمر")),
    ("zinc",      ("Zinc", "الزنك")),
    ("cement",    ("Cement", "الأسمنت")),
    ("cutting",   ("Cutting", "القطع")),
    ("finish",    ("Finish", "التشطيب")),
    ("delivery",  ("Delivery", "التسليم")),
])


def _blank():
    return {"count": 0, "acc": 0, "rej": 0, "pend": 0, "weight": 0.0}


def _add(acc, fact):
    acc["count"] += 1
    acc["weight"] += fact["_weight"]
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
    # Denominator is DECIDED rows only; no decisions yet -> None, never 0%.
    acc["rej_pct"] = round(acc["rej"] * 100.0 / decided, 1) if decided else None
    acc["acc_pct"] = round(acc["acc"] * 100.0 / decided, 1) if decided else None
    acc["weight"] = round(acc["weight"], 1)
    acc["weight_mt"] = round(acc["weight"] / 1000.0, 3)
    return acc


def _group(facts, dim, limit=None, sort_by="count"):
    agg = defaultdict(_blank)
    for f in facts:
        _add(agg[f.get(dim, UNKNOWN)], f)
    rows = [dict(_finish(dict(v)), key=k) for k, v in agg.items()]
    if sort_by == "rej":
        rows.sort(key=lambda r: (-r["rej"], -r["count"]))
    elif sort_by == "key":
        rows.sort(key=lambda r: r["key"])
    else:
        rows.sort(key=lambda r: -r["count"])
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def stage_matrix(facts):
    """One row per stage, in canonical production order, with the stages that
    carry no rows still listed so a silent gap is visible rather than absent."""
    order = ProductionStage.active_names()
    rank = {name: i for i, name in enumerate(order)}
    rows = _group(facts, "stage")
    rows = [r for r in rows if r["key"] != UNKNOWN]
    rows.sort(key=lambda r: (rank.get(r["key"], len(order)), r["key"]))
    seen = {r["key"] for r in rows}
    for name in order:
        if name not in seen:
            rows.append(dict(_finish(_blank()), key=name))
    rows.sort(key=lambda r: (rank.get(r["key"], len(order)), r["key"]))
    return rows


def stage_panel(facts, stage_name, locale="en"):
    """Detail for one stage: totals plus its breakdown by DN, machine, shift,
    supervisor, month and reject reason."""
    sub = [f for f in facts if f["stage"] == stage_name]
    return {
        "stage": stage_name,
        "total": _finish(_stage_total(sub)),
        "by_dn": _group(sub, "dn"),
        "by_machine": _group(sub, "machine"),
        "by_shift": _group(sub, "shift", sort_by="key"),
        "by_supervisor": _group(sub, "supervisor", limit=15),
        "by_month": _group(sub, "month", sort_by="key"),
        "by_reason": [r for r in _group(sub, "reason", limit=15, sort_by="rej")
                      if r["key"] != UNKNOWN and r["rej"]],
        "has_data": bool(sub),
    }


def _stage_total(sub):
    acc = _blank()
    for f in sub:
        _add(acc, f)
    return acc


def defect_panels(facts):
    """Reject breakdowns — the reference's "Defects" panel, every cut."""
    return {
        "by_month": _group(facts, "month", sort_by="key"),
        "by_dn": _group(facts, "dn"),
        "by_supervisor": _group(facts, "supervisor", limit=15, sort_by="rej"),
        "by_machine": _group(facts, "machine", sort_by="rej"),
        "by_mold": [r for r in _group(facts, "mold", limit=20, sort_by="rej")
                    if r["key"] != UNKNOWN],
        "by_class": _group(facts, "pipe_class"),
        "by_shift": _group(facts, "shift", sort_by="key"),
    }


def reason_panels(facts):
    """Pareto of reject reasons plus reasons x DN, both count and weight."""
    rejects = [f for f in facts if f["_class"] == "reject"]
    agg = defaultdict(lambda: {"count": 0, "weight": 0.0})
    for f in rejects:
        key = f["reason"] if f["reason"] != UNKNOWN else "Unspecified"
        agg[key]["count"] += 1
        agg[key]["weight"] += f["_weight"]

    ordered = sorted(agg.items(), key=lambda kv: -kv[1]["count"])
    total = sum(v["count"] for v in agg.values())
    pareto, running = [], 0
    for name, v in ordered:
        running += v["count"]
        pareto.append({
            "reason": name,
            "count": v["count"],
            "weight": round(v["weight"], 1),
            "pct": round(v["count"] * 100.0 / total, 1) if total else 0.0,
            "cum_pct": round(running * 100.0 / total, 1) if total else 0.0,
        })

    # Reasons x DN, limited to the reasons that actually drive the Pareto.
    top = [p["reason"] for p in pareto[:8]]
    dn_labels = sorted({f["dn"] for f in rejects})
    matrix = {r: {dn: 0 for dn in dn_labels} for r in top}
    for f in rejects:
        key = f["reason"] if f["reason"] != UNKNOWN else "Unspecified"
        if key in matrix:
            matrix[key][f["dn"]] += 1

    return {
        "pareto": pareto[:20],
        "total_rejects": total,
        "dn_labels": dn_labels,
        "matrix": [{"reason": r, "cells": [matrix[r][dn] for dn in dn_labels],
                    "total": sum(matrix[r].values())} for r in top],
    }


def trend_panels(facts):
    """Count, weight and reject % over day / week / month."""
    out = {}
    for dim in ("day", "week", "month"):
        rows = [r for r in _group(facts, dim, sort_by="key") if r["key"] != UNKNOWN]
        out[dim] = rows
    return out


def analytics_panels(facts):
    """Cross-cuts the single-dimension panels cannot show: DN x class and
    machine x shift reject rates, plus best/worst performers."""
    def _matrix(row_dim, col_dim):
        cols = sorted({f[col_dim] for f in facts if f[col_dim] != UNKNOWN})
        agg = defaultdict(_blank)
        for f in facts:
            if f[col_dim] == UNKNOWN:
                continue
            _add(agg[(f[row_dim], f[col_dim])], f)
        rows = sorted({f[row_dim] for f in facts if f[row_dim] != UNKNOWN})
        return {
            "cols": cols,
            "rows": [
                {"key": r,
                 "cells": [_finish(dict(agg.get((r, c)) or _blank())) for c in cols]}
                for r in rows
            ],
        }

    sup = [r for r in _group(facts, "supervisor") if r["key"] != UNKNOWN and r["decided"] >= 5]
    sup_ranked = sorted(sup, key=lambda r: r["rej_pct"])
    mach = [r for r in _group(facts, "machine") if r["key"] != UNKNOWN and r["decided"] >= 5]

    return {
        "dn_by_class": _matrix("dn", "pipe_class"),
        "machine_by_shift": _matrix("machine", "shift"),
        "best_supervisors": sup_ranked[:5],
        "worst_supervisors": list(reversed(sup_ranked))[:5],
        "machines": sorted(mach, key=lambda r: -(r["rej_pct"] or 0)),
    }


def build(filters, locale="en"):
    """Everything the hub needs, from one fact-table pass."""
    facts = crosstab_service.build_facts(filters)
    stages = stage_matrix(facts)
    stage_lookup = {}
    for code, _labels in STAGE_PANELS.items():
        name = ProductionStage.name_for_code(code)
        if name:
            stage_lookup[code] = stage_panel(facts, name, locale)
    return {
        "facts": facts,
        "fact_count": len(facts),
        "stage_matrix": stages,
        "stage_panels": stage_lookup,
        "stage_panel_labels": STAGE_PANELS,
        "defects": defect_panels(facts),
        "reasons": reason_panels(facts),
        "trends": trend_panels(facts),
        "analytics": analytics_panels(facts),
    }
