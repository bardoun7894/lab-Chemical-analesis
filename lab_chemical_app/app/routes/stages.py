"""
Production Stages Routes
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from datetime import date, datetime
from sqlalchemy.exc import IntegrityError
from app import db, get_locale
from app.models.pipe import Pipe, PipeStage, normalize_no_code
from app.models.chemical import ChemicalAnalysis, Machine, DefectType, DecisionType
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.stage_defect_type import StageDefectType
from app.models.stage_decision_type import StageDecisionType
from app.models.stage_history import PipeStageHistory
from app.models.product import Mold
from app.models.user import User
from app.services.permission_service import requires_permission
from app.services.decision_service import get_decision_color
from app.services import measurement_stats_service
from app.services import stage_flow_service
from app.services import dimension_standard_service
from app.services import lining_points_service
from app.services.barcode_service import clean_barcode


# Standard DN sizes used across the plant
DN_SIZES = [
    80,
    100,
    150,
    200,
    250,
    300,
    350,
    400,
    450,
    500,
    600,
    700,
    800,
    900,
    1000,
    1200,
]

# Available pipe classes
PIPE_CLASSES = ["K9", "C25", "C40", "C50", "C64", "Fittings"]


def _get_shift_engineers():
    """Return users that can be selected as Shift Engineer on the pipe form."""
    return (
        User.query.filter(
            User.is_active.is_(True),
            User.role.in_(["operator", "supervisor", "admin"]),
        )
        .order_by(User.full_name, User.username)
        .all()
    )


def _extract_dn(product):
    """Extract the actual nominal diameter integer from a Product (e.g. 800).

    Delegates to ``Product.dn_value`` so the DN-parsing rule lives in one place
    (the DN ``code`` like 'P80' is a product-code token, not the real DN).
    """
    return product.dn_value if product else None


def _get_form_context(**extra):
    """Shared context for pipe form render — centralises dropdown data."""
    from app.models.product import Product
    products = Product.query.filter_by(is_active=True).order_by(Product.product_code).all()
    ctx = dict(
        products=products,
        product_dn_map={p.id: _extract_dn(p) for p in products},
        machines=Machine.query.filter_by(is_active=True).all(),
        molds=Mold.query.filter_by(is_active=True)
        .order_by(Mold.diameter, Mold.mold_number)
        .all(),
        latest_ladles=ChemicalAnalysis.query.order_by(ChemicalAnalysis.id.desc())
        .limit(10)
        .all(),
        production_orders=ProductionOrder.query.filter(
            ProductionOrder.status.in_(["pending", "in_progress"])
        )
        .order_by(ProductionOrder.order_date.desc())
        .all(),
        stage_decisions=get_stage_decisions_from_db(),
        stage_defects=get_stage_defects_from_db(),
        stage_machines=get_stage_machines_from_db(),
        dn_sizes=DN_SIZES,
        pipe_classes=PIPE_CLASSES,
        shift_engineers=_get_shift_engineers(),
        today=date.today(),
        ccm_stage=ProductionStage.name_for_code("ccm"),
        coating_stage=ProductionStage.name_for_code("coating"),
        cutting_stage=ProductionStage.name_for_code("cutting"),
        annealing_stage=ProductionStage.name_for_code("annealing"),
        zinc_stage=ProductionStage.name_for_code("zinc"),
        measurement_units=PipeStage.measurement_units_by_stage(),
    )
    ctx.update(extra)
    return ctx


stages_bp = Blueprint("stages", __name__)


def _parse_thickness_profile(items, stage_name):
    """Scan a key/value mapping for the cement/coating thickness grids.

    Only the Coating row renders these inputs. Returns
    ``(profile_dict_or_None, has_readings)``. Profile shape::

        {"cement_points": {"socket_1": {"x", "y", "diff", "avg"}, ...},
         "cement_avg": float_or_None,
         "cement_std"/"_std_min"/"_std_max": float_or_None,
         and the same four for "coating"}

    The two halves of the answer are deliberately different questions. The
    profile is everything the post carried and is worth writing. The flag is
    whether the operator actually *measured* something, which is what decides
    whether this stage happened at all — the Min/Nominal/Max band is prefilled
    from the order's spec and posts on every save, so counting it as activity
    marked untouched stages as in progress.

    The band is only written when the post actually carried the input. A screen
    that renders the reading cells but not the band — the Stage Console did
    until 2026-08-29 — otherwise nulled the band the order's spec had filled
    in, silently losing the limits the readings are judged against. Absent
    means "no opinion"; present-and-blank still clears.
    """
    if stage_name != ProductionStage.name_for_code("coating"):
        return None, False
    values = dict(items)
    profile = {}
    has_readings = False
    has_payload = False

    # Cement is measured X/Y at the socket and the spigot.
    for layer in lining_points_service.LAYERS:
        points, present = lining_points_service.read_points(
            values, stage_name, layer)
        if not present:
            continue
        has_payload = True
        profile[lining_points_service.points_key(layer)] = points
        profile[lining_points_service.average_key(layer)] = \
            lining_points_service.overall_average(points)
        if lining_points_service.has_readings(points):
            has_readings = True

    # Coating is measured once at each metre of the 6 m pipe.
    coating_cells = []
    coating_present = False
    for m in range(1, 7):
        field = f"stage_{stage_name}_thick_coating_{m}"
        if field in values:
            coating_present = True
        raw = values.get(field)
        if raw:
            has_readings = True
            coating_cells.append(float(raw))
        else:
            coating_cells.append(None)
    if coating_present:
        has_payload = True
        profile["coating"] = coating_cells

    # Both layers keep their Min / Nominal / Max band, whichever way their
    # readings are taken.
    for layer in ("cement", "coating"):
        for suffix in ("_std", "_std_min", "_std_max"):
            field = f"stage_{stage_name}_thick_{layer}{suffix}"
            if field not in values:
                continue
            raw = values.get(field)
            profile[f"{layer}{suffix}"] = float(raw) if raw else None
            # Present-and-blank is a deliberate clear, so the band is still
            # worth writing. It is NOT stage activity though: it is prefilled
            # from the order's spec and posts on every save, so counting it
            # would create a stage row — and show the stage as in progress —
            # for a pipe nobody has measured.
            has_payload = True
    return (profile if has_payload else None), has_readings


def _merge_thickness_profile(existing, posted):
    """Overlay the posted lining keys onto the saved profile.

    ``_parse_thickness_profile`` returns only the keys a post carried, so a
    console save that has the six meter cells but no Min/Nominal/Max band must
    not blank the band. Keys are replaced individually, not deep-merged:
    clearing a cell on the screen that renders it still clears it.
    """
    merged = dict(existing or {})
    posted = dict(posted or {})

    # The positions merge one at a time. A post that names only the socket
    # would otherwise replace the whole set and drop the spigot readings,
    # which is what a partial API save looks like.
    for layer in lining_points_service.LAYERS:
        key = lining_points_service.points_key(layer)
        if key not in posted:
            continue
        points = dict(merged.get(key) or {})
        points.update(posted.pop(key) or {})
        merged[key] = points
        posted[lining_points_service.average_key(layer)] = \
            lining_points_service.overall_average(points)

    merged.update(posted)
    return merged


def _read_thickness_profile(stage_name):
    """Form-post variant of _parse_thickness_profile (add/edit routes)."""
    return _parse_thickness_profile(request.form.items(), stage_name)


def _parse_dimension_profile(items, stage_name):
    """Scan a key/value mapping for the CCM dimension grids.

    Wall thickness is measured at 7 positions along the pipe (1, 2, 3, 4, 5,
    5.5, 6 m) with 3 readings at each — the operator's sheet exactly. Input
    names are ``stage_<CCM>_dim_thick_p<position index>_<reading>``, and the
    saved shape is::

        {"thickness": {"positions": {"1": [r1, r2, r3], ...}},
         "diameter":  {"samples": {"S1": [D1..D15], ...}}}

    Before 2026-08-22 thickness was a flat 21-cell row per sample
    (``thick_S<k>_<n>``); those 21 cells were always these 7x3 readings, just
    unnamed. Such posts are still accepted and folded into positions so an old
    form left open in a tab cannot silently drop a pipe's readings.

    Diameter keeps its dynamic sample rows (S1..Sn). The legacy "Standard"
    thickness row was removed 2026-07-26 and any ``dim_thick_standard_*`` input
    is ignored.

    Both CCM and Annealing additionally carry the TA 1012 symbol readings
    (d1, d2, S1, C, t1 ...) posted as ``stage_<Stage>_std_<symbol>`` and saved
    under ``{"symbols": {"d1": 326.4, ...}}``. Annealing carries only those —
    the thickness and diameter grids stay on CCM.

    Returns ``(profile_or_None, has_readings)``. The two are different
    questions: the profile is everything the post carried and is worth
    writing, while the flag says whether the operator actually measured
    something. The Min/Nominal/Max band is prefilled from the order's
    Application spec and posts on every save, so counting it as activity
    stamped a date on CCM and showed the stage as In Progress on a pipe
    nobody had touched.
    """
    ccm_stage = ProductionStage.name_for_code("ccm")
    annealing_stage = ProductionStage.name_for_code("annealing")
    if stage_name not in (ccm_stage, annealing_stage):
        return None, False
    import re

    # request.form.items() is a one-shot iterator and this function scans the
    # mapping twice (symbols, then the grids) — materialise it or the second
    # pass silently sees an empty form. Unpacking, not list(): this module
    # defines a `list` view function that shadows the builtin.
    items = [*items]

    symbols, has_symbols = _parse_standard_symbols(items, stage_name)
    if stage_name != ccm_stage:
        # Annealing's grid was merged into the ovality table on 2026-08-23 and
        # no longer posts a separate std_ row, so the actual each report reads
        # is derived from that grid's X/Y pair. An explicit std_ post still
        # wins, which keeps an older form left open in a tab working.
        ov_profile, _has_ov = _parse_ovality_profile(items, stage_name)
        derived, has_derived = _symbols_from_ovality(ov_profile)
        if has_derived:
            derived.update(symbols)
            symbols, has_symbols = derived, True
        if not has_symbols:
            return None, False
        return {"symbols": symbols}, True

    positions = measurement_stats_service.THICKNESS_POSITIONS
    per_position = measurement_stats_service.THICKNESS_READINGS_PER_POSITION

    prefix = f"stage_{stage_name}_dim_"
    ov_prefix = f"stage_{stage_name}_ov_"
    thick_positions = {}
    legacy_flat = {}
    dia = {"samples": {}}
    thick_std = None
    thick_std_min = None
    thick_std_max = None
    # Which band inputs the post actually carried. A screen that renders the
    # meter grid but not the Min/Nominal/Max band — the Stage Console did until
    # 2026-08-29 — must leave a saved band alone rather than dropping it when
    # the thickness section is rewritten. Present-and-blank still clears.
    std_present = {
        rest: (prefix + rest) in {k for k, _ in items}
        for rest in ("thick_std", "thick_std_min", "thick_std_max")
    }
    ov_raw = {}
    has_any = False
    has_readings = False
    has_payload = False
    for key, val in items:
        if not val:
            continue

        if key.startswith(ov_prefix):
            rest = key[len(ov_prefix):]
            m_ov = re.fullmatch(r"(x|y)_([A-Za-z0-9_]+)", rest)
            if m_ov:
                axis, point = m_ov.group(1), m_ov.group(2)
                known = dimension_standard_service.known_point_names()
                if point in known or re.fullmatch(r"D\d+", point):
                    try:
                        ov_raw.setdefault(point, {})[axis] = float(val)
                        has_any = True
                    except (TypeError, ValueError):
                        pass
            continue

        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]

        if rest == "thick_std":
            try:
                thick_std = float(val)
                # The band, not a reading: see has_readings below.
                has_payload = True
            except (TypeError, ValueError):
                pass
            continue

        if rest == "thick_std_min":
            try:
                thick_std_min = float(val)
                # The band, not a reading: see has_readings below.
                has_payload = True
            except (TypeError, ValueError):
                pass
            continue

        if rest == "thick_std_max":
            try:
                thick_std_max = float(val)
                # The band, not a reading: see has_readings below.
                has_payload = True
            except (TypeError, ValueError):
                pass
            continue

        m = re.fullmatch(r"thick_p(\d+)_(\d+)", rest)
        if m:
            pos_idx, reading_idx = int(m.group(1)), int(m.group(2))
            if 1 <= pos_idx <= len(positions) and 1 <= reading_idx <= per_position:
                label = positions[pos_idx - 1]
                thick_positions.setdefault(label, [None] * per_position)[
                    reading_idx - 1
                ] = float(val)
                has_any = True
                has_readings = True
            continue

        m = re.fullmatch(r"thick_(S\d+)_(\d+)", rest)
        if m and 1 <= int(m.group(2)) <= 21:
            legacy_flat.setdefault(m.group(1), [None] * 21)[
                int(m.group(2)) - 1
            ] = float(val)
            has_any = True
            has_readings = True
            continue

        m = re.fullmatch(r"dia_(S\d+)_(\d+)", rest)
        if m and 1 <= int(m.group(2)) <= 15:
            dia["samples"].setdefault(m.group(1), [None] * 15)[
                int(m.group(2)) - 1
            ] = float(val)
            has_any = True
            has_readings = True
    has_any = has_any or has_payload
    if not has_any and not has_symbols:
        return None, False

    if not thick_positions and legacy_flat:
        thick_positions = measurement_stats_service.thickness_matrix(
            {"thickness": {"samples": legacy_flat}}
        )
    # Only the sections the post actually carried are returned; the caller
    # merges them over the saved profile. Returning an empty thickness block
    # for a symbols-only post would wipe the grid the operator filled earlier.
    profile = {}
    if has_any:
        ordered = {p: thick_positions[p] for p in positions if p in thick_positions}
        ordered.update(
            {p: v for p, v in thick_positions.items() if p not in ordered}
        )
        thick_block = {"positions": ordered}
        if thick_std is not None or std_present["thick_std"]:
            thick_block["standard"] = thick_std
        if thick_std_min is not None or std_present["thick_std_min"]:
            thick_block["standard_min"] = thick_std_min
        if thick_std_max is not None or std_present["thick_std_max"]:
            thick_block["standard_max"] = thick_std_max
        profile["thickness"] = thick_block
        profile["diameter"] = dia
        if ov_raw:
            ov_points = {}
            for pt, axes in ov_raw.items():
                x, y = axes.get("x"), axes.get("y")
                if x is None and y is None:
                    continue
                entry = {"x": x, "y": y}
                if (dimension_standard_service.is_ovality_point(pt)
                        and x is not None and y is not None and (x + y)):
                    entry["ovality"] = round((x - y) / (x + y) * 100, 2)
                ov_points[pt] = entry
            if ov_points:
                profile["ovality"] = {"points": ov_points}
                has_readings = True
    if has_symbols:
        profile["symbols"] = symbols
        has_readings = True
    return profile, has_readings


def _parse_standard_symbols(items, stage_name):
    """Read the TA 1012 symbol grid posted as ``stage_<Stage>_std_<symbol>``.

    One actual reading per standard symbol (d1, d2, S1, C, t1 ...). Only the
    symbols the standard actually defines are accepted, so a stray field name
    can never invent a characteristic that no DN has a nominal for.

    Returns ({symbol: value}, has_any).
    """
    import re

    known = set(dimension_standard_service.symbol_keys())
    if not known:
        return {}, False
    prefix = f"stage_{stage_name}_std_"
    out = {}
    has_any = False
    for key, val in items:
        if val in (None, "") or not key.startswith(prefix):
            continue
        symbol = key[len(prefix):]
        if symbol not in known or not re.fullmatch(r"[A-Za-z0-9_]+", symbol):
            continue
        try:
            out[symbol] = float(val)
        except (TypeError, ValueError):
            continue
        has_any = True
    return out, has_any


def _merge_dimension_profile(existing, posted):
    """Overlay the posted dimension sections onto the saved profile.

    ``_parse_dimension_profile`` returns only the sections a post actually
    carried, so a symbols-only save from the Annealing popup must not blank the
    thickness grid CCM wrote earlier (and vice versa). Sections are replaced
    wholesale, not deep-merged: clearing every cell of a grid the operator is
    editing has to be able to clear it.

    The one exception is the thickness section's Min/Nominal/Max band, which
    ``_parse_dimension_profile`` only emits when the post carried those inputs
    — it sits in the same section as the grid but is not rendered on every
    screen that takes the readings.
    """
    merged = dict(existing or {})
    posted = posted or {}
    for section, block in posted.items():
        if section == "thickness" and isinstance(block, dict):
            # The Min/Nominal/Max band is part of this section but is not
            # rendered on every screen that takes the readings, so merge the
            # thickness section key-wise: the posted grid replaces the saved
            # grid, and a band the post never carried survives.
            band = dict(merged.get(section) or {})
            band.update(block)
            merged[section] = band
        else:
            merged[section] = block
    return merged


def _read_dimension_profile(stage_name):
    """Form-post variant of _parse_dimension_profile (add/edit routes)."""
    return _parse_dimension_profile(request.form.items(), stage_name)


# The grid columns come from the standard sheet, not from an invented D1..D15
# run — see dimension_standard_service for why. Re-exported here because the
# routes and their tests read the grid through this module.
ovality_points_for = dimension_standard_service.ovality_points_for
ovality_columns_for = dimension_standard_service.ovality_columns_for


def _parse_ovality_profile(items, stage_name):
    """Scan a key/value mapping for the Annealing ovality/dimension grid.

    Input names: ``stage_<Annealing>_ov_x_<point>`` and ``..._ov_y_<point>``,
    where <point> is ID or a TA 1012 symbol (d1, d2, S1, C, t1 ...). X and Y
    are the two perpendicular readings at that characteristic.

    Ovality % is computed and stored only where it means something — the
    diameters — so a wall thickness no longer gets a roundness figure printed
    under it (DrAlaa 2026-08-23). It is stored rather than derived on render so
    a saved profile reads the same everywhere.

    A characteristic measured once keeps X with Y left null instead of being
    dropped: most symbols on the sheet take a single reading.

    Returns (profile_or_None, has_any).
    """
    if stage_name != ProductionStage.name_for_code("annealing"):
        return None, False
    import re

    known = dimension_standard_service.known_point_names()
    prefix = f"stage_{stage_name}_ov_"
    raw_points = {}
    has_any = False
    for key, val in items:
        if not val or not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        m = re.fullmatch(r"(x|y)_([A-Za-z0-9_]+)", rest)
        if not m:
            continue
        axis, point = m.group(1), m.group(2)
        # Legacy D1..D15 keys stay accepted so re-saving an old reading, or a
        # form left open in a tab, cannot silently drop what was measured.
        if point not in known and not re.fullmatch(r"D\d+", point):
            continue
        try:
            raw_points.setdefault(point, {})[axis] = float(val)
            has_any = True
        except (TypeError, ValueError):
            continue
    if not has_any:
        return None, False
    points = {}
    for pt, axes in raw_points.items():
        x = axes.get("x")
        y = axes.get("y")
        if x is None and y is None:
            continue
        entry = {"x": x, "y": y}
        if (dimension_standard_service.is_ovality_point(pt)
                and x is not None and y is not None and (x + y)):
            entry["ovality"] = round((x - y) / (x + y) * 100, 2)
        points[pt] = entry
    if not points:
        return None, False
    return {"points": points}, True


def _symbols_from_ovality(profile):
    """One actual per TA 1012 symbol, derived from the grid's X/Y pair.

    spc_service and pipe_measurement_service read
    ``dimension_profile["symbols"]``. The merged grid is now the only place an
    Annealing reading is entered, so the mean of the two perpendicular readings
    — or the single one, when only X was taken — becomes that actual. Without
    this the operator would have to type the same number into two grids for the
    capability report to see it.

    ID is excluded: TA 1012 gives it no nominal, so it has nothing to be
    compared against.

    Returns ({symbol: actual}, has_any).
    """
    points = (profile or {}).get("points") or {}
    known = set(dimension_standard_service.symbol_keys())
    out = {}
    for pt, entry in points.items():
        if pt not in known:
            continue
        vals = [v for v in (entry.get("x"), entry.get("y")) if v is not None]
        if not vals:
            continue
        out[pt] = round(sum(vals) / len(vals), 2)
    return out, bool(out)


def _read_ovality_profile(stage_name):
    """Form-post variant of _parse_ovality_profile (add/edit routes)."""
    return _parse_ovality_profile(request.form.items(), stage_name)


VISUAL_CHECK_KEYS = ("marking", "ovality", "straightness", "internal_finish", "external_finish")

# The sheet's own order: the two weights, the stripped area, the C factor.
ZINC_MASS_KEYS = ("m2", "m1", "area", "c")


def _parse_visual_profile(items, stage_name):
    """Read the Finish-stage visual-inspection checklist from the form post.

    Input names: stage_<Finish>_visual_<key>=1 (or absent).
    Stored shape: {"marking": bool, "ovality": bool, "straightness": bool,
    "internal_finish": bool, "external_finish": bool}. Only returns a profile
    when the modal was actually used (at least one key is present in the
    payload) so that older forms without the modal still save cleanly.
    Returns (profile_or_None, has_any).
    """
    if stage_name != ProductionStage.name_for_code("finish"):
        return None, False
    prefix = f"stage_{stage_name}_visual_"
    seen_keys = set()
    profile = {}
    has_any = False
    for key, val in items:
        if not key.startswith(prefix):
            continue
        short = key[len(prefix):]
        if short.endswith("_hidden"):
            short = short[: -len("_hidden")]
        if short not in VISUAL_CHECK_KEYS:
            continue
        seen_keys.add(short)
        profile[short] = bool(val and val != "0")
        has_any = True
    if not has_any:
        return None, False
    for k in VISUAL_CHECK_KEYS:
        profile.setdefault(k, False)
    # Every box carries a _hidden companion that posts whether or not it is
    # ticked, so the checklist arrives on every save of the Finish stage. An
    # all-unticked one is the form's default, not an inspection: counting it
    # showed Finish as In Progress with five red crosses against a pipe nobody
    # had looked at. The profile is still written — the caller decides what to
    # do with it — but it does not claim the stage happened.
    return profile, any(profile.get(k) for k in VISUAL_CHECK_KEYS)


def _read_visual_profile(stage_name):
    """Form-post variant of _parse_visual_profile (add/edit routes)."""
    return _parse_visual_profile(request.form.items(), stage_name)


def _parse_zinc_profile(items, stage_name):
    """Read the Zinc coating-mass sheet from the form post.

    The lab weighs the sample before (M1) and after (M2) stripping the zinc off
    a known area A, and the TA sheet turns that into a coating mass:

        M = C x (M2 - M1) / A      g/m2

    C (1.2) and A (0.025 m2) come printed on the sheet but stay editable — a
    different sample size is the operator's call, not a constant to bake in.

    M is computed here rather than taken from the browser, and stored beside the
    readings so a later change to the formula cannot silently rewrite what the
    lab actually recorded. Only the two weights count as data: A and C are
    prefilled and would otherwise make every Zinc save look like a measurement.

    Returns (profile_or_None, has_any).
    """
    if stage_name != ProductionStage.name_for_code("zinc"):
        return None, False
    prefix = f"stage_{stage_name}_zinc_"
    raw = {}
    for key, val in items:
        if not key.startswith(prefix):
            continue
        short = key[len(prefix):]
        if short not in ZINC_MASS_KEYS or val in (None, ""):
            continue
        try:
            raw[short] = float(val)
        except (TypeError, ValueError):
            continue
    if raw.get("m1") is None and raw.get("m2") is None:
        return None, False

    profile = {k: raw.get(k) for k in ZINC_MASS_KEYS}
    m1, m2 = profile["m1"], profile["m2"]
    area, c = profile["area"], profile["c"]
    if None not in (m1, m2, area, c) and area:
        profile["mass"] = round(c * (m2 - m1) / area, 2)
    else:
        profile["mass"] = None
    return profile, True


def _read_zinc_profile(stage_name):
    """Form-post variant of _parse_zinc_profile (add/edit routes)."""
    return _parse_zinc_profile(request.form.items(), stage_name)


RING_INPUT_KEYS = ("force", "od_initial", "od_final")
RING_KEYS = RING_INPUT_KEYS + ("od_diff", "deflection")


def _parse_ring_profile(items, stage_name):
    """Read the Cutting ring-deflection sheet from the form post.

    A ring cut off the pipe is squeezed under a known force and the outside
    diameter is measured before and after:

        Difference in OD = Initial OD - Final OD      mm
        % Deflection     = Difference / Initial OD    x 100

    Both derived rows are computed here rather than taken from the browser,
    and stored beside the readings so a later change to the formula cannot
    silently rewrite what the lab actually recorded. Only a diameter counts as
    data — a force on its own is not a ring test.

    Returns (profile_or_None, has_any).
    """
    if stage_name != ProductionStage.name_for_code("cutting"):
        return None, False
    prefix = f"stage_{stage_name}_ring_"
    raw = {}
    for key, val in items:
        if not key.startswith(prefix):
            continue
        short = key[len(prefix):]
        if short not in RING_INPUT_KEYS or val in (None, ""):
            continue
        try:
            raw[short] = float(val)
        except (TypeError, ValueError):
            continue
    if raw.get("od_initial") is None and raw.get("od_final") is None:
        return None, False

    profile = {k: raw.get(k) for k in RING_INPUT_KEYS}
    initial, final = profile["od_initial"], profile["od_final"]
    if None not in (initial, final):
        diff = initial - final
        profile["od_diff"] = round(diff, 2)
        profile["deflection"] = round(diff / initial * 100, 2) if initial else None
    else:
        profile["od_diff"] = None
        profile["deflection"] = None
    return profile, True


def _read_ring_profile(stage_name):
    """Form-post variant of _parse_ring_profile (add/edit routes)."""
    return _parse_ring_profile(request.form.items(), stage_name)


def _apply_warehouse_barcode(pipe, raw):
    """Set the pipe's warehouse barcode from the Finish row. Returns an error
    string, or None on success.

    The field sits in the Finish stage row but the column lives on Pipe: the
    number identifies the pipe, is unique across the plant, and is the key the
    storekeeper scans. Keeping it off PipeStage also keeps it clear of
    update_stage, which blanks any stage field a console form does not post.

    A blank input clears the code — blanking a mistyped barcode is a
    legitimate edit, and refusing it would strand the pipe.
    """
    value, error = clean_barcode(raw)
    if error:
        return error
    if value == pipe.warehouse_barcode:
        return None
    if value is not None:
        clash = Pipe.query.filter(
            Pipe.warehouse_barcode == value, Pipe.id != pipe.id
        ).first()
        if clash is not None:
            return (
                f"Barcode {value} already belongs to pipe "
                f"{clash.pipe_code or clash.no_code or clash.id}"
            )
    pipe.warehouse_barcode = value
    return None


def _read_stage_extras(stage_name):
    """Read Delivery-row fields (date/customer/receipt/sales order) and the
    Finish bundle from the inline stage table in the add/edit form.

    Returns (extras, has_any): extras maps PipeStage column names to raw form
    strings; has_any is True when the user typed anything, so callers can
    include it in their "should this stage row exist" gate.
    """
    extras = {}
    if stage_name == ProductionStage.name_for_code("delivery"):
        extras["delivery_date"] = request.form.get(f"stage_{stage_name}_date") or ""
        extras["delivery_customer"] = request.form.get(f"stage_{stage_name}_customer") or ""
        extras["delivery_receipt"] = request.form.get(f"stage_{stage_name}_receipt") or ""
        extras["sales_order"] = request.form.get(f"stage_{stage_name}_sales_order") or ""
    elif stage_name == ProductionStage.name_for_code("finish"):
        extras["bundle_number"] = request.form.get(f"stage_{stage_name}_bundle_number") or ""
    return extras, any(extras.values())


def _apply_stage_extras(stage, extras):
    """Write extras from _read_stage_extras onto a PipeStage row.
    Blank strings become None (consistent with update_stage's treatment)."""
    for col, raw in extras.items():
        if col == "delivery_date":
            if raw:
                try:
                    stage.delivery_date = date.fromisoformat(raw)
                except ValueError:
                    pass
        else:
            setattr(stage, col, raw or None)


def next_annealing_batch(today=None):
    """Next date-based Annealing batch number: ANN-YYYYMMDD-N.

    N is 1 + the highest suffix already used for that date, so the sequence
    resets daily. Foreign values on the shared bundle_number column (legacy
    numbers, Finish/Delivery bundles) never affect the count.
    """
    today = today or date.today()
    prefix = f"ANN-{today:%Y%m%d}-"
    seq = 0
    rows = (
        db.session.query(PipeStage.bundle_number)
        .filter(
            PipeStage.stage_name == ProductionStage.name_for_code("annealing"),
            PipeStage.bundle_number.isnot(None),
            PipeStage.bundle_number.like(f"{prefix}%"),
        )
        .all()
    )
    for (bn,) in rows:
        try:
            seq = max(seq, int(bn[len(prefix):]))
        except (ValueError, TypeError):
            continue
    return f"{prefix}{seq + 1}"


def _auto_stamp_annealing(stage):
    """System-set the Annealing batch/date/time. Fill-only: a value that
    already exists is never renumbered or re-stamped, so re-saving the row
    (any route) preserves the original stamp. The batch prefix follows the
    row's own stage_date so a late entry lands on the right day."""
    from datetime import datetime as _dt

    if not stage.stage_date:
        stage.stage_date = _dt.now().date()
    if not stage.stage_time:
        stage.stage_time = _dt.now().time().replace(microsecond=0)
    if not stage.bundle_number:
        stage.bundle_number = next_annealing_batch(stage.stage_date)


def get_stage_defects_from_db():
    """Get stage defects from database, grouped by stage"""
    defects = (
        StageDefectType.query.filter_by(is_active=True)
        .order_by(StageDefectType.stage_name, StageDefectType.sort_order)
        .all()
    )

    stage_defects = {}
    for defect in defects:
        if defect.stage_name not in stage_defects:
            stage_defects[defect.stage_name] = []
        stage_defects[defect.stage_name].append(
            (defect.defect_name_en, defect.defect_name_ar)
        )

    return stage_defects


def get_stage_decisions_from_db():
    """Get stage decisions from database, grouped by stage"""
    decisions = (
        StageDecisionType.query.filter_by(is_active=True)
        .order_by(StageDecisionType.stage_name, StageDecisionType.sort_order)
        .all()
    )

    stage_decisions = {}
    for decision in decisions:
        if decision.stage_name not in stage_decisions:
            stage_decisions[decision.stage_name] = []
        stage_decisions[decision.stage_name].append(
            (decision.decision_name_en, decision.decision_name_ar)
        )

    # Fallback: any active stage with no configured decision rows falls back to
    # the built-in defaults, so a newly-added built-in stage (e.g. Lab Approval)
    # always has options in the edit modal even before an admin configures them.
    for stage_name in ProductionStage.active_names():
        if not stage_decisions.get(stage_name):
            defaults = PipeStage.STAGE_DECISIONS.get(stage_name)
            if defaults:
                # NB: the module-level route `list()` shadows the builtin here,
                # so copy via slice, not list().
                stage_decisions[stage_name] = defaults[:]

    return stage_decisions


def get_stage_machines_from_db():
    """Get machines from database, grouped by stage"""
    machines = (
        Machine.query.filter_by(is_active=True)
        .order_by(Machine.stage, Machine.machine_code)
        .all()
    )

    stage_machines = {}
    for machine in machines:
        if machine.stage and machine.stage not in stage_machines:
            stage_machines[machine.stage] = []
        if machine.stage:
            stage_machines[machine.stage].append(
                {
                    "id": machine.id,
                    "code": machine.machine_code,
                    "name": machine.machine_name or machine.machine_code,
                }
            )

    return stage_machines


# Columns the "Group by" select may use, mapped to Pipe attributes.
GROUP_BY_COLUMNS = {
    "diameter": Pipe.diameter,
    "pipe_class": Pipe.pipe_class,
    "shift": Pipe.shift,
    "final_decision": Pipe.final_decision_value,
    "production_order": Pipe.production_order_id,
    "ladle": Pipe.ladle_id,
}


def _filtered_query(args):
    """Build the filtered Pipe query shared by list() and export_excel().

    Applies every list filter (common + advanced) from the given args mapping.
    Returns an unordered, unpaginated query so callers decide ordering/paging.
    """
    diameter = args.get("diameter", type=int)
    pipe_class = args.get("pipe_class")
    production_order_id = args.get("production_order_id", type=int)
    ladle_id = args.get("ladle_id")
    shift = args.get("shift", type=int)
    final_decision = args.get("final_decision")
    machine_id = args.get("machine_id", type=int)
    mold_number = args.get("mold_number")
    no_code = args.get("no_code")
    pipe_code = args.get("pipe_code")
    customer = args.get("customer")
    sales_order = args.get("sales_order")
    date_from = args.get("date_from")
    date_to = args.get("date_to")
    # Advanced
    weight_min = args.get("weight_min", type=float)
    weight_max = args.get("weight_max", type=float)
    mechanical_test_role = args.get("mechanical_test_role")
    product_id = args.get("product_id", type=int)
    stage = args.get("stage")
    pipe_status = args.get("pipe_status")
    responsible = args.get("responsible")
    delivery_receipt = args.get("delivery_receipt")
    bundle = args.get("bundle")
    hour_from = args.get("hour_from")
    hour_to = args.get("hour_to")
    stage_date = args.get("stage_date")
    stage_hour_from = args.get("stage_hour_from")
    stage_hour_to = args.get("stage_hour_to")
    approved_from = args.get("approved_from")
    approved_to = args.get("approved_to")
    delivery_date_from = args.get("delivery_date_from")
    delivery_date_to = args.get("delivery_date_to")

    query = Pipe.query

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if diameter:
        query = query.filter(Pipe.diameter == diameter)
    if pipe_class:
        query = query.filter(Pipe.pipe_class == pipe_class)
    if production_order_id:
        query = query.filter(Pipe.production_order_id == production_order_id)
    if ladle_id:
        query = query.filter(Pipe.ladle_id == ladle_id)
    if shift:
        query = query.filter(Pipe.shift == shift)
    if final_decision:
        query = query.filter(Pipe.final_decision_value == final_decision)
    if machine_id:
        # Machines are recorded per production stage (PipeStage.machine_id),
        # not on the pipe itself (Pipe.machine_id is unused/NULL), so match
        # pipes that passed through this machine in ANY stage.
        query = query.filter(Pipe.stages.any(PipeStage.machine_id == machine_id))
    if mold_number:
        query = query.filter(Pipe.mold_number == mold_number)
    if no_code:
        query = query.filter(Pipe.no_code.ilike(f"%{no_code}%"))
    if pipe_code:
        like = f"%{pipe_code}%"
        query = query.filter(db.or_(
            Pipe.pipe_code.ilike(like),
            Pipe.no_code.ilike(like),
        ))
    if weight_min is not None:
        query = query.filter(Pipe.actual_weight >= weight_min)
    if weight_max is not None:
        query = query.filter(Pipe.actual_weight <= weight_max)
    if mechanical_test_role:
        query = query.filter(Pipe.mechanical_test_role == mechanical_test_role)
    if product_id:
        query = query.filter(Pipe.product_id == product_id)
    if stage and not (stage_date or stage_hour_from or stage_hour_to):
        # Match pipes that have a PipeStage record for the requested stage.
        # (When stage timing filters are set, stage_name folds into the SAME
        # any() below — separate any() calls could match different rows.)
        query = query.filter(Pipe.stages.any(PipeStage.stage_name == stage))
    if stage_date or stage_hour_from or stage_hour_to:
        # Stage-entry timing: e.g. "which pipes entered Annealing today
        # between 08:00 and 12:00". stage_time is the recorded entry time;
        # most rows predate time entry (3/387 set), so fall back to the
        # row's last-update clock time — when the reading was actually saved.
        conds = []
        if stage:
            conds.append(PipeStage.stage_name == stage)
        if stage_date:
            try:
                conds.append(PipeStage.stage_date == date.fromisoformat(stage_date))
            except ValueError:
                pass
        if stage_hour_from or stage_hour_to:
            t_from = f"{stage_hour_from}:00" if stage_hour_from else "00:00:00"
            t_to = f"{stage_hour_to}:59" if stage_hour_to else "23:59:59"
            eff_time = db.func.coalesce(
                db.func.time(PipeStage.stage_time),
                db.func.time(PipeStage.updated_at),
            )
            conds.append(eff_time >= t_from)
            conds.append(eff_time <= t_to)
        if conds:
            query = query.filter(Pipe.stages.any(db.and_(*conds)))
    if approved_from or approved_to:
        # Approval timing: when a decision was recorded on a stage, taken from
        # the stage history (changed_at is the real decision moment; the stage
        # row's updated_at moves on ANY edit). Honors the stage dropdown.
        h_conds = [
            PipeStageHistory.decision.isnot(None),
            PipeStageHistory.decision != "",
        ]
        if stage:
            h_conds.append(PipeStageHistory.stage_name == stage)
        try:
            if approved_from:
                h_conds.append(
                    PipeStageHistory.changed_at >= datetime.fromisoformat(approved_from)
                )
            if approved_to:
                h_conds.append(
                    PipeStageHistory.changed_at <= datetime.fromisoformat(approved_to)
                )
        except ValueError:
            pass
        approved_pipe_ids = db.session.query(PipeStageHistory.pipe_id).filter(*h_conds)
        query = query.filter(Pipe.id.in_(approved_pipe_ids))
    if pipe_status:
        # Lab decision state (WAITING/ACCEPT/HOLD/FROZEN/BLOCKED/REJECT) —
        # distinct from the final_decision filter above.
        query = query.filter(Pipe.lab_decision == pipe_status)
    if responsible:
        p = f"%{responsible}%"
        # Match the pipe-level shift engineer OR any stage's shift responsible.
        query = query.filter(
            db.or_(
                Pipe.shift_engineer.ilike(p),
                Pipe.stages.any(PipeStage.shift_responsible.ilike(p)),
            )
        )
    if delivery_receipt:
        p = f"%{delivery_receipt}%"
        query = query.filter(
            Pipe.stages.any(
                db.and_(
                    PipeStage.stage_name == ProductionStage.name_for_code("delivery"),
                    PipeStage.delivery_receipt.ilike(p),
                )
            )
        )
    if delivery_date_from or delivery_date_to:
        # Delivery date range — the Delivery stage's own delivery_date column.
        dd_conds = [PipeStage.delivery_date.isnot(None)]
        try:
            if delivery_date_from:
                dd_conds.append(
                    PipeStage.delivery_date >= date.fromisoformat(delivery_date_from)
                )
            if delivery_date_to:
                dd_conds.append(
                    PipeStage.delivery_date <= date.fromisoformat(delivery_date_to)
                )
        except ValueError:
            pass
        query = query.filter(Pipe.stages.any(db.and_(*dd_conds)))
    if bundle:
        query = query.filter(
            Pipe.stages.any(PipeStage.bundle_number.ilike(f"%{bundle}%"))
        )
    if hour_from or hour_to:
        # Hour-of-day range. Pipe.created_at is the only timestamp every pipe
        # has (production_date is date-only); stage_time is sparsely populated
        # but matched too so recorded stage times also surface.
        t_from = f"{hour_from}:00" if hour_from else "00:00:00"
        t_to = f"{hour_to}:59" if hour_to else "23:59:59"
        created_time = db.func.time(Pipe.created_at)
        query = query.filter(
            db.or_(
                db.and_(created_time >= t_from, created_time <= t_to),
                Pipe.stages.any(
                    db.and_(
                        PipeStage.stage_time.isnot(None),
                        db.func.time(PipeStage.stage_time) >= t_from,
                        db.func.time(PipeStage.stage_time) <= t_to,
                    )
                ),
            )
        )
    if customer:
        query = query.join(
            ProductionOrder, Pipe.production_order_id == ProductionOrder.id
        ).filter(ProductionOrder.customer_name.ilike(f"%{customer}%"))
    if sales_order:
        # Sales order lives in two places: the production order's sales_number
        # and the Delivery stage's sales_order field. Match either — an inner
        # join alone would drop pipes whose order link is missing.
        p = f"%{sales_order}%"
        query = query.filter(
            db.or_(
                Pipe.production_order.has(ProductionOrder.sales_number.ilike(p)),
                Pipe.stages.any(PipeStage.sales_order.ilike(p)),
            )
        )

    return query


def _ladle_decisions_for(pipe_list):
    """Map ladle_id -> Arabic chemical decision for the given pipes."""
    ladle_ids = [p.ladle_id for p in pipe_list if p.ladle_id]
    if not ladle_ids:
        return {}
    analyses = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.ladle_id.in_(ladle_ids)
    ).all()
    return {a.ladle_id: a.decision for a in analyses}


