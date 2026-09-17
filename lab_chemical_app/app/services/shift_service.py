"""Which production shift a moment in time falls in.

The 8/16/24 rule was copy-pasted into three places in the stages routes
(pipe registration, pipe edit, and the legacy shift dashboard) with no shared
definition, so nothing could reuse it and nothing could correct it in one
place. Reporting is the first caller that needs it applied to a stored
timestamp rather than to "now".

Timezone matters here and is easy to get wrong: ``PipeStage.stage_time`` and
the registration-time shift are written from local ``datetime.now()``, while
``created_at`` / ``updated_at`` use ``datetime.utcnow()``. Bucketing a UTC
timestamp with a local rule silently shifts every reading by the UTC offset —
an hour or two is enough to move a reading into the wrong shift. Callers pass
``is_utc=True`` for the database timestamps so the offset is corrected first.
"""

from datetime import datetime, timedelta

# Local shift boundaries, matching the rule the pipe form has always applied.
SHIFT_BOUNDS = ((8, 16, 1), (16, 24, 2))
NIGHT_SHIFT = 3

# The plant runs on Africa/Casablanca. The app has no TZ set (config pins Babel
# to UTC and neither the Dockerfile nor compose sets one), so the container
# clock is UTC and the offset has to be applied explicitly rather than assumed
# to be zero.
LOCAL_UTC_OFFSET_HOURS = 1


def shift_for_time(value, is_utc=False):
    """The shift number (1/2/3) for a time or datetime, or None if unknown.

    ``value`` may be a ``datetime``, a ``time``, or None. ``is_utc`` converts a
    stored UTC timestamp to local before bucketing; it is ignored for a bare
    ``time``, which carries no date to shift across midnight and is already
    local wherever the app writes one.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        if is_utc:
            value = value + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)
        hour = value.hour
    else:
        hour = getattr(value, "hour", None)
    if hour is None:
        return None

    for start, end, shift in SHIFT_BOUNDS:
        if start <= hour < end:
            return shift
    return NIGHT_SHIFT


def stage_shift(stage):
    """The shift a stage decision was recorded in.

    ``stage_time`` is the shop-floor time the operator typed, but only the
    Annealing row has an input for it — roughly 3% of production stages carry
    one. ``updated_at`` is set on every save and is the moment the decision was
    actually recorded, so it is the fallback. It is UTC, hence the flag.
    """
    if stage is None:
        return None
    if getattr(stage, "stage_time", None) is not None:
        return shift_for_time(stage.stage_time)
    stamp = getattr(stage, "updated_at", None) or getattr(stage, "created_at", None)
    return shift_for_time(stamp, is_utc=True)
