"""SPC service — control charts and run-rule detection over lab data.

Read-only analytics: series are computed per request from ACTIVE
MechanicalTest rows and ChemicalAnalysis rows; nothing is persisted.

Math conventions
----------------
- I-MR: CL = mean, sigma = MR̄ / 1.128 (d2 for n=2), UCL/LCL = mean ± 3σ
  (= mean ± 2.66·MR̄).
- X̄-R: standard A2/D3/D4 factor table for subgroup sizes 2..10.
- P chart: per-point limits from p̄ and the subgroup size; LCL floored at 0.
- C chart: c̄ ± 3√c̄; LCL floored at 0.
- Nelson rules 1, 2, 3, 5, 6, 7, 8 (rule 4 has no Western Electric analogue
  and is omitted per spec FR-003).
"""
import math
from statistics import mean

from flask import url_for

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.services import analytics_service


# d2 for a moving range of two consecutive points.
D2_IMR = 1.128

# A2, D3, D4 by subgroup size.
XBAR_R_CONSTANTS = {
    2: (1.880, 0.0, 3.267),
    3: (1.023, 0.0, 2.574),
    4: (0.729, 0.0, 2.282),
    5: (0.577, 0.0, 2.114),
    6: (0.483, 0.0, 2.004),
    7: (0.419, 0.076, 1.924),
    8: (0.373, 0.136, 1.864),
    9: (0.337, 0.184, 1.816),
    10: (0.308, 0.223, 1.777),
}

# Nelson run lengths (rule 2 = 9 same side, per FR-003).
RUN_SAME_SIDE = 9
RUN_TREND = 6
RUN_WITHIN_1SIGMA = 15
RUN_BEYOND_1SIGMA = 8

MECHANICAL_CHARACTERISTICS = {
    "tensile_mpa": ("Tensile (MPa)", "tensile_mpa"),
    "tensile_strength": ("Tensile (KgF/mm²)", "tensile_strength"),
    "elongation": ("Elongation (%)", "elongation"),
    "hardness": ("Hardness (HB)", "hardness"),
    "nodularity": ("Nodularity (%Nd)", "nodularity_percent"),
    "microstructure_70": ("Microstructure % (>70)", "percent_70"),
    "microstructure_40": ("Microstructure % (>40)", "percent_40"),
    # Both carry an acceptance criterion in mechanical_rules.json (NC >= 400,
    # Carbides < 1) but had no characteristic, so neither could be charted or
    # given a capability index.
    "nodule_count": ("Nodule count (NC)", "nodule_count"),
    "carbides": ("Carbides (%)", "carbides"),
}

CHEMICAL_CHARACTERISTICS = {
    "carbon": ("Carbon (C %)", "carbon"),
    "silicon": ("Silicon (Si %)", "silicon"),
    "magnesium": ("Magnesium (Mg %)", "magnesium"),
    "copper": ("Copper (Cu %)", "copper"),
    "chromium": ("Chromium (Cr %)", "chromium"),
    "sulfur": ("Sulfur (S %)", "sulfur"),
    "manganese": ("Manganese (Mn %)", "manganese"),
    "phosphorus": ("Phosphorus (P %)", "phosphorus"),
    "lead": ("Lead (Pb %)", "lead"),
    "aluminum": ("Aluminum (Al %)", "aluminum"),
    "carbon_equivalent": ("Carbon Equivalent (CE)", "carbon_equivalent"),
    "manganese_equivalent": ("Manganese Equivalent (MnE)", "manganese_equivalent"),
    "magnesium_equivalent": ("Magnesium Equivalent (MgE)", "magnesium_equivalent"),
}

# Dimensional characteristics read the stage JSON profiles rather than a
# column. The second tuple item is the extractor key, resolved in
# _dimensional_points; "sym:<symbol>" pulls one TA 1012 symbol reading.
DIMENSIONAL_CHARACTERISTICS = {
    "wall_thickness": ("Wall thickness (mm)", "wall_thickness"),
    "outer_diameter": ("Outer diameter (mm)", "outer_diameter"),
    "cement_thickness": ("Cement lining (mm)", "cement"),
    "coating_thickness": ("Coating (\u00b5m)", "coating"),
}


def _register_symbol_characteristics():
    """One characteristic per TA 1012 symbol, e.g. dim_d1 -> "Dimension d1".

    Built from the standards file so adding a symbol there makes it chartable
    without touching this module.
    """
    out = {}
    try:
        from app.services import dimension_standard_service
        for symbol in dimension_standard_service.symbol_keys():
            label = dimension_standard_service.symbol_label(symbol)
            out[f"dim_{symbol}"] = (f"Dimension {label}", f"sym:{symbol}")
    except Exception:  # noqa: BLE001 - a broken config must not break SPC
        return {}
    return out