@stages_bp.route("/")
@login_required
@requires_permission('stages', 'list')
def list():
    """List all pipes with stage status"""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    group_by = request.args.get("group_by")

    query = _filtered_query(request.args)

    # Get filter options for dropdowns
    from app.models.production_order import ProductionOrder
    from app.models.chemical import Machine
    from app.models.product import Product

    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.product_code)
        .all()
    )

    production_orders = (
        ProductionOrder.query.filter(
            ProductionOrder.status.in_(["pending", "in_progress", "completed"])
        )
        .order_by(ProductionOrder.order_number.desc())
        .limit(50)
        .all()
    )

    machines = Machine.query.filter_by(is_active=True).all()

    # Get unique diameters and classes
    diameters = (
        db.session.query(Pipe.diameter)
        .distinct()
        .filter(Pipe.diameter.isnot(None))
        .order_by(Pipe.diameter)
        .all()
    )
    diameters = [d[0] for d in diameters]

    classes = (
        db.session.query(Pipe.pipe_class)
        .distinct()
        .filter(Pipe.pipe_class.isnot(None))
        .order_by(Pipe.pipe_class)
        .all()
    )
    classes = [c[0] for c in classes]

    pipes = None
    grouped = None
    ladle_analyses = {}

    if group_by and group_by in GROUP_BY_COLUMNS:
        # Grouped mode: no pagination, fetch all rows ordered by the group column.
        group_col = GROUP_BY_COLUMNS[group_by]
        rows = query.order_by(
            group_col, Pipe.production_date.desc(), Pipe.ladle_id, Pipe.arrange_pipe.asc()
        ).all()
        ladle_decisions = _ladle_decisions_for(rows)

        # When grouping by ladle, surface each ladle's chemical analysis so the
        # group header can show the chemistry + link to record the lab decision.
        if group_by == "ladle":
            ladle_ids = [r.ladle_id for r in rows if r.ladle_id]
            if ladle_ids:
                ladle_analyses = {
                    a.ladle_id: a
                    for a in ChemicalAnalysis.query.filter(
                        ChemicalAnalysis.ladle_id.in_(ladle_ids)
                    ).all()
                }

        # Resolve production_order ids to order numbers for labels.
        order_labels = {}
        if group_by == "production_order":
            order_labels = {
                o.id: o.order_number for o in ProductionOrder.query.all()
            }

        def _label(pipe):
            val = getattr(pipe, group_col.key)
            if val is None or val == "":
                return "—"
            if group_by == "production_order":
                return order_labels.get(val, str(val))
            if group_by == "diameter":
                return f"DN{val}"
            if group_by == "shift":
                return str(val)
            return str(val)

        grouped = []
        for row in rows:
            label = _label(row)
            if grouped and grouped[-1][0] == label:
                grouped[-1][1].append(row)
            else:
                grouped.append((label, [row]))
    else:
        pipes = query.order_by(
            Pipe.production_date.desc(), Pipe.ladle_id, Pipe.arrange_pipe.asc()
        ).paginate(page=page, per_page=per_page)
        ladle_decisions = _ladle_decisions_for(pipes.items)

    from app.services import export_service
    picker_columns = export_service.picker_meta(
        _stages_columns({}, {}, {}, _pipe_stage_names()))
    return render_template(
        "stages/list.html",
        picker_columns=picker_columns,
        pipes=pipes,
        grouped=grouped,
        group_by=group_by or "",
        production_orders=production_orders,
        products=products,
        machines=machines,
        diameters=diameters,
        classes=classes,
        ladle_decisions=ladle_decisions,
        ladle_analyses=ladle_analyses,
        get_decision_color=get_decision_color,
    )


