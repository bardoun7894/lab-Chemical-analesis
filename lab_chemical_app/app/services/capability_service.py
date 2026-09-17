"""Process capability service — Cp/Cpk/Pp/Ppk/DPMO against spec limits.

Spec limits are NEVER fabricated: chemistry limits come from the decision
service's own element-rules resolver (the optimal / فحص أخيرة فقط range), and
the tensile limit from the same mechanical_rules config the decision engine
uses. Characteristics with no stored limits report "unavailable".

Conventions
-----------
- sigma_within  = MR̄ / 1.128 (individuals chart estimate) → Cp/Cpk
- sigma_overall = sample stdev (ddof=1)                     → Pp/Ppk
- DPMO from the overall (long-term) normal model
- sigma level = 3·Ppk + 1.5 (customary 1.5σ shift)
- n < 30 → low-confidence flag (indices still shown, never hidden)
"""
from statistics import mean, stdev, NormalDist

from app.services import decision_service, mechanical_decision_service
from app.services import dimension_standard_service
from app.services.spc_service import D2_IMR, CHARACTERISTICS

LOW_CONFIDENCE_N = 30

# The optimal-range decision string in element_rules.json (same constant the
# decision service treats as in-spec).
OPTIMAL_DECISION = "فحص أخيرة فقط"

# tensile_mpa = tensile_strength * 9.8 everywhere in the app
# (MechanicalTest.calculate_derived_values).
KGF_TO_MPA = 9.8


def _tensile_lsl_mpa():
    """Tensile lower limit in MPa from the shared mechanical rules config."""
    threshold_kgf = _criterion_bound("tensile_strength", ">=")
    if threshold_kgf is None:
        return None
    return threshold_kgf * KGF_TO_MPA


# SPC characteristic -> (acceptance_criteria key, which side the bound is).
# Every one of these criteria is already configured in mechanical_rules.json;
# they were simply never read here, so the lab characteristics showed as
# "no limits" in the capability picker while the decision engine was happily
# judging tests against them.
MECHANICAL_CRITERION = {
    "tensile_strength": ("tensile_strength", "lsl"),
    "elongation": ("elongation", "lsl"),
    "nodularity": ("nd", "lsl"),
    "microstructure_70": ("ferrite", "lsl"),
    "nodule_count": ("nc", "lsl"),
    "hardness": ("hardness", "usl"),
    "carbides": ("carbides", "usl"),
}


def _criterion_bound(key, operator):
    """The numeric bound of one acceptance criterion, or None.

    Never guesses: a criterion whose condition does not start with the expected
    operator, or whose number will not parse, yields None so the characteristic
    stays limitless rather than getting a fabricated bound.
    """
    config = mechanical_decision_service.load_mechanical_config()
    criteria = config.get("acceptance_criteria", {}).get(key, {})
    condition = (criteria.get("condition") or "").strip()
    if not condition.startswith(operator):
        return None
    try:
        return float(condition[len(operator):].strip())
    except ValueError:
        return None


def _mechanical_limits(characteristic):
    """(LSL, USL) for a mechanical characteristic from mechanical_rules.json."""
    entry = MECHANICAL_CRITERION.get(characteristic)
    if not entry:
        return None
    key, side = entry
    # ">=" is a floor, "<" a ceiling. Both are one-sided: the standard sets a
    # minimum tensile, not a maximum, and a maximum hardness, not a minimum.
    operator = ">=" if side == "lsl" else "<"
    bound = _criterion_bound(key, operator)
    if bound is None:
        return None
    source = f"mechanical_rules acceptance criterion ({key} {operator} {bound:g})"
    if side == "lsl":
        return {"lsl": bound, "usl": None, "one_sided": True, "source": source}
    return {"lsl": None, "usl": bound, "one_sided": True, "source": source}


# Dimensional characteristic -> the TA 1012 symbol that carries its tolerance.
# The lining layers are covered by a different standard and have none here, so
# they deliberately have no entry rather than a borrowed limit.
DIMENSIONAL_SYMBOL = {
    "wall_thickness": "S1",
    "outer_diameter": "d1",
}


def dimensional_symbol_for(characteristic):
    """The standard symbol behind a dimensional characteristic, or None."""
    if characteristic in DIMENSIONAL_SYMBOL:
        return DIMENSIONAL_SYMBOL[characteristic]
    if characteristic.startswith("dim_"):
        symbol = characteristic[4:]
        if symbol in dimension_standard_service.symbol_keys():
            return symbol
    return None


def _filtered_dns(filters):
    """Distinct DNs in the filtered pipe set, as strings."""
    from app.models.pipe import Pipe
    from app.services import analytics_service

    if filters and filters.get("diameter"):
        return {str(filters["diameter"])}
    if filters is None:
        return set()
    query = analytics_service.apply_pipe_filters(Pipe.query, filters)
    return {
        str(dn) for (dn,) in query.with_entities(Pipe.diameter).distinct()
        if dn is not None
    }


