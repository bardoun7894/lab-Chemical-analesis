"""Sequential stage gating — a stage cannot be decided before the one before it.

The app already had exactly one gate: Zinc, the stage after Lab Approval, is
blocked while the lab result is pending. That one carries a comment saying not
to generalise it, because at the time the client wanted only that step
enforced. This module is the generalisation, and it is off by default so
existing behaviour is unchanged until someone turns it on.

The rule is deliberately narrow: it blocks *recording a decision* on a stage
whose predecessor has not been accepted. Entering measurements, dates or notes
stays open, because the shop floor routinely records readings before the
paperwork upstream catches up, and blocking that would push people back to
paper.
"""

from app.models.pipe import PipeStage
from app.models.stage import ProductionStage

SETTINGS_KEY = "stage_flow"
REQUIRE_PREVIOUS = "require_previous_approval"


def is_enabled(settings=None):
    """True when sequential gating is switched on in Settings."""
    if settings is None:
        from app.routes.admin import load_app_settings
        settings = load_app_settings()
    return bool((settings or {}).get(SETTINGS_KEY, {}).get(REQUIRE_PREVIOUS))


def previous_stage(stage_name):
    """The active stage immediately before this one, or None for the first.

    Order comes from Stage Management, so a stage that was deactivated is not
    treated as a missing prerequisite — it is simply not in the line any more.
    """
    names = ProductionStage.active_names()
    if stage_name not in names:
        return None
    idx = names.index(stage_name)
    return names[idx - 1] if idx > 0 else None


def blocking_reason(pipe, stage_name, decision, settings=None):
    """Why this decision cannot be recorded yet, or None if it can.

    Returns a message meant for the operator. Only a decision is gated: a save
    that carries no decision passes regardless.
    """
    if not decision or not is_enabled(settings):
        return None

    prev_name = previous_stage(stage_name)
    if prev_name is None:
        return None

    prev = next(
        (s for s in (pipe.stages or []) if s.stage_name == prev_name), None
    )
    if prev is None or not prev.decision:
        return (
            f"لا يمكن تسجيل قرار {stage_name} قبل اعتماد مرحلة {prev_name}"
            " — لم يُسجَّل لها قرار بعد."
        )

    verdict = PipeStage.classify_decision(prev.decision)
    if verdict == "accept":
        return None
    if verdict == "reject":
        return (
            f"لا يمكن تسجيل قرار {stage_name}: مرحلة {prev_name} مرفوضة"
            f" ({prev.decision})."
        )
    # Hold, rework, resample and anything else unrecognised are all "not yet
    # approved" — they are explicitly not an acceptance.
    return (
        f"لا يمكن تسجيل قرار {stage_name} قبل اعتماد مرحلة {prev_name}"
        f" — قرارها الحالي: {prev.decision}."
    )