_PIPE_ELEMENTS = [
    ("C", "carbon"), ("Si", "silicon"), ("Mn", "manganese"),
    ("P", "phosphorus"), ("S", "sulfur"), ("Mg", "magnesium"),
    ("Cu", "copper"), ("Cr", "chromium"), ("Pb", "lead"),
    ("Al", "aluminum"), ("CE", "carbon_equivalent"),
]


def _stages_columns(analyses, stages_by_pipe, ladle_decisions, stage_names):
    """Full stages/pipes field registry: ``[(key, label, value_fn, default)]``.

    Default set = base list columns + all chemical elements + per-stage
    decisions (preserving the rich default export). Extra pipe attributes are
    available via the picker (default off). For the picker, call with empty
    dicts — only keys/labels/default flags are read there.
    """
    def _el(attr):
        def f(p):
            a = analyses.get(p.ladle_id)
            v = getattr(a, attr, None) if a else None
            return v if v is not None else ""
        return f

    def _stage(sn):
        def f(p):
            st = stages_by_pipe.get(p.id, {}).get(sn)
            return (st.decision or "") if st else ""
        return f

    cols = [
        ("no_code", "No.Code", lambda p: p.no_code, True),
        ("ladle_id", "Ladle ID", lambda p: p.ladle_id or "", True),
        ("ladle_decision", "Ladle Decision", lambda p: ladle_decisions.get(p.ladle_id, "") or "", True),
        ("order", "Order", lambda p: p.arrange_pipe if p.arrange_pipe is not None else "", True),
        ("dn", "DN", lambda p: p.diameter if p.diameter is not None else "", True),
        ("type", "Class", lambda p: p.pipe_class or "", True),
        ("weight", "Weight", lambda p: p.actual_weight if p.actual_weight is not None else "", True),
        ("date", "Date", lambda p: str(p.production_date) if p.production_date else "", True),
        ("shift", "Shift", lambda p: p.shift if p.shift is not None else "", True),
        ("lab", "Lab Decision", lambda p: p.lab_decision or "", True),
        ("final", "Final Decision", lambda p: p.final_decision_value or "", True),
    ]
    cols += [("el_%s" % label, label, _el(attr), True) for label, attr in _PIPE_ELEMENTS]
    cols += [("stage_%d" % i, sn, _stage(sn), True) for i, sn in enumerate(stage_names, start=1)]
    # --- extra pipe fields (default off) ---
    cols += [
        ("manufacturing_order", "Mfg Order", lambda p: p.manufacturing_order or "", False),
        ("mold_number", "Mold #", lambda p: p.mold_number or "", False),
        ("thickness", "Thickness", lambda p: p.thickness, False),
        ("thickness_socket_2", "Thk Socket 2", lambda p: p.thickness_socket_2, False),
        ("thickness_spigot", "Thk Spigot", lambda p: p.thickness_spigot, False),
        ("thickness_spigot_2", "Thk Spigot 2", lambda p: p.thickness_spigot_2, False),
        ("iso_weight", "ISO Weight", lambda p: p.iso_weight, False),
        ("shift_engineer", "Shift Engineer", lambda p: p.shift_engineer or "", False),
        ("lab_decision_reason", "Lab Reason", lambda p: p.lab_decision_reason or "", False),
        ("final_decision_reason", "Final Reason", lambda p: p.final_decision_reason or "", False),
        ("cascade_from", "Cascade From", lambda p: p.cascade_from or "", False),
        ("created_at", "Created", lambda p: str(p.created_at) if p.created_at else "", False),
    ]
    return cols