def dimensional_limit_note(characteristic, filters=None):
    """Why a dimensional characteristic has no limits, in one sentence.

    Returns None when limits are available (or the characteristic is not
    dimensional). Cp/Cpk needs one spec band for the whole series, and the
    band is per DN — so a series spanning several DNs has no single answer and
    must say so rather than quietly picking one.
    """
    symbol = dimensional_symbol_for(characteristic)
    if symbol is None:
        return None
    dns = _filtered_dns(filters)
    if not dns:
        return "Filter to one DN to get spec limits for this characteristic."
    if len(dns) > 1:
        return (
            "Series spans DN " + ", ".join(sorted(dns))
            + " — the tolerance differs per DN, so filter to one."
        )
    dn = next(iter(dns))
    if dimension_standard_service.limits_for(dn, symbol) is None:
        return (
            f"No tolerance on file for DN{dn} {symbol} — "
            "fill it in Settings > Dimension Standards."
        )
    return None


def spec_limits_for(characteristic, filters=None):
    """Resolve (LSL, USL) for a characteristic, or None when unavailable.

    Chemical characteristics resolve through decision_service.load_element_rules
    (the exact resolver the auto-decision engine uses — a mock/spy on that
    function observes this call). Tensile resolves through the mechanical
    rules config. Dimensional characteristics resolve through the TA 1012
    standard for the filtered DN, and only when the series is a single DN —
    see dimensional_limit_note. Anything else returns None.
    """
    symbol = dimensional_symbol_for(characteristic)
    if symbol is not None:
        dns = _filtered_dns(filters)
        if len(dns) != 1:
            return None
        return dimension_standard_service.limits_for(next(iter(dns)), symbol)

    if characteristic == "tensile_mpa":
        lsl = _tensile_lsl_mpa()
        if lsl is None:
            return None
        return {"lsl": lsl, "usl": None, "one_sided": True,
                "source": f"mechanical_rules acceptance criterion "
                          f"({lsl / KGF_TO_MPA:g} KgF/mm²)"}

    mechanical = _mechanical_limits(characteristic)
    if mechanical is not None:
        return mechanical

    element_code = decision_service.ELEMENT_MAP.get(characteristic)
    if not element_code:
        return None
    rules = decision_service.load_element_rules()
    ranges = rules.get(element_code)
    if not ranges:
        return None
    optimal = [r for r in ranges if r.get("decision") == OPTIMAL_DECISION]
    if not optimal:
        return None
    return {
        "lsl": float(optimal[0]["min"]),
        "usl": float(optimal[0]["max"]),
        "one_sided": False,
        "source": "element_rules optimal range (فحص أخيرة فقط)",
    }


def _moving_range_sigma(values):
    mrs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return mean(mrs) / D2_IMR


def capability_indices(values, lsl, usl):
    """Compute capability indices for a series against spec limits.

    Returns None for n < 2 or zero-variation series (indices would divide by
    zero — the UI shows a state, not a number). One-sided when only one of
    lsl/usl is given: Cp/Pp are omitted, Cpk/Ppk become the one-sided value.
    """
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n < 2:
        return None

    mu = mean(vals)
    sigma_within = _moving_range_sigma(vals)
    sigma_overall = stdev(vals)  # sample, ddof=1
    if sigma_within == 0 or sigma_overall == 0:
        return None

    one_sided = (lsl is None) != (usl is None)

    def _cpk(sigma):
        candidates = []
        if usl is not None:
            candidates.append((usl - mu) / (3 * sigma))
        if lsl is not None:
            candidates.append((mu - lsl) / (3 * sigma))
        return min(candidates)

    result = {
        "n": n,
        "mean": mu,
        "sigma_within": sigma_within,
        "sigma_overall": sigma_overall,
        "lsl": lsl,
        "usl": usl,
        "one_sided": one_sided,
        "low_confidence": n < LOW_CONFIDENCE_N,
        "cp": None if one_sided else (usl - lsl) / (6 * sigma_within),
        "pp": None if one_sided else (usl - lsl) / (6 * sigma_overall),
        "cpk": _cpk(sigma_within),
        "ppk": _cpk(sigma_overall),
    }

    dist = NormalDist(mu=mu, sigma=sigma_overall)
    p_out = 0.0
    if lsl is not None:
        p_out += dist.cdf(lsl)
    if usl is not None:
        p_out += 1 - dist.cdf(usl)
    result["dpmo"] = p_out * 1_000_000
    result["sigma_level"] = 3 * result["ppk"] + 1.5
    return result


def histogram(values, bins=15):
    """Equal-width histogram bins for the capability chart."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"edges": [], "counts": []}
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return {"edges": [lo, hi], "counts": [len(vals)]}
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return {"edges": edges, "counts": counts}


def capable_characteristics(filters=None):
    """Characteristics that have resolvable spec limits (for the picker)."""
    out = []
    for key in CHARACTERISTICS:
        if spec_limits_for(key, filters) is not None:
            out.append(key)
    return out
