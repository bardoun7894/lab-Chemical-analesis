"""Dimensional standard (TA 1012 / TYTON) lookup — nominal + tolerance per DN.

Until now the only spec limits in the app were chemical (element_rules.json)
and tensile (mechanical_rules.json). Wall thickness and the socket dimensions
had none, so an operator typing a reading had nothing to compare against and
the capability report could not produce Cp/Cpk for them.

This module owns `app/data/dimension_standards.json`: one entry per DN, one
sub-entry per standard symbol (d1..d7, S1, S2, C, t1..t6, r1..r3), each with a
nominal and an asymmetric tolerance. `evaluate()` turns a reading into a
deviation plus an in/out-of-tolerance verdict, and `limits_for()` feeds the
same numbers to the SPC and capability reports.

A symbol whose nominal is null is simply not part of the standard for that DN.
A symbol with a nominal but no tolerance still charts in SPC (it has a target)
but yields no capability index — a missing tolerance is never silently treated
as zero.
"""

import json
import os
import re
import threading

STANDARDS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "dimension_standards.json"
)

# Writes are rare (admin edits) but must not interleave with a concurrent read
# under gunicorn's 4 workers sharing the host-mounted file.
_WRITE_LOCK = threading.Lock()

_EMPTY = {"source": "", "verified": False, "symbols": [], "by_dn": {}}


