"""Application spec — the standard a run is built to and the three thicknesses
it must hold.

The block started life on the production order (2026-08-27). It now also lives
on the Product, which is where the specification actually belongs: a product is
built to ISO 8179 / EN 598 (sewage) or ISO 2531 / EN 545 (water), optionally
AWWA, and that standard fixes the wall, cement-lining and external-coating
thicknesses as Value ± Tolerance. An order copies the product's block and may
then be edited for a customer who wants something slightly different.

A product is built to exactly one standard, so the choice is two radios, not
five ticks: first Sewage or Water, then one standard inside that group. AWWA
is offered under either group because it applies to both.

Shape stored in ``application_profile`` (JSON) on both tables:

    {"group": "sewage" | "water" | None,
     "standards": {"iso_8179": bool, "en_598": bool,
                   "iso_2531": bool, "en_545": bool, "awwa": bool},
     "layers": {"thickness"|"cement"|"coating":
                {"value", "tolerance_plus", "tolerance_minus",
                 "min", "nominal", "max"}}}

``standards`` stays a dict with at most one True rather than a single string:
every reader downstream (standard_labels, the stage badges, the order detail
table) already walks it, and one shape means no migration for the records
written while it was a multi-select.

Everything here is shared by the product form, the order form and the stage
screens, so a new standard or a renamed layer is one line in one place.
"""

import json

# The order sheet's own grouping: sewage standards, water standards, then AWWA
# on its own. (group, ((key, label), ...)).
APPLICATION_STANDARDS = (
    ("sewage", (("iso_8179", "ISO 8179"), ("en_598", "EN 598"))),
    ("water", (("iso_2531", "ISO 2531"), ("en_545", "EN 545"))),
    ("other", (("awwa", "AWWA"),)),
)

# Wall, cement lining, external coating — the sheet's three rows.
APPLICATION_LAYERS = (
    ("thickness", "Thickness", "السماكة"),
    ("cement", "Cement thickness", "سماكة الأسمنت"),
    ("coating", "Coating thickness", "سماكة الكوتنج"),
)

APPLICATION_FIELDS = ("value", "tolerance_plus", "tolerance_minus", "min", "nominal", "max")

# What an Intended Use parameter may be tagged with in admin. "both" (and an
# untagged parameter) offers every standard.
INTENT_GROUPS = ("sewage", "water", "both")

# The two that actually name a set of standards. "both" is the answer for an
# Intended Use that picks neither, and gates nothing — it must never be stored
# or passed around as if it were a group.
SPEC_GROUPS = ("sewage", "water")

# AWWA sits outside the sewage/water split — it is offered whatever the
# intended use says.
UNGATED_GROUP = "other"

# Every standard key, in sheet order.
STANDARD_KEYS = tuple(key for _g, entries in APPLICATION_STANDARDS
                      for key, _label in entries)

# Which group each standard belongs to. AWWA maps to UNGATED_GROUP because it
# names no group of its own — a product on AWWA is still sewage or water, and
# that comes from the operator's group choice.
STANDARD_GROUP = {key: group for group, entries in APPLICATION_STANDARDS
                  for key, _label in entries}


def standards_for_group(group):
    """Every standard a group offers, AWWA included.

    A sewage product is specified under ISO 8179, EN 598 *and* AWWA — all
    three carry their own thicknesses. The group is the only single choice.
    """
    if group not in ("sewage", "water"):
        return ()
    by_group = dict(APPLICATION_STANDARDS)
    return tuple(key for key, _label in by_group.get(group, ())) + \
        tuple(key for key, _label in by_group.get(UNGATED_GROUP, ()))


def standard_options(group):
    """(key, label) for every standard a group offers — for the order picker."""
    by_group = dict(APPLICATION_STANDARDS)
    if group not in ("sewage", "water"):
        return [(k, l) for _g, entries in APPLICATION_STANDARDS
                for k, l in entries]
    return list(by_group.get(group, ())) + list(by_group.get(UNGATED_GROUP, ()))


def group_of_standard(key):
    """The sewage/water group a standard implies, or '' when it implies none."""
    group = STANDARD_GROUP.get(key)
    return group if group in ("sewage", "water") else ""


def selected_standard(profile):
    """The one standard key on a profile, or None.

    Takes the first ticked in sheet order, so a record written while this was
    a multi-select still resolves to something rather than raising.
    """
    ticked = (profile or {}).get("standards") or {}
    return next((key for key in STANDARD_KEYS if ticked.get(key)), None)