def _pipe_stage_names():
    return [s for s in Pipe.STAGES if s not in ("Melting Ladle", "Lab Approval")]


@stages_bp.route("/export.xlsx")
@login_required
@requires_permission('stages', 'list')
def export_excel():
    """Export/print the filtered pipe list, respecting current filters.

    Honours ``cols=``, ``ids=`` and ``format=print`` from the Table Tools
    picker. Default set preserves the rich export (base + chemical elements +
    per-stage decisions); extra pipe fields are available via the picker.
    """
    from app.services import export_service

    query = (
        _filtered_query(request.args)
        .order_by(Pipe.production_date.desc(), Pipe.ladle_id, Pipe.arrange_pipe.asc())
    )
    query = export_service.filter_ids(query, Pipe.id, request.args.get("ids"))
    rows = query.all()
    ladle_decisions = _ladle_decisions_for(rows)

    ladle_ids = [p.ladle_id for p in rows if p.ladle_id]
    analyses = {}
    if ladle_ids:
        for a in ChemicalAnalysis.query.filter(ChemicalAnalysis.ladle_id.in_(ladle_ids)).all():
            analyses[a.ladle_id] = a
    stages_by_pipe = {}
    pipe_ids = [p.id for p in rows]
    if pipe_ids:
        for s in PipeStage.query.filter(PipeStage.pipe_id.in_(pipe_ids)).all():
            stages_by_pipe.setdefault(s.pipe_id, {})[s.stage_name] = s

    columns = _stages_columns(analyses, stages_by_pipe, ladle_decisions, _pipe_stage_names())
    selected = export_service.resolve_columns(columns, request.args.get("cols"))
    if request.args.get("format") == "print":
        return export_service.build_print("Pipes", selected, rows)
    return export_service.build_xlsx("Pipes", selected, rows, "pipes.xlsx")