DIMENSIONAL_CHARACTERISTICS.update(_register_symbol_characteristics())

CHARACTERISTICS = {
    **MECHANICAL_CHARACTERISTICS,
    **CHEMICAL_CHARACTERISTICS,
    **DIMENSIONAL_CHARACTERISTICS,
}

# Hard cap so a wide filter window can't return an unbounded series. The UI
# shows a truncation note when hit — never a silent cap (plan risk notes).
DEFAULT_SERIES_CAP = 2000


def characteristic_label(key):
    return CHARACTERISTICS[key][0]


def is_mechanical(key):
    return key in MECHANICAL_CHARACTERISTICS


def is_dimensional(key):
    return key in DIMENSIONAL_CHARACTERISTICS


# ---------------------------------------------------------------------------
# Series building
# ---------------------------------------------------------------------------

def build_series(characteristic, filters, cap=DEFAULT_SERIES_CAP):
    """Build an ordered series of (date, value, subgroup, source) points.

    Mechanical characteristics read ACTIVE tests only (SUPERSEDED excluded);
    chemical characteristics read one point per analysis. ``subgroup`` is the
    ladle id (X̄-R grouping per spec US1). Raises ValueError on an unknown
    characteristic.
    """
    if characteristic not in CHARACTERISTICS:
        raise ValueError(f"Unknown characteristic: {characteristic}")

    if is_mechanical(characteristic):
        points = _mechanical_points(characteristic, filters)
    elif is_dimensional(characteristic):
        points = _dimensional_points(characteristic, filters)
    else:
        points = _chemical_points(characteristic, filters)

    points.sort(key=lambda p: (p["date"], p["source_id"]))
    total = len(points)
    truncated = total > cap
    return {
        "characteristic": characteristic,
        "label": characteristic_label(characteristic),
        "points": points[:cap],
        "total": total,
        "truncated": truncated,
        "cap": cap,
    }


def _safe_url(endpoint, **kwargs):
    """url_for that degrades to None outside a request context (CLI/tests)."""
    try:
        return url_for(endpoint, **kwargs)
    except RuntimeError:
        return None


