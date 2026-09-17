"""Breaches of the KPI targets an admin set, for one production day.

The daily report already printed alerts, but against numbers written into the
code — a machine over 7% rejects was "تدخل فوري", over 4% was "متابعة" — so a
plant that had decided its limit was 3% was never told. This module answers
the same question from `/admin/settings/kpi-targets` instead, which is the
only place the limit is a management decision rather than a developer's.

Three scopes, because "the reject rate is fine" can be true of the plant and
false of the machine standing idle behind a scrap pile:

  plant    each KPI reading for the day against its own target
  machine  every machine's own reject rate against `machine_reject_pct`
  stage    every stage's own reject rate against `stage_reject_pct`
  mould    every mould's own reject rate against `mold_reject_pct`
  shift    every shift's own reject rate against `shift_reject_pct`
  DN       every diameter's own reject rate against `dn_reject_pct`
  month    the month to date against `month_reject_pct`, judged once

A scope with no target set raises nothing. That is deliberate and matches
kpi_target_service: this code never invents a limit, so an empty settings page
means a quiet system rather than one grading against guessed numbers.

The day is the unit, not the shift: the client asked for the day explicitly —
a shift is too small a sample for a rate to mean anything, and the same three
rejects would raise the same alert three times over.
"""

from datetime import date as _date

from app.services import kpi_readings_service, kpi_target_service

# A machine or a stage with almost nothing on it will show 100% rejects off a
# single pipe. That is noise, not a signal, so a scope has to have produced at
# least this many decided pipes before its rate is judged.
MIN_SAMPLE = kpi_readings_service.MIN_SAMPLE

SEVERITY_WARN = "warn"
SEVERITY_CRITICAL = "critical"

# How far past its target a reading has to be before it stops being a warning.
# Relative, so it scales with the target itself: a plant aiming at 2% rejects
# is in trouble at 4%, one aiming at 10% is not.
CRITICAL_FACTOR = 2.0


def _severity(value, target, direction):
    """Warning, or bad enough to stop what you are doing."""
    if direction == kpi_target_service.LOWER_IS_BETTER:
        return (SEVERITY_CRITICAL if target > 0 and value >= target * CRITICAL_FACTOR
                else SEVERITY_WARN)
    if target > 0 and value <= target / CRITICAL_FACTOR:
        return SEVERITY_CRITICAL
    return SEVERITY_WARN


def _breach(key, value, target, scope, scope_key=None, sample=None):
    direction = kpi_target_service.direction(key)
    unit = kpi_target_service.unit(key)
    name = kpi_target_service.label(key)
    where = f" — {scope_key}" if scope_key else ""
    over = "تجاوز" if direction == kpi_target_service.LOWER_IS_BETTER else "أقل من"
    return {
        # Identity of the breach, not of the row: the same problem found again
        # tomorrow is a new alert, found again this afternoon is the same one.
        "key": key,
        "scope": scope,
        "scope_key": scope_key,
        "value": round(value, 2),
        "target": target,
        "unit": unit,
        "sample": sample,
        "severity": _severity(value, target, direction),
        "message": (f"{name}{where}: {round(value, 2)}{unit} "
                    f"{over} الهدف {target}{unit}"
                    + (f" (على {sample} ماسورة)" if sample else "")),
    }


def _rate_breaches(rows, target_key, scope, targets):
    """One breach per group whose reject rate crosses the scoped target."""
    target = targets.get(target_key)
    if target is None:
        return []
    out = []
    for key, tally in rows.items():
        decided = tally["decided"]
        if decided < MIN_SAMPLE:
            continue
        pct = tally["rejects"] / decided * 100
        if pct > target:
            out.append(_breach(target_key, pct, target, scope, key, decided))
    return sorted(out, key=lambda b: -b["value"])