def load_standards():
    """Return the whole standards document, or an empty skeleton if unreadable.

    Never raises: a missing or corrupt file must degrade to "no spec limits",
    never break the stage form that merely wants to show a nominal.
    """
    try:
        with open(STANDARDS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    data.setdefault("symbols", [])
    data.setdefault("by_dn", {})
    return data


def save_standards(data):
    """Persist the standards document (admin editor)."""
    with _WRITE_LOCK:
        with open(STANDARDS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


# Cement and coating are not TA 1012 symbols — the sheet names neither, and
# they shipped with no nominal on any of the 15 DNs, so they have always been
# empty rows in the Settings editor. Their spec now lives on the product's
# Application table, per standard, and their readings are taken per metre in
# the Coating popup. Leaving them here as a second, DN-keyed source that an
# admin could fill would mean two answers to "what is the cement thickness for
# this pipe" with nothing deciding between them.
#
# Filtered in code rather than removed from dimension_standards.json because
# that file is a live bind mount on every install — editing the shipped copy
# would not reach a running one.
RETIRED_SYMBOLS = ("cement", "coating")


def symbols():
    """Ordered symbol definitions: [{key, label, label_ar}, ...]."""
    return [s for s in load_standards().get("symbols", [])
            if s.get("key") not in RETIRED_SYMBOLS]


def symbol_keys():
    return [s["key"] for s in symbols() if s.get("key")]


def symbol_label(key, locale="en"):
    for s in symbols():
        if s.get("key") == key:
            return s.get("label_ar" if locale == "ar" else "label") or key
    return key


def _normalise_dn(dn):
    """DN comes off Pipe.diameter as int/float/str — the config keys are str."""
    if dn is None:
        return None
    try:
        return str(int(float(dn)))
    except (TypeError, ValueError):
        return str(dn).strip() or None


def for_dn(dn, data=None):
    """All symbol entries for one DN, each enriched with lsl/usl.

    Returns {} when the DN is not in the standard — an unknown DN must read as
    "no spec", not as an error, since the pipe can still be produced.
    """
    data = data if data is not None else load_standards()
    key = _normalise_dn(dn)
    if not key:
        return {}
    raw = (data.get("by_dn") or {}).get(key)
    if not isinstance(raw, dict):
        return {}
    out = {}
    for sym, entry in raw.items():
        if not isinstance(entry, dict) or sym in RETIRED_SYMBOLS:
            continue
        out[sym] = _with_limits(entry)
    return out


def _with_limits(entry):
    """Add lsl/usl derived from nominal ± tolerance; None where undefined."""
    nominal = entry.get("nominal")
    tol_plus = entry.get("tol_plus")
    tol_minus = entry.get("tol_minus")
    lsl = usl = None
    if nominal is not None:
        if tol_minus is not None:
            lsl = float(nominal) - float(tol_minus)
        if tol_plus is not None:
            usl = float(nominal) + float(tol_plus)
    return {
        "nominal": nominal,
        "tol_plus": tol_plus,
        "tol_minus": tol_minus,
        "lsl": lsl,
        "usl": usl,
    }


def limits_for(dn, symbol):
    """(LSL, USL) for one DN+symbol in the shape capability_service expects.

    Returns None when the symbol has no usable two-sided or one-sided limit,
    so the caller skips Cp/Cpk rather than inventing a bound.
    """
    entry = for_dn(dn).get(symbol)
    if not entry:
        return None
    lsl, usl = entry["lsl"], entry["usl"]
    if lsl is None and usl is None:
        return None
    return {
        "lsl": lsl,
        "usl": usl,
        "one_sided": lsl is None or usl is None,
        "nominal": entry["nominal"],
        "source": f"TA 1012 DN{_normalise_dn(dn)} {symbol}",
    }


def evaluate(dn, symbol, actual):
    """Compare one reading against the standard.

    Returns a dict with the nominal, the signed deviation and a status of
    ``in`` / ``out`` / ``no_spec``. ``no_spec`` covers both an unknown DN and a
    symbol that carries a nominal but no tolerance — in neither case may the
    reading be called out-of-tolerance.
    """
    result = {
        "actual": actual,
        "nominal": None,
        "deviation": None,
        "lsl": None,
        "usl": None,
        "status": "no_spec",
    }
    if actual is None:
        return result
    entry = for_dn(dn).get(symbol)
    if not entry or entry["nominal"] is None:
        return result

    actual = float(actual)
    nominal = float(entry["nominal"])
    result.update(
        nominal=entry["nominal"],
        deviation=round(actual - nominal, 4),
        lsl=entry["lsl"],
        usl=entry["usl"],
    )
    if entry["lsl"] is None and entry["usl"] is None:
        return result
    within = True
    if entry["lsl"] is not None and actual < entry["lsl"]:
        within = False
    if entry["usl"] is not None and actual > entry["usl"]:
        within = False
    result["status"] = "in" if within else "out"
    return result


def available_dns():
    """DN keys present in the standard, numerically ordered."""
    keys = list((load_standards().get("by_dn") or {}).keys())

    def _sort_key(k):
        try:
            return (0, float(k))
        except ValueError:
            return (1, 0.0)

    return sorted(keys, key=_sort_key)


def is_verified():
    """False while the transcribed table still needs a human check."""
    return bool(load_standards().get("verified"))


# --- Ovality / dimension grid columns -------------------------------------
#
# The Annealing grid used to be a fixed ``["ID"] + D1..D15`` run of columns
# that matched no symbol on the standard sheet, so the operator had to map an
# unnamed box onto a sheet symbol in their head (DrAlaa 2026-08-23, holding the
# TA 1012 page next to the popup: "mish D1 w D2 ... 3ala hasab elly moragooda
# fel gadwal"). The columns now come from the standard itself.

# TA 1012 names no symbol for the internal diameter, but the operator measures
# it, so it leads the grid ahead of the sheet's own symbols.
OVALITY_ID_POINT = "ID"

# Ovality = |X-Y|/(X+Y)×100 states how far from round a circle is. It says nothing
# about a wall thickness, a socket depth or a radius, so it is computed on the
# diameters only and left blank under every other column instead of being
# printed as if a thickness could be oval.
OVALITY_DIAMETERS = (OVALITY_ID_POINT, "d1", "d4")

# Readings taken before 2026-08-23 were keyed D1..D15 — an invented run that
# matched no symbol. New grids never post these, but old profiles still hold
# them, and they were diameters along the pipe, so they keep their ovality.
_LEGACY_POINT_RE = re.compile(r"D\d+")


def ovality_points_for(dn):
    """Grid columns for a DN: ID, then the symbols TA 1012 defines for it.

    A DN with no nominals on file renders ID alone rather than fifteen boxes
    with no target behind them.
    """
    entries = for_dn(dn) or {}
    return [OVALITY_ID_POINT] + [
        key for key in symbol_keys()
        if (entries.get(key) or {}).get("nominal") is not None
    ]


def ovality_columns_for(dn, profile=None):
    """`ovality_points_for` plus any legacy point the saved profile still has.

    Rendering only the current columns would hide a D1..D15 reading taken
    before the change — indistinguishable from data loss on every pipe
    annealed until then.
    """
    points = ovality_points_for(dn)
    saved = list((profile or {}).get("points") or {})
    return points + sorted(p for p in saved if p not in points)


def is_ovality_point(point):
    """True when ovality is meaningful for this column (a diameter).

    Widened to every column at some point before the 2026-08-27 prod sync,
    which put an "Ovality %" under S1 — a wall thickness. The grid's own JS
    had always computed the percentage for every column, so the likely intent
    was to stop the stored value disagreeing with the displayed one. The fix
    belongs on the other side: the JS now honours ``data-oval``, which exists
    for exactly this and had gone dead.
    """
    return point in OVALITY_DIAMETERS or bool(
        _LEGACY_POINT_RE.fullmatch(point or ""))


def known_point_names():
    """Every point name a post may legitimately carry, DN aside.

    Validation is deliberately DN-independent: a pipe whose DN is corrected
    after the reading was taken must not have its measurements rejected.
    """
    return set(symbol_keys()) | {OVALITY_ID_POINT}


def merged_readings(ovality_profile, dimension_profile):
    """Grid cells for one stage: X/Y pairs, backfilled from the older grid.

    Readings taken before the ovality and standard grids merged live in
    ``dimension_profile["symbols"]`` as a single actual per symbol. They are
    surfaced as X, with Y left empty, so an upgraded install never shows a
    blank box for a dimension that was in fact measured. A real X/Y pair always
    wins over the backfill.
    """
    points = dict((ovality_profile or {}).get("points") or {})
    legacy = (dimension_profile or {}).get("symbols") or {}
    for symbol, actual in legacy.items():
        if symbol in points or actual is None:
            continue
        points[symbol] = {"x": actual, "y": None}
    return points
