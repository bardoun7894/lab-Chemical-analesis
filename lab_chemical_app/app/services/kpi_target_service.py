"""Targets for the dashboard KPIs.

Each KPI tile shows a number with no reference point, so nobody can tell a good
month from a bad one without knowing the plan in their head. This module lets
an admin record the target for each KPI and turns a reading into a verdict.

Deliberately no default numbers. A target is a management decision about this
foundry, not something this code can guess, so every KPI ships with an empty
target and the tile simply shows no verdict until someone sets one. That keeps
a blank configuration honest rather than quietly grading against an invented
figure.

Direction is a property of the KPI, not a setting: a higher yield is always
better and a higher reject rate never is. Only the number is configurable.
"""

SETTINGS_KEY = "kpi_targets"

HIGHER_IS_BETTER = "higher"
LOWER_IS_BETTER = "lower"

# key, Arabic label, English label, unit, direction.
# `key` matches the field name in bi_service's kpis dict.
KPIS = (
    # --- quality ------------------------------------------------------------
    ("reject_pct", "معدل الرفض (اليوم)", "Reject rate (today)", "%",
     LOWER_IS_BETTER),
    ("fty_pct", "FTY — سليمة من أول مرة", "First time yield", "%", HIGHER_IS_BETTER),
    ("first_pass_pct", "مقبولة بدون رفض", "Accepted without a reject", "%",
     HIGHER_IS_BETTER),
    ("rft_pct", "RFT — عدّت بدون إعادة تشغيل", "Right first time", "%",
     HIGHER_IS_BETTER),
    ("rework_pct", "معدل إعادة التشغيل", "Rework rate", "%", LOWER_IS_BETTER),
    ("defect_pct", "نسبة العيوب", "Defect rate", "%", LOWER_IS_BETTER),
    ("chem_reject_pct", "رفض التحليل الكيميائي", "Chemical reject rate", "%",
     LOWER_IS_BETTER),
    ("mech_fail_pct", "رسوب الاختبار الميكانيكي", "Mechanical fail rate", "%",
     LOWER_IS_BETTER),
    # --- flow ---------------------------------------------------------------
    ("hold_pct", "نسبة المحجوز", "Held rate", "%", LOWER_IS_BETTER),
    ("pending_pct", "نسبة اللي لسه بدون قرار", "Undecided rate", "%",
     LOWER_IS_BETTER),
    # --- output -------------------------------------------------------------
    ("produced", "إجمالي الإنتاج", "Produced", "ماسورة", HIGHER_IS_BETTER),
    ("actual_mt", "الوزن الفعلي", "Actual weight", "طن", HIGHER_IS_BETTER),
    ("yield_pct", "Yield العائد", "Yield", "%", HIGHER_IS_BETTER),
    ("saving_mt", "Saving بالوزن", "Saving (weight)", "طن", HIGHER_IS_BETTER),
    ("saving_pct", "Saving بالنسبة", "Saving (share)", "%", HIGHER_IS_BETTER),
)

# Targets that are not plant-wide readings: they are the line a single unit
# may not cross, and the alert engine tests every one of those units against
# them. They live in the same settings section and the same screen, because an
# admin setting "the reject rate we accept" should not have to learn that the
# plant number and the per-machine number are different features.
SCOPED_KPIS = (
    ("machine_reject_pct", "معدل رفض المكنة الواحدة", "Reject rate per machine",
     "%", LOWER_IS_BETTER),
    ("stage_reject_pct", "معدل رفض المرحلة الواحدة", "Reject rate per stage",
     "%", LOWER_IS_BETTER),
    ("mold_reject_pct", "معدل رفض الاسطمبة الواحدة", "Reject rate per mould",
     "%", LOWER_IS_BETTER),
    ("shift_reject_pct", "معدل رفض الوردية الواحدة", "Reject rate per shift",
     "%", LOWER_IS_BETTER),
    ("dn_reject_pct", "معدل رفض القطر الواحد", "Reject rate per DN",
     "%", LOWER_IS_BETTER),
    # The plant's own rate over a window, day and month side by side, because
    # a day can sit under the line all week while the month drifts over it and
    # the month is what the client reports upward.
    #
    # `day_reject_pct` and `reject_pct` are the SAME reading. The first is the
    # explicit name that sits next to the monthly one; the second is the
    # original key and is still honoured so an existing saved target keeps
    # working. Setting both does not alert twice — see evaluate_day.
    ("day_reject_pct", "معدل الرفض اليومي", "Reject rate per day",
     "%", LOWER_IS_BETTER),
    ("month_reject_pct", "معدل الرفض الشهري", "Reject rate per month",
     "%", LOWER_IS_BETTER),
)

# The daily plant reject rate has two target keys for historical reasons.
# `day_reject_pct` wins when both are set; nothing reads them directly.
DAY_REJECT_KEYS = ("day_reject_pct", "reject_pct")


def day_reject_target(targets):
    """The one target that governs the daily plant reject rate, or None."""
    for key in DAY_REJECT_KEYS:
        if targets.get(key) is not None:
            return key, targets[key]
    return None, None

ALL_KPIS = KPIS + SCOPED_KPIS

_BY_KEY = {k: (ar, en, unit, direction) for k, ar, en, unit, direction in ALL_KPIS}


def label(key, locale="ar"):
    """The KPI's own name, for an alert that has to read as a sentence."""
    entry = _BY_KEY.get(key)
    if not entry:
        return key
    return entry[0] if locale == "ar" else entry[1]


def unit(key):
    return _BY_KEY[key][2] if key in _BY_KEY else ""


def direction(key):
    return _BY_KEY[key][3] if key in _BY_KEY else HIGHER_IS_BETTER


def load(settings=None):
    """``{kpi_key: target_float}`` for every KPI that has one set.

    Keys with a blank or unparseable value are omitted rather than defaulted,
    so "no target" and "target of zero" stay distinguishable — zero is a
    legitimate target for the reject rate.
    """
    if settings is None:
        from app.routes.admin import load_app_settings

        settings = load_app_settings()

    raw = (settings or {}).get(SETTINGS_KEY) or {}
    out = {}
    for key in _BY_KEY:
        value = raw.get(key)
        if value is None or value == "":
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def evaluate(key, value, targets):
    """How a reading compares with its target.

    Returns ``None`` when the KPI has no target or no reading — the tile then
    renders exactly as it did before this feature existed. Otherwise a dict:

        {"target": float, "met": bool, "direction": "higher"|"lower",
         "delta": float}

    ``delta`` is signed the way the reader expects: positive means better than
    target, whichever way the KPI runs. So a reject rate 1.5 points *under*
    its target reports ``+1.5``, not ``-1.5``.
    """
    if key not in _BY_KEY or key not in targets or value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    target = targets[key]
    direction = _BY_KEY[key][3]

    if direction == LOWER_IS_BETTER:
        met = value <= target
        delta = target - value
    else:
        met = value >= target
        delta = value - target

    return {
        "target": target,
        "met": met,
        "direction": direction,
        "delta": round(delta, 2),
    }


def evaluate_all(kpis, settings=None):
    """``{kpi_key: verdict}`` for a bi_service kpis dict. Empty when unset."""
    targets = load(settings)
    if not targets:
        return {}
    out = {}
    for key in _BY_KEY:
        verdict = evaluate(key, (kpis or {}).get(key), targets)
        if verdict:
            out[key] = verdict
    return out