@stages_bp.route("/add", methods=["GET", "POST"])
@login_required
@requires_permission('stages', 'add')
def add():
    """Add new pipe"""
    if not current_user.can_edit:
        flash("You do not have permission to add records.", "error")
        return redirect(url_for("stages.list"))

    if request.method == "POST":
        try:
            pipe = Pipe()

            no_code, no_code_error = normalize_no_code(request.form["no_code"])
            if no_code_error:
                flash(no_code_error, "error")
                return redirect(url_for("stages.add"))

            # Check for duplicate pipe - STRICT: no_code must be globally unique
            existing = Pipe.query.filter_by(no_code=no_code).first()
            if existing:
                flash(
                    f"Pipe with No. Code '{no_code}' already exists (ID: {existing.id}, Ladle: {existing.ladle_id or 'N/A'}). Each pipe code must be unique across all production orders.",
                    "error",
                )
                return redirect(url_for("stages.add"))

            # Production info
            pipe.production_date = date.fromisoformat(request.form["production_date"])

            # Auto-determine shift based on time if not provided
            shift_value = request.form.get("shift")
            if shift_value:
                pipe.shift = int(shift_value)
            else:
                # Auto-calculate shift based on current hour
                from datetime import datetime

                hour = datetime.now().hour
                if 8 <= hour < 16:
                    pipe.shift = 1
                elif 16 <= hour < 24:
                    pipe.shift = 2
                else:
                    pipe.shift = 3

            pipe.shift_engineer = request.form.get("shift_engineer")
            pipe.manufacturing_order = request.form.get("manufacturing_order")

            # Link to production order
            production_order_id = request.form.get("production_order_id")
            if production_order_id:
                pipe.production_order_id = int(production_order_id)

            # Link to product
            product_id = request.form.get("product_id")
            if product_id:
                pipe.product_id = int(product_id)

            # Pipe identification
            pipe.pipe_code = request.form.get("pipe_code")
            # The warehouse barcode is typed into the Finish stage row but is
            # a pipe-level identity, not a stage reading.
            barcode_error = _apply_warehouse_barcode(
                pipe, request.form.get("finish_barcode")
            )
            if barcode_error:
                flash(barcode_error, "error")
                db.session.rollback()
                return redirect(url_for("stages.add"))
            pipe.diameter = int(request.form.get("diameter") or 0)
            pipe.pipe_class = request.form.get("pipe_class")
            pipe.machine_id = (
                int(request.form.get("machine_id"))
                if request.form.get("machine_id")
                else None
            )
            pipe.mold_number = request.form.get("mold_number")
            pipe.iso_weight = float(request.form.get("iso_weight") or 0)
            pipe.no_code = no_code
            pipe.arrange_pipe = int(request.form.get("arrange_pipe") or 1)

            # Link to chemical analysis
            pipe.ladle_id = (request.form.get("ladle_id") or "").strip() or None

            # Ladle is mandatory (DrAlaa 2026-08-10 — a ladle-less pipe like
            # P1112 can never get a chemical/mechanical decision flow).
            if not pipe.ladle_id:
                flash(
                    "Ladle ID is required — a pipe cannot be registered without a ladle.",
                    "error",
                )
                return redirect(url_for("stages.add"))

            # A ladle that already has a mechanical test is closed: registering
            # a new pipe into it would dodge the sample/cascade decision tree
            # (DrAlaa 2026-08-10).
            from app.models.mechanical import MechanicalTest

            ladle_tested = (
                MechanicalTest.query.filter_by(
                    ladle_id=pipe.ladle_id, status="ACTIVE"
                ).first()
                is not None
            )
            if ladle_tested:
                flash(
                    f"Ladle '{pipe.ladle_id}' already has a mechanical test — new pipes can no longer be registered into it.",
                    "error",
                )
                return redirect(url_for("stages.add"))

            # Server-side authority: never store a duplicate order within a
            # ladle. The form JS pre-fills max+1, but two forms open at once
            # can race and post the same number (p1/p2 both #1 incident).
            if pipe.ladle_id:
                conflict = Pipe.query.filter_by(
                    ladle_id=pipe.ladle_id, arrange_pipe=pipe.arrange_pipe
                ).first()
                if conflict:
                    max_arrange = (
                        db.session.query(db.func.max(Pipe.arrange_pipe))
                        .filter(Pipe.ladle_id == pipe.ladle_id)
                        .scalar()
                    ) or 0
                    pipe.arrange_pipe = max_arrange + 1
                    # Only tell the operator when THEY picked the taken number.
                    # An auto-suggested order that went stale (form open a while,
                    # two forms at once, OCR autofill) is the app's own race, not
                    # a data-entry mistake — bump it quietly.
                    if request.form.get("arrange_pipe_auto") != "1":
                        flash(
                            f"Pipe Order was already taken by '{conflict.no_code}'; assigned next available #{pipe.arrange_pipe}.",
                            "warning",
                        )

            # Regenerate the pipe code from the FINAL order. The form's readonly
            # code is built client-side and can carry a stale order: the ladle
            # API sets arrange_pipe via `.value =` without firing the JS 'input'
            # event (so generatePipeCode never re-runs), and the dup-order guard
            # above may have just bumped it. Rebuild server-side at create so the
            # stored code always matches arrange_pipe. Convention:
            # {no_code}-{order}-{ladle}.
            if pipe.no_code and pipe.ladle_id:
                pipe.pipe_code = f"{pipe.no_code}-{pipe.arrange_pipe}-{pipe.ladle_id}"

            # Measurements
            pipe.thickness = (
                float(request.form.get("thickness") or 0)
                if request.form.get("thickness")
                else None
            )
            pipe.thickness_socket_2 = (
                float(request.form.get("thickness_socket_2") or 0)
                if request.form.get("thickness_socket_2")
                else None
            )
            pipe.thickness_spigot = (
                float(request.form.get("thickness_spigot") or 0)
                if request.form.get("thickness_spigot")
                else None
            )
            pipe.thickness_spigot_2 = (
                float(request.form.get("thickness_spigot_2") or 0)
                if request.form.get("thickness_spigot_2")
                else None
            )
            pipe.actual_weight = (
                float(request.form.get("actual_weight") or 0)
                if request.form.get("actual_weight")
                else None
            )

            # Metadata
            pipe.created_by_id = current_user.id
            pipe.modified_by_id = current_user.id

            db.session.add(pipe)
            db.session.commit()

            # Process stage data from form
            stage_names = ProductionStage.active_names()
            blocked_stages = []
            for stage_name in stage_names:
                decision = request.form.get(f"stage_{stage_name}_decision")
                machine_id = request.form.get(f"stage_{stage_name}_machine_id")
                reason = request.form.get(f"stage_{stage_name}_reason")
                defect_type = request.form.get(f"stage_{stage_name}_defect_type")
                defect_reason = request.form.get(f"stage_{stage_name}_defect_reason")
                notes = request.form.get(f"stage_{stage_name}_notes")

                # Get Annealing-specific fields (date and time)
                stage_date_str = request.form.get(f"stage_{stage_name}_date")
                stage_time_str = request.form.get(f"stage_{stage_name}_time")

                # Get Finish-specific field (length)
                length_value = request.form.get(f"stage_{stage_name}_length")

                # Get CCM-specific field (casting temperature).
                # Read is CCM-gated so it never picks up the legacy
                # stage_Annealing_temperature input, which shares the name
                # pattern but is bound to measurement_value.
                temperature_value = (
                    request.form.get(f"stage_{stage_name}_temperature")
                    if stage_name == ProductionStage.name_for_code("ccm")
                    else None
                )

                # Generic measurement — rendered for every stage row that has
                # no specialized field, so any (incl. custom) stage can store
                # a value in the database.
                measurement_form_value = request.form.get(
                    f"stage_{stage_name}_measurement"
                )

                # Per-meter cement/coating thickness grid (Coating row only)
                thickness_profile, has_thickness = _read_thickness_profile(stage_name)

                # CCM dimension grids (thickness 1-21 + diameter D1-D15)
                dimension_profile, has_dimensions = _read_dimension_profile(stage_name)

                # Annealing ovality grid (ID + D1..D15)
                ovality_profile, has_ovality = _read_ovality_profile(stage_name)

                # Finish visual-inspection checklist (5 items)
                visual_profile, has_visual = _read_visual_profile(stage_name)

                # Zinc coating-mass sheet (M2/M1/A/C -> M g/m2)
                zinc_profile, has_zinc = _read_zinc_profile(stage_name)

                # Cutting ring-deflection test (force / initial OD / final OD)
                ring_profile, has_ring = _read_ring_profile(stage_name)

                # Delivery-row fields (date/customer/receipt/sales order) and
                # the Finish bundle — previously read by NO handler, so they
                # were silently dropped on Save.
                stage_extras, has_extras = _read_stage_extras(stage_name)

                # Sequential gate (Settings > Stage Flow, off by default).
                # Collected rather than raised so one blocked stage does not
                # abandon the whole save — the other stages still go in.
                gate = stage_flow_service.blocking_reason(
                    pipe, stage_name, decision)
                if gate:
                    blocked_stages.append(gate)
                    continue

                # Only create stage record if there's any data
                if (
                    decision
                    or machine_id
                    or reason
                    or defect_type
                    or defect_reason
                    or notes
                    or stage_date_str
                    or length_value
                    or temperature_value
                    or measurement_form_value
                    or has_thickness
                    or has_dimensions
                    or has_ovality
                    or has_visual
                    or has_zinc
                    or has_ring
                    or has_extras
                ):
                    # For the Delivery stage the row's date input IS the
                    # delivery date (extras), not stage_date — don't double-
                    # store it in both columns. Annealing date/time are
                    # editable (the operator may backdate an entry); blanks
                    # are filled by the server stamp below.
                    is_delivery = stage_name == ProductionStage.name_for_code("delivery")
                    is_annealing = stage_name == ProductionStage.name_for_code("annealing")
                    stage = PipeStage(
                        pipe_id=pipe.id,
                        stage_name=stage_name,
                        decision=decision,
                        machine_id=int(machine_id) if machine_id else None,
                        reason=reason,
                        defect_type=defect_type,
                        defect_reason=defect_reason,
                        notes=notes,
                        has_defect=bool(defect_type),
                        stage_date=date.fromisoformat(stage_date_str)
                        if stage_date_str and not is_delivery
                        else date.today(),
                        updated_by_id=current_user.id,
                        approved_by_id=current_user.id if decision else None,
                    )
                    _apply_stage_extras(stage, stage_extras)

                    # Annealing: posted date/time are honored (manual entry);
                    # the server fills any blank and always generates the
                    # batch number itself.
                    if is_annealing:
                        if stage_time_str:
                            # Local alias: add() has a conditional
                            # `from datetime import datetime` in the shift
                            # logic, which shadows the module-level name for
                            # the whole function when that branch doesn't run.
                            from datetime import datetime as _dt

                            stage.stage_time = _dt.strptime(
                                stage_time_str, "%H:%M"
                            ).time()
                        _auto_stamp_annealing(stage)

                    # Set length for Finish
                    if stage_name == ProductionStage.name_for_code("finish") and length_value:
                        stage.measurement_value = float(length_value)
                        stage.measurement_type = "Length"

                    # Set casting temperature for CCM (optional — blank stays NULL)
                    if temperature_value:
                        stage.temperature = float(temperature_value)

                    # Generic measurement for stages without a specialized field
                    if measurement_form_value:
                        stage.measurement_value = float(measurement_form_value)
                        stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(
                            stage_name, stage_name
                        )

                    # Per-meter thickness profile for Coating
                    if thickness_profile is not None:
                        stage.thickness_profile = _merge_thickness_profile(
                            stage.thickness_profile, thickness_profile)

                    # Dimension grids for CCM
                    if dimension_profile is not None:
                        stage.dimension_profile = _merge_dimension_profile(
                            stage.dimension_profile, dimension_profile
                        )

                    # Ovality grid for Annealing
                    if has_ovality:
                        stage.ovality_profile = ovality_profile

                    # Visual checklist for Finish
                    if visual_profile is not None:
                        stage.visual_profile = visual_profile

                    # Zinc coating-mass sheet
                    if has_zinc:
                        stage.zinc_profile = zinc_profile

                    # Cutting ring-deflection test
                    if has_ring:
                        stage.ring_profile = ring_profile

                    db.session.add(stage)

            db.session.commit()

            # Auto-run decision engine if ladle has chemical decision
            if pipe.ladle_id:
                try:
                    from app.services.pipe_decision_service import (
                        assign_mechanical_roles,
                        reapply_mechanical_results,
                        update_lab_stage_decision,
                        update_final_decision,
                    )
                    from app.models.chemical import ChemicalAnalysis as CA

                    analysis = CA.query.filter_by(ladle_id=pipe.ladle_id).first()
                    if analysis and analysis.decision:
                        # Auto-create Melting Ladle stage with chemical decision
                        melting_name = ProductionStage.name_for_code("melting_ladle")
                        melting_stage = PipeStage.query.filter_by(
                            pipe_id=pipe.id, stage_name=melting_name
                        ).first()
                        if not melting_stage:
                            melting_stage = PipeStage(
                                pipe_id=pipe.id,
                                stage_name=melting_name,
                                stage_date=analysis.test_date or date.today(),
                                decision=analysis.decision,
                                reason=analysis.reason,
                                updated_by_id=current_user.id,
                            )
                            db.session.add(melting_stage)
                            db.session.commit()

                        # Re-run role assignment for all pipes in this ladle
                        assign_mechanical_roles(pipe.ladle_id)

                        # assign_mechanical_roles wiped lab_decision to WAITING;
                        # restore it from any tests already recorded so a pipe
                        # add never loses a mechanical result already in.
                        reapply_mechanical_results(pipe.ladle_id)

                        # Refresh pipe after role assignment
                        db.session.refresh(pipe)

                        # Auto-set Lab stage if decision is already determined
                        if pipe.lab_decision and pipe.lab_decision != "WAITING":
                            update_lab_stage_decision(pipe)

                        update_final_decision(pipe)
                except Exception:
                    pass
            else:
                try:
                    from app.services.pipe_decision_service import update_final_decision

                    update_final_decision(pipe)
                except Exception:
                    pass

            # A silently skipped stage is indistinguishable from a save that
            # worked, so every refusal is reported.
            for reason in blocked_stages:
                flash(reason, "warning")

            flash("Pipe added successfully!", "success")
            return redirect(url_for("stages.view", id=pipe.id))

        except IntegrityError as e:
            db.session.rollback()
            flash(
                "Database error: a duplicate value was detected. Please check your input.",
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding pipe: {str(e)}", "error")

    # Check if order_id is passed from production order page
    selected_order_id = request.args.get("order_id", type=int)

    return render_template(
        "stages/form.html",
        **_get_form_context(selected_order_id=selected_order_id),
    )


@stages_bp.route("/<int:id>")
@login_required
@requires_permission('stages', 'list')
def view(id):
    """View pipe tracking through stages"""
    pipe = Pipe.query.get_or_404(id)
    stages_status = pipe.get_all_stages_status()
    defect_types = DefectType.query.filter_by(is_active=True).all()
    decision_types = DecisionType.query.all()

    # Get chemical analysis for this pipe
    chemical_analysis = (
        ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        if pipe.ladle_id
        else None
    )

    # Get mechanical tests for this pipe's ladle (ACTIVE only — superseded retests hidden)
    from app.models.mechanical import MechanicalTest

    mechanical_tests = (
        MechanicalTest.query.filter_by(ladle_id=pipe.ladle_id, status="ACTIVE")
        .order_by(MechanicalTest.test_date.desc(), MechanicalTest.id.desc())
        .all()
        if pipe.ladle_id
        else []
    )

    # Get stage-specific decisions and defects
    stage_decisions = get_stage_decisions_from_db()
    stage_defects = get_stage_defects_from_db()
    stage_machines = get_stage_machines_from_db()

    # Get attachments
    from app.models.attachment import Attachment

    attachments = Attachment.get_for_record("pipes", pipe.id)

    # Change history for this pipe
    from app.models.audit import AuditLog

    audit_entries = (
        AuditLog.query.filter_by(table_name="pipes", record_id=pipe.id)
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
        .all()
    )

    # Compute prev/next pipe for navigation
    prev_pipe = None
    next_pipe = None
    if pipe.production_order_id:
        siblings = (
            Pipe.query.filter_by(production_order_id=pipe.production_order_id)
            .order_by(Pipe.no_code)
            .all()
        )
        for i, p in enumerate(siblings):
            if p.id == pipe.id:
                if i > 0:
                    prev_pipe = siblings[i - 1]
                if i < len(siblings) - 1:
                    next_pipe = siblings[i + 1]
                break

    return render_template(
        "stages/detail.html",
        pipe=pipe,
        chemical_analysis=chemical_analysis,
        mechanical_tests=mechanical_tests,
        stages_status=stages_status,
        defect_types=defect_types,
        decision_types=decision_types,
        stage_decisions=stage_decisions,
        stage_defects=stage_defects,
        stage_machines=stage_machines,
        all_stages=Pipe.STAGES,
        prev_pipe=prev_pipe,
        next_pipe=next_pipe,
        attachments=attachments,
        audit_entries=audit_entries,
        ccm_stage=ProductionStage.name_for_code("ccm"),
        table_name="pipes",
        record_id=pipe.id,
        redirect_url=url_for("stages.view", id=pipe.id),
    )


# Alias for backward compatibility
@stages_bp.route("/tracking/<int:id>")
@login_required
@requires_permission('stages', 'tracking')
def tracking(id):
    """Alias for view - backward compatibility"""
    return redirect(url_for("stages.view", id=id))


@stages_bp.route("/console")
@login_required
@requires_permission('stages', 'edit')
def console():
    """Stage Entry Console — pick a pipe from a searchable list, record its
    stage readings inline, autosave, move to the next pipe.

    Exists because stage data arrives one stage at a time over days: the
    edit-form round trip (open pipe → save → bounced to list → hunt for the
    next pipe) costs more clicks than the data entry itself. Reads nothing new
    and writes through the same ``update_stage`` endpoint the detail page uses,
    so every gate (Zinc lab gate, Delivery gate, decision immutability,
    stage history, final-decision recalc) still applies.
    """
    return render_template(
        "stages/console.html",
        stage_names=ProductionStage.active_names(),
        machines=Machine.query.filter_by(is_active=True).order_by(Machine.machine_code).all(),
        production_orders=(
            ProductionOrder.query.order_by(ProductionOrder.order_number.desc())
            .limit(100)
            .all()
        ),
    )


@stages_bp.route("/api/console-search")
@login_required
@requires_permission('stages', 'edit')
def api_console_search():
    """Pipe picker for the console.

    Same server-side-search shape as the mechanical form's picker (a client-side
    filter over a top-N render can never surface a pipe it never rendered), plus
    a ``pending`` filter that is the console's whole point: show me the pipes
    that still have no reading for THIS stage.

    Params: q, dn, ladle, order, date, stage, pending(1), limit
    """
    q = (request.args.get("q") or "").strip()
    dn = (request.args.get("dn") or "").strip()
    ladle = (request.args.get("ladle") or "").strip()
    order_id = (request.args.get("order") or "").strip()
    pdate = (request.args.get("date") or "").strip()
    stage_name = (request.args.get("stage") or "").strip()
    pending = request.args.get("pending") == "1"

    query = Pipe.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Pipe.pipe_code.ilike(like),
                Pipe.no_code.ilike(like),
                Pipe.ladle_id.ilike(like),
            )
        )
    if dn:
        try:
            query = query.filter(Pipe.diameter == int(dn))
        except ValueError:
            pass
    if ladle:
        query = query.filter(Pipe.ladle_id.ilike(f"%{ladle}%"))
    if order_id:
        try:
            query = query.filter(Pipe.production_order_id == int(order_id))
        except ValueError:
            pass
    if pdate:
        try:
            query = query.filter(
                Pipe.production_date == date.fromisoformat(pdate)
            )
        except ValueError:
            pass

    # "Still pending at this stage" = no PipeStage row, or one with no decision.
    if pending and stage_name:
        decided = (
            db.session.query(PipeStage.pipe_id)
            .filter(
                PipeStage.stage_name == stage_name,
                PipeStage.decision.isnot(None),
                PipeStage.decision != "",
            )
            .distinct()
        )
        query = query.filter(~Pipe.id.in_(decided))

    try:
        limit = min(int(request.args.get("limit") or 200), 500)
    except ValueError:
        limit = 200

    pipes = (
        query.order_by(Pipe.production_date.desc(), Pipe.id.desc()).limit(limit).all()
    )

    # Decision per pipe for the requested stage — one grouped query, no N+1.
    stage_state = {}
    if stage_name and pipes:
        for pid, decision in (
            db.session.query(PipeStage.pipe_id, PipeStage.decision)
            .filter(
                PipeStage.pipe_id.in_([p.id for p in pipes]),
                PipeStage.stage_name == stage_name,
            )
            .all()
        ):
            stage_state[pid] = decision or ""

    return jsonify(
        {
            "count": len(pipes),
            "truncated": len(pipes) >= limit,
            "pipes": [
                {
                    "id": p.id,
                    "code": p.pipe_code
                    or f"{p.no_code}-{p.ladle_id or ''}-{p.arrange_pipe or 1}",
                    "no_code": p.no_code or "",
                    "ladle": p.ladle_id or "",
                    "dn": p.diameter if p.diameter is not None else "",
                    "klass": p.pipe_class or "",
                    "date": p.production_date.isoformat() if p.production_date else "",
                    "blocked": p.lab_decision == "BLOCKED"
                    or p.final_decision_value == "REJECT",
                    "stage_decision": stage_state.get(p.id, ""),
                }
                for p in pipes
            ],
        }
    )