def selected_group(profile):
    """The sewage/water group on a profile, falling back to its standard."""
    group = (profile or {}).get("group")
    if group in ("sewage", "water"):
        return group
    return group_of_standard(selected_standard(profile)) or None


# The plant's own intended-use letters. S is sewage; L and E are water. This
# is the fallback so the gate works on the parameters already in the database
# without anyone re-entering them — the admin field overrides it.
INTENT_CODE_GROUPS = {"S": "sewage", "L": "water", "E": "water"}


def group_for_intent(param):
    """The standards group an INTENT_USE parameter selects.

    A real tag set in admin wins. "both" is not a real tag — it is the admin
    select's default, so every Intended Use saved for any other reason (a
    rename, a sort order) comes back tagged "both". Treating that as an answer
    ungated the block on prod: with no group the form posted an empty
    ``app_group`` and the next product save cleared the product's Application.
    So "both" falls through to the parameter's own code, where S is sewage and
    L and E are water. Only a parameter that is neither really tagged nor
    recognised ends on 'both', which hides nothing.
    """
    if not param:
        return "both"
    group = getattr(param, "application_group", None)
    if group in SPEC_GROUPS:
        return group
    code = (getattr(param, "code", "") or "").strip().upper()
    return INTENT_CODE_GROUPS.get(code, "both")


def groups_for_intent(param):
    """The standards groups to show for an INTENT_USE parameter."""
    group = group_for_intent(param)
    if group == "both":
        return {"sewage", "water", UNGATED_GROUP}
    return {group, UNGATED_GROUP}


def _derive_row(row):
    """Nominal / Min / Max, computed from Value ± Tolerance.

    The three are derived, never authored: Nominal is the Value, Min is
    Value − |−Tol| and Max is Value + |+Tol|. They are recomputed on every
    read rather than trusted from storage, because the product form used to
    treat a stored limit that disagreed with the formula as hand-typed and
    then never update it again — so editing a Value or a tolerance left the
    limits behind. Prod had a Max of 7.0 against a Value of 5.0 and a +Tol of
    5.0, and a Max of 2.0 against a Value of 1.0 and a +Tol of 25.0.

    A row with no tolerance at all keeps its empty Min and Max on purpose:
    that is the state the grids report as "no tolerance on file", and
    inventing a zero-width band from it would fail every reading that is not
    exactly nominal.
    """
    row = dict(row or {})
    value = row.get("value")
    if value is None:
        # Rows written before Value existed carry only a nominal.
        value = row.get("nominal")
    if value is None:
        return _repair_row(row)

    tol_plus = row.get("tolerance_plus")
    tol_minus = row.get("tolerance_minus")
    row["nominal"] = value
    # Fill one side only and the other stays at the value, which is what the
    # note under the table promises.
    if tol_minus is not None:
        row["min"] = value - abs(tol_minus)
    elif tol_plus is not None:
        row["min"] = value
    if tol_plus is not None:
        row["max"] = value + abs(tol_plus)
    elif tol_minus is not None:
        row["max"] = value
    return _repair_row(row)


def _impossible(row):
    """True when the three limits contradict each other.

    Min above Max, or a Nominal outside its own band. No operator means either
    one: they are what a row decays into when Value or a tolerance is edited
    while one of the derived boxes is frozen as hand-typed.
    """
    lo, nom, hi = row.get("min"), row.get("nominal"), row.get("max")
    if lo is not None and hi is not None and lo > hi:
        return True
    if nom is not None and lo is not None and nom < lo:
        return True
    if nom is not None and hi is not None and nom > hi:
        return True
    return False


def _repair_row(row):
    """Rebuild a self-contradictory band from Value ± Tolerance.

    A band whose Min sits above its Max cannot be satisfied: every reading
    judged against it is out of spec, which reads on the grid as a pipe that
    failed everywhere. Deliberate overrides — a Max typed lower than
    Value + Tol, say — stay untouched, because a band that merely disagrees
    with the formula is still a band somebody can meet.

    If Value and the tolerances cannot produce a coherent band either, the
    limits are dropped rather than guessed. The grid then reports "no tolerance
    on file" and withholds the verdict, which is the honest answer.
    """
    if not _impossible(row):
        return row
    value = row.get("value")
    if value is None:
        value = row.get("nominal")
    rebuilt = dict(row)
    rebuilt["nominal"] = value
    rebuilt["min"] = (value - abs(row["tolerance_minus"])
                      if value is not None and row.get("tolerance_minus") is not None
                      else None)
    rebuilt["max"] = (value + abs(row["tolerance_plus"])
                      if value is not None and row.get("tolerance_plus") is not None
                      else None)
    if _impossible(rebuilt):
        rebuilt["min"] = rebuilt["max"] = None
    return rebuilt