def _mechanical_points(characteristic, filters):
    field = MECHANICAL_CHARACTERISTICS[characteristic][1]
    query = MechanicalTest.query.filter(MechanicalTest.status == "ACTIVE")
    if filters.get("date_from"):
        query = query.filter(MechanicalTest.test_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(MechanicalTest.test_date <= filters["date_to"])
    if filters.get("diameter"):
        query = query.filter(MechanicalTest.diameter == filters["diameter"])
    if filters.get("shift"):
        query = query.filter(MechanicalTest.shift == filters["shift"])
    # Pipe-linked filters share one join (duplicate joins break SQLAlchemy).
    pipe_conditions = []
    if filters.get("production_order_id"):
        pipe_conditions.append(
            Pipe.production_order_id == filters["production_order_id"]
        )
    if filters.get("pipe_class"):
        # Only tests linked to a pipe can match a class filter.
        pipe_conditions.append(Pipe.pipe_class == filters["pipe_class"])
    if pipe_conditions:
        query = query.join(Pipe, MechanicalTest.pipe_id == Pipe.id).filter(
            *pipe_conditions
        )

    points = []
    for t in query.all():
        value = getattr(t, field)
        if value is None:
            continue
        points.append({
            "date": t.test_date,
            "value": float(value),
            "subgroup": t.ladle_id or t.pipe_code or f"test-{t.id}",
            "source_id": t.id,
            "source_url": _safe_url("mechanical.detail", id=t.id),
        })
    return points


def _chemical_points(characteristic, filters):
    field = CHEMICAL_CHARACTERISTICS[characteristic][1]
    query = ChemicalAnalysis.query
    if filters.get("date_from"):
        query = query.filter(ChemicalAnalysis.test_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(ChemicalAnalysis.test_date <= filters["date_to"])
    if filters.get("production_order_id"):
        query = query.filter(
            ChemicalAnalysis.production_order_id == filters["production_order_id"]
        )
    if filters.get("diameter"):
        # Chemistry carries no DN; resolve through pipes poured from the ladle.
        query = query.filter(
            ChemicalAnalysis.pipes.any(Pipe.diameter == filters["diameter"])
        )
    if filters.get("pipe_class"):
        query = query.filter(
            ChemicalAnalysis.pipes.any(Pipe.pipe_class == filters["pipe_class"])
        )

    points = []
    for a in query.all():
        value = getattr(a, field)
        if value is None:
            continue
        points.append({
            "date": a.test_date,
            "value": float(value),
            "subgroup": a.ladle_id or f"analysis-{a.id}",
            "source_id": a.id,
            "source_url": _safe_url("chemical.detail", id=a.id),
        })
    return points


def _dimensional_points(characteristic, filters):
    """Series points from the stage JSON measurement profiles.

    One point per reading, not per pipe: the operator takes 21 wall readings on
    a pipe and every one of them is an observation of the process. The pipe is
    the subgroup key, so X̄-R charts a pipe per subgroup.

    Wall thickness and diameter live on the CCM stage, the lining layers on
    Coating, and the TA 1012 symbols on either CCM or Annealing.
    """
    from app.services import measurement_stats_service

    key = DIMENSIONAL_CHARACTERISTICS[characteristic][1]

    query = analytics_service.apply_pipe_filters(Pipe.query, filters)
    pipes = {p.id: p for p in query.all()}
    if not pipes:
        return []

    stages = PipeStage.query.filter(PipeStage.pipe_id.in_(pipes.keys())).all()

    points = []
    for stage in stages:
        pipe = pipes.get(stage.pipe_id)
        if pipe is None:
            continue
        when = stage.stage_date or pipe.production_date
        if when is None:
            continue

        values = []
        if key == "wall_thickness":
            stats = measurement_stats_service.thickness_stats(
                stage.dimension_profile)
            values = stats["readings"] if stats else []
        elif key == "outer_diameter":
            stats = measurement_stats_service.diameter_stats(
                stage.dimension_profile)
            values = stats["readings"] if stats else []
        elif key in ("cement", "coating"):
            profile = stage.thickness_profile or {}
            values = [v for v in (profile.get(key) or []) if v is not None]
        elif key.startswith("sym:"):
            symbol = key[4:]
            symbols = (stage.dimension_profile or {}).get("symbols") or {}
            value = symbols.get(symbol)
            values = [value] if value is not None else []

        for value in values:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            points.append({
                "date": when,
                "value": value,
                # One subgroup per pipe: the repeated readings on a pipe are
                # the rational subgroup for X̄-R.
                "subgroup": pipe.pipe_code or pipe.no_code or f"pipe-{pipe.id}",
                "source_id": pipe.id,
                "source_url": _safe_url("stages.view", id=pipe.id),
            })
    return points


def subgroup_values(points, max_size=10):
    """Group series points into X̄-R subgroups by their subgroup key.

    Subgroups with fewer than 2 points are dropped (X̄-R needs a range);
    oversized subgroups are truncated to ``max_size`` and counted so the UI
    can disclose it.
    """
    groups = {}
    order = []
    for p in points:
        key = p["subgroup"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p["value"])

    subgroups, dropped_singletons, truncated = [], 0, 0
    for key in order:
        vals = groups[key]
        if len(vals) < 2:
            dropped_singletons += 1
            continue
        if len(vals) > max_size:
            vals = vals[:max_size]
            truncated += 1
        subgroups.append({"key": key, "values": vals})
    return {
        "subgroups": subgroups,
        "dropped_singletons": dropped_singletons,
        "truncated_subgroups": truncated,
    }


# ---------------------------------------------------------------------------
# Control limits
# ---------------------------------------------------------------------------

def imr_limits(values):
    """I-MR limits for a list of individual values. None when n < 2."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    cl = mean(vals)
    mrs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
    mr_bar = mean(mrs)
    sigma = mr_bar / D2_IMR
    if sigma == 0:
        return {"cl": cl, "ucl": cl, "lcl": cl, "mr_bar": 0.0,
                "sigma": 0.0, "no_variation": True}
    # E2 = 2.66 control-chart constant (spec: UCL/LCL = mean ± 2.66·MR̄);
    # sigma = MR̄/1.128 is kept separately for Nelson rule evaluation.
    return {
        "cl": cl,
        "ucl": cl + 2.66 * mr_bar,
        "lcl": cl - 2.66 * mr_bar,
        "mr_bar": mr_bar,
        "sigma": sigma,
        "no_variation": False,
    }


def xbar_r_limits(subgroups):
    """X̄-R limits from a list of subgroup value-lists (size 2..10).

    Returns None when no usable subgroup exists.
    """
    usable = [list(g) for g in subgroups if len(g) >= 2]
    if not usable:
        return None
    sizes = [len(g) for g in usable]
    n = min(min(sizes), 10)
    usable = [g[:n] for g in usable]
    a2, d3, d4 = XBAR_R_CONSTANTS[n]

    means = [mean(g) for g in usable]
    ranges = [max(g) - min(g) for g in usable]
    xbar_bar = mean(means)
    r_bar = mean(ranges)
    return {
        "cl": xbar_bar,
        "ucl": xbar_bar + a2 * r_bar,
        "lcl": xbar_bar - a2 * r_bar,
        "r_cl": r_bar,
        "r_ucl": d4 * r_bar,
        "r_lcl": d3 * r_bar,
        "r_bar": r_bar,
        "sigma": (a2 * r_bar) / 3 if a2 else 0.0,
        "subgroup_size": n,
        "subgroup_means": means,
        "subgroup_ranges": ranges,
        "no_variation": r_bar == 0,
        # Disclosed, never silent: unequal subgroup sizes are resized to the
        # smallest (standard A2/D3/D4 tables assume a fixed size).
        "resized": len(set(sizes)) > 1,
        "original_sizes": sizes,
    }


def p_chart_limits(proportions, sizes):
    """P-chart limits: p̄ plus per-point UCL/LCL from each subgroup size."""
    if not proportions or not sizes:
        return None
    defectives = sum(p * n for p, n in zip(proportions, sizes))
    total = sum(sizes)
    if total == 0:
        return None
    p_bar = defectives / total
    limits = []
    for n in sizes:
        if n <= 0:
            limits.append((None, None))
            continue
        spread = 3 * math.sqrt(p_bar * (1 - p_bar) / n)
        limits.append((p_bar + spread, max(0.0, p_bar - spread)))
    return {"cl": p_bar, "limits": limits}


def c_chart_limits(counts):
    """C-chart limits: c̄ ± 3√c̄, LCL floored at 0. None when empty."""
    if not counts:
        return None
    c_bar = mean(counts)
    spread = 3 * math.sqrt(c_bar)
    return {
        "cl": c_bar,
        "ucl": c_bar + spread,
        "lcl": max(0.0, c_bar - spread),
    }


# ---------------------------------------------------------------------------
# Nelson / Western Electric run rules
# ---------------------------------------------------------------------------

def nelson_violations(values, cl, sigma):
    """Detect Nelson rules 1,2,3,5,6,7,8. Returns [{rule, indices, description}].

    ``sigma <= 0`` (no-variation series) yields no violations rather than
    dividing by zero.
    """
    vals = [v for v in values if v is not None]
    if not vals or sigma <= 0:
        return []

    violations = []

    def add(rule, indices, description):
        violations.append({"rule": rule, "indices": indices,
                           "description": description})

    # Rule 1: any single point beyond 3σ.
    out3 = [i for i, v in enumerate(vals) if abs(v - cl) > 3 * sigma]
    if out3:
        add(1, out3, "Point beyond 3σ from center line")

    # Rule 2: RUN_SAME_SIDE consecutive points on the same side of CL.
    for i in range(len(vals) - RUN_SAME_SIDE + 1):
        window = vals[i:i + RUN_SAME_SIDE]
        if all(v > cl for v in window) or all(v < cl for v in window):
            add(2, list(range(i, i + RUN_SAME_SIDE)),
                f"{RUN_SAME_SIDE} consecutive points on one side of CL")
            break

    # Rule 3: RUN_TREND consecutive points steadily increasing or decreasing.
    for i in range(len(vals) - RUN_TREND + 1):
        window = vals[i:i + RUN_TREND]
        diffs = [window[j + 1] - window[j] for j in range(len(window) - 1)]
        if all(d > 0 for d in diffs) or all(d < 0 for d in diffs):
            add(3, list(range(i, i + RUN_TREND)),
                f"{RUN_TREND} consecutive points trending")
            break

    # Rule 5: 2 of 3 consecutive points beyond 2σ on the same side.
    for i in range(len(vals) - 2):
        window = vals[i:i + 3]
        for side in (1, -1):
            hits = [j for j, v in enumerate(window)
                    if side * (v - cl) > 2 * sigma]
            if len(hits) >= 2:
                add(5, [i + j for j in hits], "2 of 3 points beyond 2σ (same side)")
                break
        else:
            continue
        break

    # Rule 6: 4 of 5 consecutive points beyond 1σ on the same side.
    for i in range(len(vals) - 4):
        window = vals[i:i + 5]
        for side in (1, -1):
            hits = [j for j, v in enumerate(window)
                    if side * (v - cl) > sigma]
            if len(hits) >= 4:
                add(6, [i + j for j in hits], "4 of 5 points beyond 1σ (same side)")
                break
        else:
            continue
        break

    # Rule 7: RUN_WITHIN_1SIGMA consecutive points within 1σ of CL (either side).
    for i in range(len(vals) - RUN_WITHIN_1SIGMA + 1):
        window = vals[i:i + RUN_WITHIN_1SIGMA]
        if all(abs(v - cl) < sigma for v in window):
            add(7, list(range(i, i + RUN_WITHIN_1SIGMA)),
                f"{RUN_WITHIN_1SIGMA} consecutive points within 1σ (stratification)")
            break

    # Rule 8: RUN_BEYOND_1SIGMA consecutive points beyond 1σ on both sides.
    for i in range(len(vals) - RUN_BEYOND_1SIGMA + 1):
        window = vals[i:i + RUN_BEYOND_1SIGMA]
        if all(abs(v - cl) > sigma for v in window) and \
                any(v > cl for v in window) and any(v < cl for v in window):
            add(8, list(range(i, i + RUN_BEYOND_1SIGMA)),
                f"{RUN_BEYOND_1SIGMA} consecutive points beyond 1σ on both sides (mixture)")
            break

    return violations


# ---------------------------------------------------------------------------
# Attribute chart data
# ---------------------------------------------------------------------------

def _p_group_key(pipe, group_by):
    if group_by == "shift":
        return f"Shift {pipe.shift}" if pipe.shift else "Unknown shift"
    if group_by == "machine":
        return analytics_service.pipe_machine_code(pipe) or "Unknown"
    if group_by == "dn":
        return f"DN{pipe.diameter}" if pipe.diameter else "Unknown DN"
    if group_by == "order":
        return (pipe.production_order.order_number
                if pipe.production_order else "No order")
    return pipe.production_date.isoformat() if pipe.production_date else "Unknown"


def p_chart_data(filters, group_by="day"):
    """Proportion of non-accept pipe decisions per group.

    Decisions compare case-insensitively (a lowercase ``accept`` conforms).
    HOLD is non-conforming and also counted separately so the UI can show it
    distinctly (the non-conformance register deliberately excludes Hold).
    Pipes with no decision are excluded from the denominator.
    """
    pipes = analytics_service.apply_pipe_filters(Pipe.query, filters).all()

    groups = {}
    order = []
    for pipe in pipes:
        decision = (pipe.final_decision_value or "").strip()
        if not decision:
            continue
        key = _p_group_key(pipe, group_by)
        if key not in groups:
            groups[key] = {"key": key, "n": 0, "defectives": 0, "hold": 0}
            order.append(key)
        g = groups[key]
        g["n"] += 1
        lowered = decision.lower()
        if lowered != "accept":
            g["defectives"] += 1
            if lowered == "hold":
                g["hold"] += 1

    rows = []
    for key in order:
        g = groups[key]
        g["p"] = g["defectives"] / g["n"] if g["n"] else 0.0
        rows.append(g)
    return {"groups": rows, "group_by": group_by}


def c_chart_data(filters, group_by="day"):
    """Defect counts (PipeStage.has_defect) per group."""
    query = (
        db.session.query(PipeStage)
        .join(Pipe, PipeStage.pipe_id == Pipe.id)
        .filter(PipeStage.has_defect.is_(True))
    )
    # Pipe-level filters reuse the shared helper via a subquery of pipe ids.
    pipe_ids = [
        p.id for p in analytics_service.apply_pipe_filters(Pipe.query, filters)
        .with_entities(Pipe.id).all()
    ]
    if not pipe_ids:
        return {"groups": [], "group_by": group_by}
    query = query.filter(PipeStage.pipe_id.in_(pipe_ids))

    stages = query.all()
    pipes_by_id = {
        p.id: p for p in Pipe.query.filter(Pipe.id.in_(pipe_ids)).all()
    }

    groups = {}
    order = []
    for s in stages:
        pipe = pipes_by_id.get(s.pipe_id)
        if group_by == "stage":
            key = s.stage_name
        elif pipe is not None:
            key = _p_group_key(pipe, group_by)
        else:
            key = "Unknown"
        if key not in groups:
            groups[key] = {"key": key, "count": 0}
            order.append(key)
        groups[key]["count"] += 1

    return {"groups": [groups[k] for k in order], "group_by": group_by}
