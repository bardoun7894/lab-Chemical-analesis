"""KPI alerts — the bell, the list, and marking them read.

The app has no scheduler, so the day's readings are recalculated by whoever
looks: the bell polls `/alerts/feed`, and that call syncs the day (throttled
in the database, see kpi_alert_service.REFRESH_SECONDS). A cron can call the
same sync for the WhatsApp push without the app needing a background worker.

Guarded by the analytics/kpis permission rather than a new one of its own: an
alert is a KPI reading, whoever may see the KPI may see that it was missed,
and a brand-new permission would be denied to everyone until an admin went and
granted it — the matrix fails closed. See [[permissions-matrix-authority]].
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models.kpi_alert import KpiAlert
from app.services import kpi_alert_service
from app.services.permission_service import requires_permission

alerts_bp = Blueprint("alerts", __name__)

# How far back the list screen looks. A month is enough to see a pattern
# without turning the page into an archive nobody reads.
HISTORY_DAYS = 30

# How far back the reject-rate trend looks. Wide enough that the month rows
# carry a previous month to compare against, not so wide the daily table
# becomes a scroll.
TREND_DAYS = 60


def _parse_day(raw):
    try:
        return date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return date.today()


@alerts_bp.route("/feed")
@login_required
@requires_permission("analytics", "kpis")
def feed():
    """What the bell shows: today's open alerts and how many are unread."""
    day = _parse_day(request.args.get("day"))
    open_alerts = kpi_alert_service.sync_day(day)
    return jsonify({
        "day": day.isoformat(),
        "unread": kpi_alert_service.unread_count(day),
        "total": len(open_alerts),
        "has_targets": kpi_alert_service.has_targets(),
        "alerts": [
            {
                "id": a.id,
                "message": a.message,
                "severity": a.severity,
                "scope": a.scope,
                "scope_key": a.scope_key,
                "read": a.read_at is not None,
            }
            for a in open_alerts[:10]
        ],
    })


@alerts_bp.route("/")
@login_required
@requires_permission("analytics", "kpis")
def index():
    """Every alert of the last month, open ones first."""
    since = date.today() - timedelta(days=HISTORY_DAYS)
    kpi_alert_service.sync_day(date.today())
    rows = (
        KpiAlert.query.filter(KpiAlert.day >= since)
        .order_by(KpiAlert.day.desc(), KpiAlert.severity, KpiAlert.id.desc())
        .all()
    )
    # Today's whole scoreboard, not only what went wrong: a KPI sitting just
    # inside its target is the one worth watching, and it raises nothing.
    from app.services import kpi_readings_service, kpi_target_service

    values = kpi_readings_service.readings(date.today())
    targets = kpi_target_service.load()

    # The reject-rate tile reads whichever of the two day keys is set, so a
    # target entered as `day_reject_pct` still grades the tile.
    scoreboard_targets = dict(targets)
    day_target = kpi_target_service.day_reject_target(targets)[1]
    if day_target is not None:
        scoreboard_targets["reject_pct"] = day_target

    scoreboard = [
        {
            "key": key,
            "label": label_ar if request.cookies.get("locale") == "ar" else label_en,
            "label_ar": label_ar,
            "label_en": label_en,
            "unit": unit,
            "value": values.get(key),
            "target": scoreboard_targets.get(key),
            "verdict": kpi_target_service.evaluate(
                key, values.get(key), scoreboard_targets),
        }
        for key, label_ar, label_en, unit, _dir in kpi_target_service.KPIS
    ]

    return render_template(
        "alerts/index.html",
        alerts=rows,
        open_count=sum(1 for a in rows if a.is_open),
        has_targets=kpi_alert_service.has_targets(),
        history_days=HISTORY_DAYS,
        scoreboard=scoreboard,
        counts=values.get("_counts") or {},
        # The day's reject rate cut by machine, stage, mould and shift. The
        # plant number says the day was over the line; these say where.
        breakdown=kpi_readings_service.scoped_table(date.today(), targets),
        min_sample=kpi_readings_service.MIN_SAMPLE,
        # The same rate over time. The cuts above answer "where"; these answer
        # "is it getting worse", which no single day can.
        trend=kpi_readings_service.reject_trend(TREND_DAYS),
        trend_days=TREND_DAYS,
        # One number governs the day even though two keys can carry it.
        trend_targets={
            "day": kpi_target_service.day_reject_target(targets)[1],
            "month": targets.get("month_reject_pct"),
        },
    )


@alerts_bp.route("/read", methods=["POST"])
@login_required
@requires_permission("analytics", "kpis")
def mark_read():
    """Mark one alert read, or every open one when no id is given."""
    alert_id = request.form.get("id", type=int)
    query = KpiAlert.query.filter_by(read_at=None)
    if alert_id:
        query = query.filter_by(id=alert_id)
    else:
        query = query.filter_by(resolved_at=None)
    now = datetime.utcnow()
    for alert in query.all():
        alert.read_at = now
        alert.read_by_id = current_user.id
    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True,
                        "unread": kpi_alert_service.unread_count()})
    return redirect(url_for("alerts.index"))