def _judgeable(key, value, counts, day, today):
    """Whether this reading can be held against its target yet.

    Two ways a reading lies early in the shift:

    A rate off almost nothing. Six pipes registered and none weighed yet reads
    as a 0% yield, and the first version of this alerted "critical" on it at
    nine in the morning. Rates wait for a real sample.

    A running total. Today's tonnage is under any sensible target for most of
    the day, so a total is only judged once the day is over — otherwise the
    alert is not reporting the plant, it is reporting the clock.
    """
    if value is None:
        return False
    if key in kpi_readings_service.TOTAL_KEYS:
        return day < today
    sample = counts.get("decided", 0)
    if key == "chem_reject_pct":
        sample = counts.get("chem_decided", 0)
    elif key == "mech_fail_pct":
        sample = counts.get("mech_decided", 0)
    elif key in ("yield_pct", "saving_pct"):
        # A weight reading is about the pipes that reached the scale. Six
        # registered and none weighed is not a 0% yield, it is a morning.
        sample = counts.get("weighed", 0)
    elif key in ("pending_pct", "hold_pct", "defect_pct", "rework_pct"):
        # Measured against everything registered that day, not only what has
        # been decided — but still not against three pipes.
        sample = counts.get("pipes", 0)
    return sample >= MIN_SAMPLE


def evaluate_day(day=None, settings=None):
    """Every target breached on `day`. Newest data, read-only, no side effects.

    Returns a list of breach dicts, worst first. Empty means either a good day
    or an empty settings page — `has_targets` tells the caller which, so a
    screen can say "no targets set" instead of implying all is well.
    """
    day = day or _date.today()
    targets = kpi_target_service.load(settings)
    if not targets:
        return []

    values = kpi_readings_service.readings(day)
    counts = values.get("_counts") or {}
    if not counts.get("pipes"):
        return []

    # The daily plant reject rate answers to one target even though two keys
    # can carry it (`day_reject_pct` is the explicit name, `reject_pct` the
    # original). Resolve the winner once and judge the reading under that key,
    # so setting both does not raise the same problem twice.
    day_key, day_target = kpi_target_service.day_reject_target(targets)

    breaches = []
    today = _date.today()
    for key, _ar, _en, _unit, _dir in kpi_target_service.KPIS:
        if key == "reject_pct":
            continue  # handled below, under whichever key is set
        value = values.get(key)
        if not _judgeable(key, value, counts, day, today):
            continue
        verdict = kpi_target_service.evaluate(key, value, targets)
        if verdict and not verdict["met"]:
            breaches.append(_breach(key, value, verdict["target"], "plant"))

    reject_value = values.get("reject_pct")
    if (day_key and _judgeable("reject_pct", reject_value, counts, day, today)
            and reject_value > day_target):
        breaches.append(_breach(day_key, reject_value, day_target, "plant"))

    # The same day cut four ways. The plant reading above already answers "did
    # the day cross the line"; these answer "where", which is the only version
    # of the question anybody can act on.
    scoped = kpi_readings_service.scoped_rates(day)
    for scope, key in (("machine", "machine_reject_pct"),
                       ("stage", "stage_reject_pct"),
                       ("mold", "mold_reject_pct"),
                       ("shift", "shift_reject_pct"),
                       ("dn", "dn_reject_pct")):
        breaches += _rate_breaches(scoped[scope], key, scope, targets)

    # The month to date, judged once — not per day, or the same drifting month
    # would raise the same alert every morning until it ended.
    month = kpi_readings_service.month_reject_rate(day)
    month_target = targets.get("month_reject_pct")
    if (month_target is not None and month["pct"] is not None
            and month["decided"] >= MIN_SAMPLE and month["pct"] > month_target):
        breaches.append(_breach("month_reject_pct", month["pct"], month_target,
                                "month", month["label"], month["decided"]))

    severity_order = {SEVERITY_CRITICAL: 0, SEVERITY_WARN: 1}
    breaches.sort(key=lambda b: (severity_order[b["severity"]], -abs(b["value"])))
    return breaches


def has_targets(settings=None):
    """Whether anything is configured at all — an empty page is not a clean bill."""
    return bool(kpi_target_service.load(settings))


# ---------------------------------------------------------------------------
# Storing them: the bell, the banner, and the one WhatsApp message
# ---------------------------------------------------------------------------

# How stale the stored alerts for a day may get before the next reader
# recalculates them. Short enough that the floor sees a machine go over within
# the same tea break, long enough that a busy screen is not re-aggregating the
# whole day on every request of every worker.
REFRESH_SECONDS = 300