@stages_bp.route("/console/pipe/<int:id>")
@login_required
@requires_permission('stages', 'edit')
def console_pipe(id):
    """HTML partial: the console's right-hand pane for one pipe.

    Rendered server-side on purpose. The per-stage widgets (Annealing date/time,
    CCM temperature, Delivery customer/receipt/sales order, Finish bundle) are
    already expressed in Jinja; re-implementing that matrix in JS would be a
    second source of truth that silently drifts from the real form.
    """
    pipe = Pipe.query.get_or_404(id)
    stage_map = {s.stage_name: s for s in pipe.stages}

    from app.models.defect_reason import DefectReason

    return render_template(
        "stages/_console_pipe.html",
        pipe=pipe,
        stage_map=stage_map,
        stage_names=ProductionStage.active_names(),
        stage_decisions=get_stage_decisions_from_db(),
        stage_defects=get_stage_defects_from_db(),
        stage_machines=get_stage_machines_from_db(),
        defect_reasons=DefectReason.active_by_stage(),
        measurement_labels=PipeStage.MEASUREMENT_TYPES,
        measurement_units=PipeStage.measurement_units_by_stage(),
        ccm_stage=ProductionStage.name_for_code("ccm"),
        coating_stage=ProductionStage.name_for_code("coating"),
        cutting_stage=ProductionStage.name_for_code("cutting"),
        annealing_stage=ProductionStage.name_for_code("annealing"),
        zinc_stage=ProductionStage.name_for_code("zinc"),
        finish_stage=ProductionStage.name_for_code("finish"),
        delivery_stage=ProductionStage.name_for_code("delivery"),
        focus_stage=(request.args.get("stage") or "").strip(),
    )


@stages_bp.route("/<int:id>/stage/<stage_name>", methods=["POST"])
@login_required
@requires_permission('stages', 'edit')
def update_stage(id, stage_name):
    """Update a specific stage for a pipe"""
    if not current_user.can_edit:
        return jsonify({"success": False, "error": "No permission"}), 403

    if stage_name not in Pipe.STAGES:
        return jsonify({"success": False, "error": "Invalid stage"}), 400

    pipe = Pipe.query.get_or_404(id)

    # Block stage updates for REJECTED/BLOCKED pipes
    if pipe.final_decision_value == "REJECT":
        return jsonify(
            {"success": False, "error": "Cannot update stages on a REJECTED pipe"}
        ), 400

    if pipe.lab_decision == "BLOCKED":
        return jsonify(
            {
                "success": False,
                "error": "Cannot update stages - chemical analysis rejected (BLOCKED)",
            }
        ), 400

    # Single gate (per client request — لا تعمّم): only ZINC, the stage right
    # after Lab Approval, is gated by the effective lab decision. Everything
    # downstream of Zinc flows normally. The effective decision is the
    # supervisor's manual Lab Approval override if he set one, else the
    # auto-computed lab result — so his manual Accept unlocks Zinc even when the
    # auto result was WAITING/HOLD/FROZEN.
    from app.services.pipe_decision_service import effective_lab_decision
    eff = effective_lab_decision(pipe)
    if stage_name == ProductionStage.name_for_code("zinc"):
        if eff == "REJECT":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot start Zinc — lab decision is REJECT",
                }
            ), 400
        if eff == "WAITING":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot start Zinc while lab result is pending (WAITING). Record the Lab Approval decision first.",
                }
            ), 400
        if eff == "FROZEN":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot start Zinc — pipe is FROZEN (recoverable mechanical freeze, lift via retest or Lab Approval)",
                }
            ), 400

    # Delivery is the last stage: only allow it once everything passes
    if stage_name == ProductionStage.name_for_code("delivery"):
        if eff == "HOLD":
            return jsonify(
                {"success": False, "error": "Cannot deliver — pipe is on HOLD"}
            ), 400
        if pipe.final_decision_value != "ACCEPT":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot deliver — pipe final decision must be ACCEPT (currently: "
                    + (pipe.final_decision_value or "pending")
                    + ")",
                }
            ), 400

    try:
        # Get or create stage
        stage = PipeStage.query.filter_by(
            pipe_id=pipe.id, stage_name=stage_name
        ).first()
        is_new = stage is None

        if not stage:
            stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
            db.session.add(stage)
            db.session.flush()  # Get ID for history
        else:
            # Decision immutability: once a stage decision is set and approved,
            # only supervisors/admins can change it.
            if (
                stage.decision
                and stage.approved_by_id
                and not current_user.is_supervisor
            ):
                return jsonify(
                    {
                        "success": False,
                        "error": "This stage decision is locked. Only supervisors can modify an approved decision.",
                    }
                ), 403

            # Save history before making changes (only for existing stages)
            history = PipeStageHistory.create_from_stage(
                stage, action="update", user_id=current_user.id
            )
            db.session.add(history)

        # Update stage data
        data = request.get_json() or request.form

        is_annealing = stage_name == ProductionStage.name_for_code("annealing")

        # Annealing date/time are editable — the operator may backdate an
        # entry from the console. Blanks fall through to the fill-only
        # system stamp below; the batch number is always server-generated.
        if not is_annealing:
            stage.stage_date = (
                date.fromisoformat(data.get("stage_date"))
                if data.get("stage_date")
                else date.today()
            )
            if data.get("stage_time"):
                stage.stage_time = datetime.strptime(data["stage_time"], "%H:%M").time()
        else:
            if data.get("stage_date"):
                stage.stage_date = date.fromisoformat(data["stage_date"])
            if data.get("stage_time"):
                stage.stage_time = datetime.strptime(data["stage_time"], "%H:%M").time()
            elif not stage.stage_date:
                stage.stage_date = date.today()

        new_decision = data.get("decision")

        # Optional sequential gate (Settings > Stage Flow, off by default): a
        # decision cannot be recorded before the stage before it was accepted.
        # Checked here rather than at the top of the request because this is
        # where the decision is first known, and only a decision is gated.
        gate = stage_flow_service.blocking_reason(pipe, stage_name, new_decision)
        if gate:
            db.session.rollback()
            return jsonify({"success": False, "error": gate}), 400

        # Track who approved this decision the moment it changes
        if new_decision and new_decision != stage.decision:
            stage.approved_by_id = current_user.id
        stage.decision = new_decision
        stage.reason = data.get("reason")
        stage.has_defect = (
            data.get("has_defect") == "true" or data.get("has_defect") == True
        )
        stage.defect_type_id = (
            int(data.get("defect_type_id")) if data.get("defect_type_id") else None
        )
        stage.defect_type = data.get("defect_type")  # Stage-specific defect
        stage.defect_reason = data.get("defect_reason")
        stage.notes = data.get("notes")

        # Machine used for this stage
        stage.machine_id = (
            int(data.get("machine_id")) if data.get("machine_id") else None
        )

        # Stage-specific measurements
        if data.get("measurement_value"):
            stage.measurement_value = float(data["measurement_value"])
            stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(
                stage_name, stage_name
            )

        # Casting temperature — recorded on the CCM stage (optional)
        if "temperature" in data:
            stage.temperature = (
                float(data["temperature"])
                if data.get("temperature") not in (None, "")
                else None
            )

        # CCM dimension grids — the console posts them as stage_CCM_dim_*
        # keys inside the stage form's JSON payload. Blank keeps the prior
        # profile (same semantics as the edit form).
        dimension_profile, has_dimensions = _parse_dimension_profile(
            data.items(), stage_name
        )
        if dimension_profile is not None:
            stage.dimension_profile = _merge_dimension_profile(
                stage.dimension_profile, dimension_profile
            )

        # Coating per-meter thickness grid — same console pattern as the CCM
        # dimensions above: stage_<Coating>_thick_<layer>_<m> keys in the
        # JSON payload, blank keeps the prior profile.
        thickness_profile, has_thickness = _parse_thickness_profile(
            data.items(), stage_name
        )
        if thickness_profile is not None:
            stage.thickness_profile = _merge_thickness_profile(
                stage.thickness_profile, thickness_profile
            )

        # Annealing ovality grid — same console pattern as the CCM dimensions
        # above: stage_<Annealing>_ov_x_<point> / ov_y_<point> keys in the
        # JSON payload, blank keeps the prior profile.
        ovality_profile, has_ovality = _parse_ovality_profile(
            data.items(), stage_name
        )
        if has_ovality:
            stage.ovality_profile = ovality_profile

        # Finish visual-inspection checklist — stage_<Finish>_visual_<key>=1
        # keys in the JSON payload, blank keeps the prior profile.
        visual_profile, has_visual = _parse_visual_profile(
            data.items(), stage_name
        )
        if visual_profile is not None:
            stage.visual_profile = visual_profile

        # Zinc coating-mass sheet — stage_<Zinc>_zinc_<m2|m1|area|c> keys in the
        # JSON payload, blank keeps the prior profile.
        zinc_profile, has_zinc = _parse_zinc_profile(data.items(), stage_name)
        if has_zinc:
            stage.zinc_profile = zinc_profile

        # Cutting ring test — stage_<Cutting>_ring_<force|od_initial|od_final>
        # keys in the JSON payload, blank keeps the prior profile.
        ring_profile, has_ring = _parse_ring_profile(data.items(), stage_name)
        if has_ring:
            stage.ring_profile = ring_profile

        # Bundle # for Finish and Delivery stages
        if stage_name in (ProductionStage.name_for_code("finish"), ProductionStage.name_for_code("delivery")):
            stage.bundle_number = data.get("bundle_number") or None

        # Warehouse barcode (pipe-level, entered on the Finish row). Only
        # touched when the key is actually present: this endpoint nulls every
        # stage field a form omits, and the barcode must not follow that rule.
        if "finish_barcode" in data:
            barcode_error = _apply_warehouse_barcode(pipe, data.get("finish_barcode"))
            if barcode_error:
                db.session.rollback()
                return jsonify({"success": False, "error": barcode_error}), 400

        # Annealing batch is server-generated; date/time blanks are stamped
        if is_annealing:
            _auto_stamp_annealing(stage)

        # Delivery-specific fields
        if stage_name == ProductionStage.name_for_code("delivery"):
            stage.sales_order = data.get("sales_order")
            stage.delivery_customer = data.get("delivery_customer")
            stage.delivery_receipt = data.get("delivery_receipt")
            if data.get("delivery_date"):
                stage.delivery_date = date.fromisoformat(data["delivery_date"])

        stage.updated_by_id = current_user.id

        # If new stage, save history after creation with initial data
        if is_new:
            db.session.flush()
            history = PipeStageHistory.create_from_stage(
                stage, action="create", user_id=current_user.id
            )
            db.session.add(history)

        db.session.commit()

        # Recalculate final decision after stage update
        try:
            from app.services.pipe_decision_service import update_final_decision

            update_final_decision(pipe)
        except Exception:
            pass

        return jsonify({"success": True, "message": "Stage updated successfully"})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@stages_bp.route("/<int:id>/edit", methods=["GET", "POST"])
