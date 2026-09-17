"""Descriptive statistics over the readings stored in the stage JSON profiles.

The stage popups collect repeated readings — 7 positions x 3 readings for wall
thickness, 6 meters per lining layer, 15 diameter points — but until now the
app only ever stored and re-displayed them. This module turns a set of
readings into the numbers the lab actually reports: n, average, standard
deviation, min, max, range.

Sample (n-1) standard deviation is used throughout, matching what the SPC and
capability reports already assume. A single reading has no dispersion, so
``stdev`` is None rather than 0 — reporting 0 would read as "perfectly
consistent" when nothing was measured twice.
"""

import math

# Wall thickness is measured at these positions along the 6 m pipe. 5.5 is a
# real position on the operator's sheet (an extra reading near the spigot),
# not a typo — the labels are strings so "5.5" survives round-tripping.
THICKNESS_POSITIONS = ["1", "2", "3", "4", "5", "5.5", "6"]

# Readings taken at each position.
THICKNESS_READINGS_PER_POSITION = 3


def _clean(values):
    """Numeric readings only — drop None/blank/non-numeric cells."""
    out = []
    for v in values or []:
        if v is None or v == "":
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def describe(values):
    """n / mean / stdev / min / max / range over a flat list of readings.

    Returns None for every statistic that the sample size cannot support,
    never a placeholder zero.
    """
    vals = _clean(values)
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "stdev": None, "min": None,
                "max": None, "range": None}
    mean = sum(vals) / n
    stdev = None
    if n > 1:
        variance = sum((v - mean) ** 2 for v in vals) / (n - 1)
        stdev = math.sqrt(variance)
    return {
        "n": n,
        "mean": round(mean, 4),
        "stdev": round(stdev, 4) if stdev is not None else None,
        "min": min(vals),
        "max": max(vals),
        "range": round(max(vals) - min(vals), 4),
    }


# ---------------------------------------------------------------------------
# Wall thickness — 7 positions x 3 readings
# ---------------------------------------------------------------------------


def thickness_matrix(profile):
    """Normalise a CCM thickness profile into {position: [r1, r2, r3]}.

    Handles both shapes:

    * current — ``{"thickness": {"positions": {"1": [a, b, c], ...}}}``
    * legacy  — ``{"thickness": {"samples": {"S1": [1..21]}}}``, the flat
      21-cell grid written before the positions were named. The 21 cells were
      always 7 positions x 3 readings in the operator's sheet, so they are
      folded back in that order rather than discarded.

    Returns {} when the profile carries no thickness readings at all.
    """
    if not isinstance(profile, dict):
        return {}
    block = profile.get("thickness")
    if not isinstance(block, dict):
        return {}

    positions = block.get("positions")
    if isinstance(positions, dict) and positions:
        return {
            str(pos): list(vals or [])
            for pos, vals in positions.items()
        }

    samples = block.get("samples")
    if isinstance(samples, dict) and samples:
        # Legacy flat rows: take the first sample and slice it 3-by-3.
        for _label, vals in sorted(samples.items()):
            if not vals:
                continue
            return _fold_flat(vals)
    return {}


def _fold_flat(vals):
    """Slice a flat 21-cell row into {position: [r1, r2, r3]}."""
    out = {}
    for idx, pos in enumerate(THICKNESS_POSITIONS):
        start = idx * THICKNESS_READINGS_PER_POSITION
        chunk = list(vals[start:start + THICKNESS_READINGS_PER_POSITION])
        while len(chunk) < THICKNESS_READINGS_PER_POSITION:
            chunk.append(None)
        out[pos] = chunk
    return out


def thickness_stats(profile):
    """Per-position stats plus an overall roll-up for a thickness profile.

    ``by_position`` keeps the operator's reading order; ``overall`` describes
    every reading on the pipe as one sample, which is what feeds the capability
    report.
    """
    matrix = thickness_matrix(profile)
    if not matrix:
        return None
    by_position = {}
    flat = []
    for pos in THICKNESS_POSITIONS:
        vals = matrix.get(pos, [])
        by_position[pos] = describe(vals)
        flat.extend(_clean(vals))
    # A position the operator added outside the standard seven still counts.
    for pos, vals in matrix.items():
        if pos not in by_position:
            by_position[pos] = describe(vals)
            flat.extend(_clean(vals))
    return {
        "by_position": by_position,
        "overall": describe(flat),
        "readings": flat,
    }


# ---------------------------------------------------------------------------
# Diameter grid and lining layers
# ---------------------------------------------------------------------------


def diameter_stats(profile):
    """Stats per diameter sample row, plus an overall roll-up."""
    if not isinstance(profile, dict):
        return None
    block = profile.get("diameter")
    if not isinstance(block, dict):
        return None
    samples = block.get("samples") or {}
    if not samples:
        return None
    by_sample = {}
    flat = []
    for label, vals in sorted(samples.items()):
        by_sample[label] = describe(vals)
        flat.extend(_clean(vals))
    if not flat:
        return None
    return {"by_sample": by_sample, "overall": describe(flat), "readings": flat}


def lining_stats(thickness_profile):
    """Stats per lining layer (cement / coating) on the Coating stage."""
    if not isinstance(thickness_profile, dict):
        return None
    out = {}
    for layer in ("cement", "coating"):
        vals = thickness_profile.get(layer)
        if not vals:
            continue
        stats = describe(vals)
        if stats["n"]:
            out[layer] = stats
    return out or None