def sync_day(day=None, force=False):
    """Bring the stored alerts for `day` in line with the live readings.

    Opens what is newly over the line, updates what is still over it, and
    resolves what has come back — a machine that recovers by the afternoon
    stops shouting, but its row stays, because "we were over the line this
    morning" is worth knowing.

    Returns the day's open alerts, newest problem first.
    """
    from datetime import datetime

    from app import db
    from app.models.kpi_alert import KpiAlert, KpiAlertRun

    day = day or _date.today()
    run = db.session.get(KpiAlertRun, day)
    fresh = (
        run is not None
        and (datetime.utcnow() - run.ran_at).total_seconds() < REFRESH_SECONDS
    )
    if fresh and not force:
        return open_alerts(day)

    breaches = {(b["key"], b["scope"], b["scope_key"] or ""): b
                for b in evaluate_day(day)}
    existing = {(a.kpi_key, a.scope, a.scope_key): a
                for a in KpiAlert.query.filter_by(day=day).all()}
    now = datetime.utcnow()

    for identity, breach in breaches.items():
        alert = existing.get(identity)
        if alert is None:
            alert = KpiAlert(day=day, kpi_key=breach["key"],
                             scope=breach["scope"],
                             scope_key=breach["scope_key"] or "")
            db.session.add(alert)
        alert.value = breach["value"]
        alert.target = breach["target"]
        alert.unit = breach["unit"]
        alert.severity = breach["severity"]
        alert.message = breach["message"]
        # Back over the line after recovering: it is the same problem again,
        # and it should be unread again.
        if alert.resolved_at is not None:
            alert.resolved_at = None
            alert.read_at = None
            alert.sent_at = None

    for identity, alert in existing.items():
        if identity not in breaches and alert.resolved_at is None:
            alert.resolved_at = now

    if run is None:
        run = KpiAlertRun(day=day)
        db.session.add(run)
    run.ran_at = now

    try:
        db.session.commit()
    except Exception:
        # Four gunicorn workers can reach this at once and the identity is
        # unique; whoever lost the race already wrote the same rows.
        db.session.rollback()

    return open_alerts(day)


def open_alerts(day=None):
    """The day's unresolved alerts, worst first."""
    from app.models.kpi_alert import KpiAlert

    day = day or _date.today()
    alerts = KpiAlert.query.filter_by(day=day, resolved_at=None).all()
    order = {SEVERITY_CRITICAL: 0, SEVERITY_WARN: 1}
    return sorted(alerts, key=lambda a: (order.get(a.severity, 2),
                                         -(a.value or 0)))


def unread_count(day=None):
    from app.models.kpi_alert import KpiAlert

    return KpiAlert.query.filter_by(
        day=day or _date.today(), resolved_at=None, read_at=None).count()


def push_pending(day=None, settings=None):
    """Send today's un-sent alerts to WhatsApp. Returns how many went out.

    `sent_at` is stamped per alert, so a breach that persists all day is one
    message, not one per refresh — the same reason the rows exist at all. A
    send that fails leaves the stamp empty and the next run tries again.

    Nothing is sent unless an admin turned it on and entered the numbers: a
    message on someone's phone is not something a default in a config file
    should be able to cause.
    """
    from datetime import datetime

    from app import db
    from app.models.kpi_alert import KpiAlert
    from app.services import whatsapp_service

    if not whatsapp_service.is_configured(settings):
        return 0

    day = day or _date.today()
    min_severity = whatsapp_service.config(settings)["min_severity"]
    pending = [
        a for a in KpiAlert.query.filter_by(day=day, resolved_at=None,
                                            sent_at=None).all()
        if min_severity != SEVERITY_CRITICAL or a.severity == SEVERITY_CRITICAL
    ]
    if not pending:
        return 0

    header = f"تنبيه مؤشرات — {day.isoformat()}"
    body = "\n".join(f"• {a.message}" for a in pending)
    if whatsapp_service.send(f"{header}\n{body}", settings) == 0:
        return 0

    now = datetime.utcnow()
    for alert in pending:
        alert.sent_at = now
    db.session.commit()
    return len(pending)