@login_required
@requires_permission('stages', 'edit')
def edit_pipe(id):
    """Edit pipe"""
    if not current_user.can_edit:
        flash("ليس لديك صلاحية التعديل", "error")
        return redirect(url_for("stages.list"))

    pipe = Pipe.query.get_or_404(id)

    if request.method == "POST":
        try:
            no_code, no_code_error = normalize_no_code(request.form["no_code"])
            if no_code_error:
                flash(no_code_error, "error")
                return redirect(url_for("stages.edit_pipe", id=id))

            # Production info
            pipe.production_date = date.fromisoformat(request.form["production_date"])

            # Auto-determine shift based on time if not provided
            shift_value = request.form.get("shift")
            if shift_value:
                pipe.shift = int(shift_value)
            else:
                # Auto-calculate shift based on current hour
                from datetime import datetime

                hour = datetime.now().hour
                if 8 <= hour < 16:
                    pipe.shift = 1
                elif 16 <= hour < 24:
                    pipe.shift = 2
                else:
                    pipe.shift = 3

            pipe.shift_engineer = request.form.get("shift_engineer")
            pipe.manufacturing_order = request.form.get("manufacturing_order")

            # Link to production order
            production_order_id = request.form.get("production_order_id")
            if production_order_id:
                pipe.production_order_id = int(production_order_id)
            else:
                pipe.production_order_id = None

            # Link to product
            product_id = request.form.get("product_id")
            if product_id:
                pipe.product_id = int(product_id)
            else:
                pipe.product_id = None

            # Pipe identification
            pipe.pipe_code = request.form.get("pipe_code")
            # The warehouse barcode is typed into the Finish stage row but is
            # a pipe-level identity, not a stage reading.
            barcode_error = _apply_warehouse_barcode(
                pipe, request.form.get("finish_barcode")
            )
            if barcode_error:
                flash(barcode_error, "error")
                db.session.rollback()
                return redirect(url_for("stages.edit_pipe", id=id))
            pipe.diameter = int(request.form.get("diameter") or 0)
            pipe.pipe_class = request.form.get("pipe_class")
            pipe.machine_id = (
                int(request.form.get("machine_id"))
                if request.form.get("machine_id")
                else None
            )
            pipe.mold_number = request.form.get("mold_number")
            pipe.iso_weight = float(request.form.get("iso_weight") or 0)
            pipe.no_code = no_code
            pipe.arrange_pipe = int(request.form.get("arrange_pipe") or 1)

            # Link to chemical analysis
            pipe.ladle_id = request.form.get("ladle_id")

            # Same duplicate-order guard as add(), excluding this pipe itself
            if pipe.ladle_id:
                conflict = Pipe.query.filter(
                    Pipe.ladle_id == pipe.ladle_id,
                    Pipe.arrange_pipe == pipe.arrange_pipe,
                    Pipe.id != pipe.id,
                ).first()
                if conflict:
                    max_arrange = (
                        db.session.query(db.func.max(Pipe.arrange_pipe))
                        .filter(Pipe.ladle_id == pipe.ladle_id)
                        .scalar()
                    ) or 0
                    pipe.arrange_pipe = max_arrange + 1
                    # Only tell the operator when THEY picked the taken number.
                    # An auto-suggested order that went stale (form open a while,
                    # two forms at once, OCR autofill) is the app's own race, not
                    # a data-entry mistake — bump it quietly.
                    if request.form.get("arrange_pipe_auto") != "1":
                        flash(
                            f"Pipe Order was already taken by '{conflict.no_code}'; assigned next available #{pipe.arrange_pipe}.",
                            "warning",
                        )

            # Measurements
            pipe.thickness = (
                float(request.form.get("thickness") or 0)
                if request.form.get("thickness")
                else None
            )
            pipe.thickness_socket_2 = (
                float(request.form.get("thickness_socket_2") or 0)
                if request.form.get("thickness_socket_2")
                else None
            )
            pipe.thickness_spigot = (
                float(request.form.get("thickness_spigot") or 0)
                if request.form.get("thickness_spigot")
                else None
            )
            pipe.thickness_spigot_2 = (
                float(request.form.get("thickness_spigot_2") or 0)
                if request.form.get("thickness_spigot_2")
                else None
            )
            pipe.actual_weight = (
                float(request.form.get("actual_weight") or 0)
                if request.form.get("actual_weight")
                else None
            )

            pipe.modified_by_id = current_user.id

            # Process stage data from form
            stage_names = ProductionStage.active_names()
            delivery_name = ProductionStage.name_for_code("delivery")
            finish_name = ProductionStage.name_for_code("finish")
            annealing_name = ProductionStage.name_for_code("annealing")
            blocked_stages = []
            for stage_name in stage_names:
                decision = request.form.get(f"stage_{stage_name}_decision")
                machine_id = request.form.get(f"stage_{stage_name}_machine_id")
                reason = request.form.get(f"stage_{stage_name}_reason")
                defect_type = request.form.get(f"stage_{stage_name}_defect_type")
                defect_reason = request.form.get(f"stage_{stage_name}_defect_reason")
                notes = request.form.get(f"stage_{stage_name}_notes")

                # CCM casting temperature (CCM-gated — see add route)
                temperature_value = (
                    request.form.get(f"stage_{stage_name}_temperature")
                    if stage_name == ProductionStage.name_for_code("ccm")
                    else None
                )

                # Generic measurement (any stage without a specialized field)
                measurement_form_value = request.form.get(
                    f"stage_{stage_name}_measurement"
                )

                # Stage date/time — only stages whose row renders these inputs
                # (Annealing) post them; blank keeps the existing value.
                stage_date_value = request.form.get(f"stage_{stage_name}_date")
                stage_time_value = request.form.get(f"stage_{stage_name}_time")

                # Specialized rows. Every field the form renders must be read
                # here — an unread input is silently dropped on Save (the
                # 2026-07-26 Delivery bug).
                delivery_date_value = None
                delivery_customer = None
                delivery_receipt = None
                delivery_sales_order = None
                finish_bundle = None
                finish_length = None
                annealing_batch = None
                if stage_name == delivery_name:
                    delivery_date_value = request.form.get("stage_Delivery_date")
                    delivery_customer = request.form.get("stage_Delivery_customer")
                    delivery_receipt = request.form.get("stage_Delivery_receipt")
                    delivery_sales_order = request.form.get("stage_Delivery_sales_order")
                elif stage_name == finish_name:
                    finish_bundle = request.form.get(f"stage_{stage_name}_bundle_number")
                    finish_length = request.form.get(f"stage_{stage_name}_length")
                elif stage_name == annealing_name:
                    annealing_batch = request.form.get(
                        f"stage_{stage_name}_batch_number"
                    )

                # Per-meter cement/coating thickness grid (Coating row only)
                thickness_profile, has_thickness = _read_thickness_profile(stage_name)

                # CCM dimension grids (thickness 1-21 + diameter D1-D15)
                dimension_profile, has_dimensions = _read_dimension_profile(stage_name)

                # Annealing ovality grid (ID + D1..D15)
                ovality_profile, has_ovality = _read_ovality_profile(stage_name)

                # Finish visual-inspection checklist (5 items)
                visual_profile, has_visual = _read_visual_profile(stage_name)

                # Zinc coating-mass sheet (M2/M1/A/C -> M g/m2)
                zinc_profile, has_zinc = _read_zinc_profile(stage_name)

                # Cutting ring-deflection test (force / initial OD / final OD)
                ring_profile, has_ring = _read_ring_profile(stage_name)

                # Sequential gate (Settings > Stage Flow, off by default).
                # Collected rather than raised so one blocked stage does not
                # abandon the whole save — the other stages still go in.
                gate = stage_flow_service.blocking_reason(
                    pipe, stage_name, decision)
                if gate:
                    blocked_stages.append(gate)
                    continue

                # Get or create stage
                stage = PipeStage.query.filter_by(
                    pipe_id=pipe.id, stage_name=stage_name
                ).first()
                is_new = stage is None

                # Only create/update stage record if there's any data
                if (
                    decision
                    or machine_id
                    or reason
                    or defect_type
                    or defect_reason
                    or notes
                    or temperature_value
                    or measurement_form_value
                    or stage_date_value
                    or stage_time_value
                    or delivery_date_value
                    or delivery_customer
                    or delivery_receipt
                    or delivery_sales_order
                    or finish_bundle
                    or finish_length
                    or annealing_batch
                    or has_thickness
                    or has_dimensions
                    or has_ovality
                    or has_visual
                    or has_zinc
                    or has_ring
                ):
                    if not stage:
                        stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
                        db.session.add(stage)
                        db.session.flush()
                    else:
                        # Save history before making changes
                        history = PipeStageHistory.create_from_stage(
                            stage, action="update", user_id=current_user.id
                        )
                        db.session.add(history)

                    # Record approver the moment the decision changes
                    if decision and decision != stage.decision:
                        stage.approved_by_id = current_user.id
                    stage.decision = decision
                    stage.machine_id = int(machine_id) if machine_id else None
                    stage.reason = reason
                    stage.defect_type = defect_type
                    stage.defect_reason = defect_reason
                    stage.notes = notes
                    stage.has_defect = bool(defect_type)
                    stage.stage_date = stage.stage_date or date.today()
                    stage.updated_by_id = current_user.id

                    # Casting temperature for CCM (optional — blank keeps prior value)
                    if temperature_value:
                        stage.temperature = float(temperature_value)

                    # Generic measurement (blank keeps prior value)
                    if measurement_form_value:
                        stage.measurement_value = float(measurement_form_value)
                        stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(
                            stage_name, stage_name
                        )

                    # Lining thickness for Coating (blank keeps prior)
                    if thickness_profile is not None:
                        stage.thickness_profile = _merge_thickness_profile(
                            stage.thickness_profile, thickness_profile)

                    # Dimension grids for CCM (blank keeps prior)
                    if dimension_profile is not None:
                        stage.dimension_profile = _merge_dimension_profile(
                            stage.dimension_profile, dimension_profile
                        )

                    # Ovality grid for Annealing (blank keeps prior)
                    if has_ovality:
                        stage.ovality_profile = ovality_profile

                    # Visual checklist for Finish (blank keeps prior)
                    if visual_profile is not None:
                        stage.visual_profile = visual_profile

                    # Zinc coating-mass sheet (blank keeps prior)
                    if has_zinc:
                        stage.zinc_profile = zinc_profile

                    # Cutting ring test (blank keeps prior)
                    if has_ring:
                        stage.ring_profile = ring_profile

                    # Stage date/time from the row's inputs (blank keeps prior,
                    # falling back to today for a brand-new row). Annealing is
                    # included — its date/time are editable (manual backdating);
                    # only the batch number stays server-generated.
                    if stage_date_value:
                        stage.stage_date = date.fromisoformat(stage_date_value)
                    if stage_time_value:
                        # Local import: edit_pipe has a conditional
                        # `from datetime import datetime` in the shift logic,
                        # which shadows the module-level name for the whole
                        # function when that branch doesn't run.
                        from datetime import datetime as _dt

                        stage.stage_time = _dt.strptime(
                            stage_time_value, "%H:%M"
                        ).time()

                    # Delivery row: one date input drives both the stage date and
                    # the delivery date; text fields round-trip like notes.
                    if stage_name == delivery_name:
                        if delivery_date_value:
                            stage.delivery_date = date.fromisoformat(
                                delivery_date_value
                            )
                            stage.stage_date = stage.delivery_date
                        stage.delivery_customer = delivery_customer or None
                        stage.delivery_receipt = delivery_receipt or None
                        stage.sales_order = delivery_sales_order or None

                    # Finish row: bundle # + length (length is its measurement)
                    if stage_name == finish_name:
                        stage.bundle_number = finish_bundle or None
                        if finish_length:
                            stage.measurement_value = float(finish_length)
                            stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(
                                stage_name, stage_name
                            )

                    # Annealing row: date/time come from the (editable) inputs
                    # above; the server fills any blank and always generates
                    # the batch number itself.
                    if stage_name == annealing_name:
                        _auto_stamp_annealing(stage)

                    # If new stage, save history after creation
                    if is_new:
                        db.session.flush()
                        history = PipeStageHistory.create_from_stage(
                            stage, action="create", user_id=current_user.id
                        )
                        db.session.add(history)

            db.session.commit()

            # Auto-run decision engine if ladle has chemical decision
            if pipe.ladle_id:
                try:
                    from app.services.pipe_decision_service import (
                        assign_mechanical_roles,
                        reapply_mechanical_results,
                        update_lab_stage_decision,
                        update_final_decision,
                    )
                    from app.models.chemical import ChemicalAnalysis as CA

                    analysis = CA.query.filter_by(ladle_id=pipe.ladle_id).first()
                    if analysis and analysis.decision:
                        # Ensure Melting Ladle stage exists with chemical decision
                        melting_name = ProductionStage.name_for_code("melting_ladle")
                        melting_stage = PipeStage.query.filter_by(
                            pipe_id=pipe.id, stage_name=melting_name
                        ).first()
                        if not melting_stage:
                            melting_stage = PipeStage(
                                pipe_id=pipe.id,
                                stage_name=melting_name,
                                stage_date=analysis.test_date or date.today(),
                                decision=analysis.decision,
                                reason=analysis.reason,
                                updated_by_id=current_user.id,
                            )
                            db.session.add(melting_stage)
                            db.session.commit()

                        assign_mechanical_roles(pipe.ladle_id)

                        # assign_mechanical_roles wiped lab_decision to WAITING;
                        # restore it from tests already recorded so editing a
                        # pipe (or its Lab Approval) never resets the auto lab.
                        reapply_mechanical_results(pipe.ladle_id)

                        db.session.refresh(pipe)

                        if pipe.lab_decision and pipe.lab_decision != "WAITING":
                            update_lab_stage_decision(pipe)

                        update_final_decision(pipe)
                except Exception:
                    pass
            else:
                try:
                    from app.services.pipe_decision_service import update_final_decision

                    update_final_decision(pipe)
                except Exception:
                    pass

            # A silently skipped stage is indistinguishable from a save that
            # worked, so every refusal is reported.
            for reason in blocked_stages:
                flash(reason, "warning")

            flash("تم تحديث الأنبوب بنجاح", "success")
            return redirect(url_for("stages.view", id=pipe.id))

        except IntegrityError as e:
            db.session.rollback()
            flash(
                "Database error: a duplicate value was detected. Please check your input.",
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "error")

    return render_template(
        "stages/form.html",
        pipe=pipe,
        **_get_form_context(selected_order_id=pipe.production_order_id),
    )


