"""Cement lining thickness, measured at the socket and the spigot.

Cement is not taken per metre: the operator measures at the two ends of the
pipe, twice at each, and each measurement is a perpendicular pair — X and Y —
exactly like the diameter readings on the ovality grid. What matters is the
spread between the pair and the average of the four positions, not a profile
along the barrel.

**Coating is different and stays per metre**: six readings, one at each metre
of the 6 m pipe. It was briefly moved onto this grid too and moved back — the
two layers are measured differently on the floor, so they are recorded
differently here.

Shape stored on ``PipeStage.thickness_profile``::

    {"cement_points": {"socket_1": {"x": .., "y": .., "diff": .., "avg": ..},
                       ...},
     "cement_avg": ..,
     "coating": [m1 .. m6]}

``diff`` is the spread of one pair, |X - Y|: how far apart the two readings of
one position are, which has no direction. ``avg`` is their mean, and
``cement_avg`` is the mean of the position averages that were actually taken.
All are derived rather than typed, and are stored so the reports and the export
do not each have to recompute them.

A per-metre ``cement`` list left over from before the grid is not converted:
folding metres into socket and spigot positions would invent readings nobody
took. It is still read where it exists.
"""

# The layers recorded on this grid. Coating is measured per metre and is not
# one of them.
LAYERS = ("cement",)

# (key, end, ordinal) — the ordinals run 1..4 across both ends, as on the sheet.
POINTS = (
    ("socket_1", "socket", "1"),
    ("socket_2", "socket", "2"),
    ("spigot_3", "spigot", "3"),
    ("spigot_4", "spigot", "4"),
)

POINT_KEYS = tuple(key for key, _end, _n in POINTS)

END_LABELS = {
    "socket": ("Socket", "السوكيت"),
    "spigot": ("Spigot", "الاسبيجوت"),
}

# The per-layer keys on the profile.
def points_key(layer):
    return "%s_points" % layer


def average_key(layer):
    return "%s_avg" % layer


def field_name(stage_name, layer, key, axis):
    """The input name one X or Y cell posts under.

    ``_pt_`` keeps these clear of ``stage_<s>_thick_<layer>_<1..6>``, the
    per-metre cells, so a post carrying both is never ambiguous.
    """
    return "stage_%s_thick_%s_pt_%s_%s" % (stage_name, layer, key, axis)


def _num(raw):
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def summarise_point(x, y):
    """(diff, avg) for one X/Y pair, from whichever readings were taken.

    The spread is unsigned: X and Y are two perpendicular measurements of the
    same position, so which one is larger says nothing — only how far apart
    they are. A position with only one reading still has an average, but no
    spread, because there is no second measurement to differ from.
    """
    taken = [v for v in (x, y) if v is not None]
    if not taken:
        return None, None
    avg = round(sum(taken) / len(taken), 2)
    diff = round(abs(x - y), 2) if x is not None and y is not None else None
    return diff, avg


def read_points(values, stage_name, layer):
    """Pull one layer's eight cells out of a posted mapping.

    Returns ``(points, present)``. ``present`` says the screen carried the
    grid at all, so a save from a screen without it never clears the readings.
    """
    points = {}
    present = False
    for key, _end, _n in POINTS:
        row = {}
        for axis in ("x", "y"):
            name = field_name(stage_name, layer, key, axis)
            if name in values:
                present = True
                row[axis] = _num(values.get(name))
        if not row:
            continue
        x, y = row.get("x"), row.get("y")
        diff, avg = summarise_point(x, y)
        points[key] = {"x": x, "y": y, "diff": diff, "avg": avg}
    return points, present


def overall_average(points):
    """Mean of the position averages that were taken, or None."""
    avgs = [(points.get(key) or {}).get("avg") for key, _e, _n in POINTS]
    avgs = [a for a in avgs if a is not None]
    if not avgs:
        return None
    return round(sum(avgs) / len(avgs), 2)


def has_readings(points):
    return any(
        (points or {}).get(key, {}).get(axis) is not None
        for key, _e, _n in POINTS
        for axis in ("x", "y")
    )