def normalise(profile):
    """Read a stored profile into the current field set.

    Orders written before the ``tolerance`` → ``tolerance_plus`` /
    ``tolerance_minus`` split (2026-08-27) carry the single old key. Rather
    than migrate the data, read it into both sides so those orders stop
    rendering empty ±Tol cells.
    """
    if not profile:
        return {}
    layers = {}
    for key, row in (profile.get("layers") or {}).items():
        row = dict(row or {})
        legacy = row.pop("tolerance", None)
        if legacy is not None:
            for side in ("tolerance_plus", "tolerance_minus"):
                if row.get(side) is None:
                    row[side] = legacy
        layers[key] = _derive_row(row)
    specs = {}
    for std, table in (profile.get("specs") or {}).items():
        specs[std] = {k: _derive_row(v) for k, v in (table or {}).items()}
    return {"group": selected_group(profile),
            "standards": dict(profile.get("standards") or {}),
            "specs": specs, "layers": layers}


def read_application_profile(form, marker="app_block_present"):
    """Read the Application block out of a form post.

    Input names: ``app_std_<key>`` for the ticks, ``app_<layer>_<field>`` for
    the grid, plus the hidden ``marker`` input.

    Returns ``(profile_or_None, has_any)``. None when the block was never on
    screen — the checkboxes only post when ticked, so a form that predates the
    block is otherwise indistinguishable from one where the operator cleared
    everything. The marker tells the two apart, and the caller leaves a saved
    profile alone when it is absent.
    """
    group = (form.get("app_group") or "").strip()
    if group not in ("sewage", "water"):
        group = ""

    # `app_std` is the order's choice of which standard that run is built to.
    # The product does not have one: it carries a table for every standard in
    # its group, and the order picks from them.
    chosen = (form.get("app_std") or "").strip()
    if chosen not in STANDARD_KEYS:
        chosen = next((key for key in STANDARD_KEYS
                       if form.get("app_std_" + key)), "")
    if not group:
        group = group_of_standard(chosen)

    if chosen:
        standards = {key: key == chosen for key in STANDARD_KEYS}
    else:
        # Every standard the group offers is part of the product's spec.
        offered = standards_for_group(group)
        standards = {key: key in offered for key in STANDARD_KEYS}

    # Each standard carries its own thicknesses — a product can be specified
    # under ISO 2531 and under EN 545 at once, with only one of them active.
    # Names are hyphen-delimited because both the standard keys and the layer
    # keys contain underscores: app_spec-<std>-<layer>-<field>.
    specs = {}
    for std in STANDARD_KEYS:
        specs[std] = _read_layers(form, "app_spec-%s-" % std, sep="-")

    # The order form does not author tables; it carries the product's whole set
    # forward as JSON so reopening it and switching the choice still has
    # figures to show.
    carried = form.get("app_specs_json")
    if carried and not any(_any_value(t) for t in specs.values()):
        try:
            loaded = json.loads(carried)
        except (TypeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            for std in STANDARD_KEYS:
                table = loaded.get(std)
                if isinstance(table, dict):
                    specs[std] = table

    # The legacy single table posts app_<layer>_<field>. Read it as the active
    # standard's data so a form that predates the tabs still saves.
    legacy = _read_layers(form, "app_", sep="_")
    if chosen and not _any_value(specs.get(chosen)) and _any_value(legacy):
        specs[chosen] = legacy

    # `layers` stays the active standard's numbers. Everything downstream —
    # the order form prefill, the stage popups, the order detail table —
    # already reads it, and keeping it means none of them need to know that
    # the spec is now per standard.
    layers = specs.get(chosen) or legacy

    if not form.get(marker):
        return None, False

    # The block was on screen but named neither a group nor a standard. That is
    # not somebody clearing the Application — a real authoring always leaves a
    # standard ticked. It is the gate failing to resolve, and saving it would
    # demote a good profile to an empty one: prod lost the Application on two
    # products this way, from a form opened only to correct a weight. Blank
    # keeps prior, same as a form that never rendered the block at all.
    if not group and not any(standards.values()):
        return None, False

    return {"group": group or None, "standards": standards,
            "specs": specs, "layers": layers}, True


def _read_layers(form, prefix, sep="_"):
    """Read one three-row thickness table out of a form, under `prefix`."""
    out = {}
    for key, _en, _ar in APPLICATION_LAYERS:
        row = {}
        for field in APPLICATION_FIELDS:
            raw = form.get("%s%s%s%s" % (prefix, key, sep, field))
            if raw in (None, ""):
                row[field] = None
                continue
            try:
                row[field] = float(raw)
            except (TypeError, ValueError):
                row[field] = None
        out[key] = row
    return out


def _any_value(layers):
    for row in (layers or {}).values():
        if any(v is not None for v in (row or {}).values()):
            return True
    return False


def layers_for(profile, standard=None):
    """The thickness table for one standard, falling back to the active one.

    Records written before the tabs have no `specs`, so they answer with their
    single `layers` table whatever standard is asked for — which is right:
    that table *was* the active standard's.
    """
    profile = profile or {}
    specs = profile.get("specs") or {}
    if standard and standard in specs and _any_value(specs[standard]):
        return specs[standard]
    if standard and specs:
        return specs.get(standard) or {}
    return profile.get("layers") or {}


def for_order(order_profile, product_profile, group=None):
    """What the order screen should show: the product's tables, the order's choice.

    The spec is authored on the product, so the figures always come from
    there — an order saved before the tables existed has no `specs` of its own
    and would otherwise show a blank grid whichever standard was picked.

    The group follows the product too. An order can outlive a change of
    Intended Use, and a choice that no longer belongs to the product's group
    is dropped rather than shown: order 44 still named ISO 2531 after its
    product moved to sewage.
    """
    order_profile = normalise(order_profile or {})
    product_profile = normalise(product_profile or {})

    # The product is the master, per standard. An order created through the
    # current form carries a copy of the product's whole set so that switching
    # the choice works without a round trip, and that copy used to win outright
    # as long as it held any figure at all — which froze the order against
    # every later correction. Prod had two such orders, one of them the only
    # order with pipes on it: fixing a figure on the product changed nothing on
    # the floor. The order's copy now fills only the standards the product has
    # no table for.
    order_specs = order_profile.get("specs") or {}
    product_specs = product_profile.get("specs") or {}
    specs = {}
    for key in set(order_specs) | set(product_specs):
        if _any_value(product_specs.get(key)):
            specs[key] = product_specs[key]
        elif _any_value(order_specs.get(key)):
            specs[key] = order_specs[key]

    # Intended Use on the product decides the group, so switching a product
    # from S to L moves its orders to the water standards without anyone
    # reopening the Application block to re-save it. The stored group is only
    # the fallback for a product whose Intended Use says nothing either way.
    if group not in SPEC_GROUPS:
        group = None
    group = group or product_profile.get("group") or order_profile.get("group")

    chosen = selected_standard(order_profile)
    offered = standards_for_group(group)
    if offered and chosen not in offered:
        chosen = None
    if chosen is None:
        # nothing valid chosen yet — offer the first that actually has figures
        chosen = next((k for k in offered if _any_value(specs.get(k))), None)

    return {
        "group": group,
        "standards": {k: k == chosen for k in STANDARD_KEYS},
        "specs": specs,
        "layers": (specs.get(chosen) if chosen else None)
                  or order_profile.get("layers") or {},
    }


def spec_has_values(profile, standard):
    """True when this standard's tab holds any number — drives the tab dot."""
    return _any_value(layers_for(profile, standard))


def standard_labels(profile):
    """The standards ticked on a profile, as display labels in sheet order.

    Empty when the record predates the block or nothing was ticked. Stage
    screens use this to show the operator which standard the run is built to
    without having to open the order.
    """
    ticked = (profile or {}).get("standards") or {}
    return [
        label
        for _group, entries in APPLICATION_STANDARDS
        for key, label in entries
        if ticked.get(key)
    ]


def layer_summary(profile):
    """Compact ``[(label_en, label_ar, nominal), ...]`` for list screens.

    Nominal is whatever the record actually holds — the derived Nominal when
    set, else the typed Value — so a half-filled row still shows a number.
    """
    layers = (normalise(profile) or {}).get("layers") or {}
    out = []
    for key, label_en, label_ar in APPLICATION_LAYERS:
        row = layers.get(key) or {}
        val = row.get("nominal")
        if val is None:
            val = row.get("value")
        out.append((label_en, label_ar, val))
    return out


def has_values(profile):
    """True when anything at all was ticked or typed."""
    if not profile:
        return False
    if any((profile.get("standards") or {}).values()):
        return True
    for row in (profile.get("layers") or {}).values():
        if any(v is not None for v in (row or {}).values()):
            return True
    return False