@stages_bp.route("/api/ocr-extract", methods=["POST"])
@login_required
@requires_permission('stages', 'add')
def api_ocr_extract():
    """Extract pipe data from a pipe label/marking image via Gemini Vision."""
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No image file"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400
    try:
        image_bytes = file.read()
        from app.services.pipe_ocr_service import extract_pipe_from_image

        values = extract_pipe_from_image(image_bytes, file.filename)
        extracted = sum(1 for v in values.values() if v is not None)
        return jsonify(
            {
                "success": True,
                "values": values,
                "extracted": extracted,
                "message": f"Extracted {extracted} fields. Please verify.",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@stages_bp.route("/shift-dashboard")
@login_required
@requires_permission('stages', 'shift_dashboard')
def shift_dashboard():
    """Moved into the Reports section as a filterable report. Kept as a
    redirect so existing nav links / bookmarks keep working."""
    return redirect(url_for("reports.shift_engineer", **request.args.to_dict()))


def _legacy_shift_dashboard():
    """Old current-shift-only dashboard (superseded by reports.shift_engineer)."""
    from datetime import datetime as dt

    now = dt.now()
    hour = now.hour
    if 8 <= hour < 16:
        current_shift = 1
    elif 16 <= hour < 24:
        current_shift = 2
    else:
        current_shift = 3

    today = date.today()

    # Pipes produced today in this shift
    pipes = (
        Pipe.query.filter(
            Pipe.production_date == today,
            Pipe.shift == current_shift,
        )
        .order_by(Pipe.no_code)
        .all()
    )

    total = len(pipes)
    accepted = sum(1 for p in pipes if (p.final_decision_value or "") == "ACCEPT")
    rejected = sum(1 for p in pipes if (p.final_decision_value or "") == "REJECT")
    pending = total - accepted - rejected

    # Stages with defects today
    defect_stages = (
        db.session.query(PipeStage)
        .join(Pipe)
        .filter(
            Pipe.production_date == today,
            Pipe.shift == current_shift,
            PipeStage.has_defect.is_(True),
        )
        .all()
    )

    # Pipes waiting for decision
    waiting = [p for p in pipes if p.lab_decision == "WAITING"]

    return render_template(
        "stages/shift_dashboard.html",
        current_shift=current_shift,
        today=today,
        pipes=pipes,
        stats={
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "pending": pending,
        },
        defect_stages=defect_stages,
        waiting_pipes=waiting,
    )


def _delivery_filtered_query(args):
    """Build the filtered Delivery-stage query shared by the list and export.

    Distinct from the main pipe-list ``_filtered_query`` (that one queries
    ``Pipe``; this one queries ``PipeStage`` filtered to the Delivery stage and
    joins to ``Pipe`` only when a pipe-level filter is requested).
    """
    sales_order = args.get("sales_order")
    customer = args.get("customer")
    bundle = args.get("bundle")
    delivery_receipt = args.get("delivery_receipt")
    no_code = args.get("no_code")
    dn = args.get("dn")
    pipe_class = args.get("class")
    product_id = args.get("product_id")
    shift = args.get("shift")
    date_from = args.get("date_from")
    date_to = args.get("date_to")

    query = PipeStage.query.filter_by(stage_name=ProductionStage.name_for_code("delivery"))

    # PipeStage-native filters
    if sales_order:
        query = query.filter(PipeStage.sales_order == sales_order)
    if customer:
        query = query.filter(PipeStage.delivery_customer.ilike(f"%{customer}%"))
    if bundle:
        query = query.filter(PipeStage.bundle_number.ilike(f"%{bundle}%"))
    if delivery_receipt:
        query = query.filter(PipeStage.delivery_receipt.ilike(f"%{delivery_receipt}%"))
    if date_from:
        query = query.filter(PipeStage.delivery_date >= date_from)
    if date_to:
        query = query.filter(PipeStage.delivery_date <= date_to)

    # Pipe-level filters — join once, only when needed
    need_join = any([no_code, dn, pipe_class, product_id, shift])
    if need_join:
        query = query.join(Pipe, PipeStage.pipe_id == Pipe.id)
        if no_code:
            query = query.filter(Pipe.no_code.ilike(f"%{no_code}%"))
        if dn:
            try:
                query = query.filter(Pipe.diameter == int(dn))
            except (TypeError, ValueError):
                pass
        if pipe_class:
            query = query.filter(Pipe.pipe_class == pipe_class)
        if product_id:
            try:
                query = query.filter(Pipe.product_id == int(product_id))
            except (TypeError, ValueError):
                pass
        if shift:
            try:
                query = query.filter(Pipe.shift == int(shift))
            except (TypeError, ValueError):
                pass

    return query.order_by(
        PipeStage.delivery_date.desc().nullslast(), PipeStage.id.desc()
    )


@stages_bp.route("/deliveries")
@login_required
@requires_permission('stages', 'delivery')
def delivery_list():
    """List of delivered pipes / delivery stage records, with filters."""
    from app.models.product import Product

    deliveries = _delivery_filtered_query(request.args).limit(500).all()
    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.product_code)
        .all()
    )
    # Same "compare side by side" summary as the engineer report, over the
    # currently-filtered deliveries: by customer / engineer / sales order.
    from app.services import analytics_service
    delivery_summary = analytics_service.summarize_deliveries(deliveries)
    from app.services import export_service, table_registry
    return render_template(
        "stages/deliveries.html",
        picker_columns=export_service.picker_meta(table_registry.delivery_columns()),
        deliveries=deliveries,
        delivery_summary=delivery_summary,
        products=products,
        dn_sizes=DN_SIZES,
        pipe_classes=PIPE_CLASSES,
        filters={
            "sales_order": request.args.get("sales_order"),
            "customer": request.args.get("customer"),
            "bundle": request.args.get("bundle"),
            "delivery_receipt": request.args.get("delivery_receipt"),
            "no_code": request.args.get("no_code"),
            "dn": request.args.get("dn"),
            "class": request.args.get("class"),
            "product_id": request.args.get("product_id"),
            "shift": request.args.get("shift"),
            "date_from": request.args.get("date_from"),
            "date_to": request.args.get("date_to"),
        },
    )


@stages_bp.route("/deliveries/export.xlsx")
@login_required
@requires_permission('stages', 'delivery')
def delivery_export():
    """Export/print the filtered Delivery records, respecting current filters.

    Default fields mirror the on-screen deliveries table; more pipe/stage
    fields are available via the picker. Honours ``cols=``, ``ids=`` and
    ``format=print``.
    """
    from app.services import export_service, table_registry
    from app.models.pipe import PipeStage

    query = _delivery_filtered_query(request.args)
    query = export_service.filter_ids(query, PipeStage.id, request.args.get("ids"))
    rows = query.all()

    selected = export_service.resolve_columns(
        table_registry.delivery_columns(), request.args.get("cols"))
    if request.args.get("format") == "print":
        return export_service.build_print("Deliveries", selected, rows)
    return export_service.build_xlsx("Deliveries", selected, rows, "deliveries.xlsx")


@stages_bp.route("/api/ladle/<ladle_id>")
@login_required
@requires_permission('stages', 'list')
def api_get_ladle(ladle_id):
    """API to get ladle info for pipe creation"""
    analysis = ChemicalAnalysis.query.filter_by(ladle_id=ladle_id).first()
    if analysis:
        # Auto-calculate next arrange_pipe number
        max_arrange = (
            db.session.query(db.func.max(Pipe.arrange_pipe))
            .filter(Pipe.ladle_id == ladle_id)
            .scalar()
        )
        next_arrange = (max_arrange or 0) + 1

        return jsonify(
            {
                "found": True,
                "test_date": analysis.test_date.isoformat(),
                "furnace": analysis.furnace.furnace_code if analysis.furnace else None,
                "decision": analysis.decision,
                "next_arrange_pipe": next_arrange,
                "pipe_count": Pipe.query.filter_by(ladle_id=ladle_id).count(),
            }
        )
    return jsonify({"found": False})


@stages_bp.route("/go")
@login_required
@requires_permission('stages', 'list')
def go():
    """Smart topbar search redirect.

    A complete pipe code jumps straight to that pipe's detail page; anything
    partial falls back to the stages list filtered by the same text.
    """
    q = (request.args.get("q") or "").strip()
    if q:
        exact = (
            Pipe.query.filter(db.func.lower(Pipe.pipe_code) == q.lower())
            .first()
        )
        if exact:
            return redirect(url_for("stages.view", id=exact.id))
    return redirect(url_for("stages.list", pipe_code=q))


@stages_bp.route("/api/search-pipes")
@login_required
@requires_permission('stages', 'list')
def api_search_pipes():
    """Live suggestions for the topbar search — matches even 1-2 characters
    of pipe_code / no_code / ladle_id."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    pipes = (
        Pipe.query.filter(db.or_(
            Pipe.pipe_code.ilike(like),
            Pipe.no_code.ilike(like),
            Pipe.ladle_id.ilike(like),
        ))
        .order_by(Pipe.production_date.desc(), Pipe.id.desc())
        .limit(8)
        .all()
    )
    return jsonify([
        {
            "id": p.id,
            "pipe_code": p.pipe_code or "",
            "ladle_id": p.ladle_id or "",
            "diameter": p.diameter,
        }
        for p in pipes
    ])


@stages_bp.route("/<int:id>/history")
@login_required
@requires_permission('stages', 'list')
def stage_history(id):
    """Get history for all stages of a pipe"""
    pipe = Pipe.query.get_or_404(id)

    history = (
        PipeStageHistory.query.filter_by(pipe_id=pipe.id)
        .order_by(PipeStageHistory.changed_at.desc())
        .all()
    )

    history_data = []
    for h in history:
        history_data.append(
            {
                "id": h.id,
                "stage_name": h.stage_name,
                "action": h.action,
                "decision": h.decision,
                "reason": h.reason,
                "machine_code": h.machine_code,
                "has_defect": h.has_defect,
                "defect_type": h.defect_type,
                "defect_reason": h.defect_reason,
                "notes": h.notes,
                "measurement_value": h.measurement_value,
                "stage_date": h.stage_date.isoformat() if h.stage_date else None,
                "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M:%S"),
                "changed_by": h.changed_by.full_name or h.changed_by.username
                if h.changed_by
                else "Unknown",
            }
        )

    return jsonify({"history": history_data})


@stages_bp.route("/<int:pipe_id>/stage/<stage_name>/history")
@login_required
@requires_permission('stages', 'list')
def single_stage_history(pipe_id, stage_name):
    """Get history for a specific stage of a pipe"""
    pipe = Pipe.query.get_or_404(pipe_id)

    history = (
        PipeStageHistory.query.filter_by(pipe_id=pipe.id, stage_name=stage_name)
        .order_by(PipeStageHistory.changed_at.desc())
        .all()
    )

    history_data = []
    for h in history:
        history_data.append(
            {
                "id": h.id,
                "stage_name": h.stage_name,
                "action": h.action,
                "decision": h.decision,
                "reason": h.reason,
                "machine_code": h.machine_code,
                "has_defect": h.has_defect,
                "defect_type": h.defect_type,
                "defect_reason": h.defect_reason,
                "notes": h.notes,
                "measurement_value": h.measurement_value,
                "stage_date": h.stage_date.isoformat() if h.stage_date else None,
                "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M:%S"),
                "changed_by": h.changed_by.full_name or h.changed_by.username
                if h.changed_by
                else "Unknown",
            }
        )

    return jsonify({"history": history_data})


@stages_bp.route("/api/ovality-grid")
@login_required
def api_ovality_grid():
    """The dimension grid for one DN, rendered on its own.

    The Add form has no pipe yet, so the grid first draws with the internal
    diameter alone; the DN only becomes known when the operator picks it in the
    select on the same page. Re-rendering the macro here — rather than rebuilding
    the columns in JavaScript — keeps one definition of what a DN measures.

    Readings are never returned: the caller preserves whatever was already typed
    and reapplies it after the swap.
    """
    stage_name = (request.args.get("stage") or "").strip()
    if not stage_name:
        return jsonify({"error": "stage is required"}), 400
    dn = (request.args.get("dn") or "").strip() or None
    entries = dimension_standard_service.for_dn(dn) if dn else {}
    html = render_template(
        "stages/_ovality_grid_fragment.html",
        stage_name=stage_name,
        dn=dn,
        entries=entries,
        points=dimension_standard_service.ovality_columns_for(dn),
        saved={},
        is_ar=(get_locale() == "ar"),
    )
    return jsonify({"html": html, "dn": dn or ""})


@stages_bp.route("/<int:id>/zinc-sheet")
@login_required
@requires_permission('stages', 'list')
def zinc_sheet(id):
    """The zinc coating-mass sheet for one pipe, laid out for the printer.

    The lab keeps a signed paper copy of this test, so the page reproduces the
    TA sheet rather than the app's own tables: the run header across the top,
    the M1/M2/A/C row with the formula worked out beside it, and the final
    decision plus the two signature blocks at the bottom.

    Everything but the signatures is filled from the Zinc stage row — who
    approved it and when, the machine it ran on, the pipe's DN and class, the
    decision and any note — so the printed sheet carries the same record as the
    screen and nothing has to be copied over by hand.
    """
    pipe = Pipe.query.get_or_404(id)
    zinc_name = ProductionStage.name_for_code("zinc")
    stage = pipe.get_stage(zinc_name)
    return render_template(
        "stages/zinc_sheet_print.html",
        pipe=pipe,
        stage=stage,
        zp=(stage.zinc_profile if stage and stage.zinc_profile else {}) or {},
        stage_name=zinc_name,
        is_ar=(get_locale() == "ar"),
    )
