"""A KPI target breach, kept so it can be seen, read, and sent once.

Computing the breaches is cheap enough to do live (kpi_alert_service does),
but three of the things asked of an alert cannot be done without a record of
it: a bell that says how many are unread, a WhatsApp message that goes out
once rather than on every page load, and a breach that stops shouting once
the day recovers.

One row per (day, kpi, scope, scope key): the same machine over the same
target at 11:00 and again at 15:00 is one problem, not two. The value and
severity are updated in place as the day goes on.
"""

from datetime import datetime

from app import db


class KpiAlert(db.Model):
    __tablename__ = "kpi_alerts"
    __table_args__ = (
        db.UniqueConstraint("day", "kpi_key", "scope", "scope_key",
                            name="uq_kpi_alert_identity"),
        db.Index("ix_kpi_alerts_day", "day"),
    )

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False)
    kpi_key = db.Column(db.String(50), nullable=False)
    # "plant" | "machine" | "stage"
    scope = db.Column(db.String(20), nullable=False, default="plant")
    # The machine code or stage name; "" for plant-wide, never NULL — a NULL
    # never equals a NULL, so the unique constraint would stop deduplicating.
    scope_key = db.Column(db.String(100), nullable=False, default="")

    value = db.Column(db.Float)
    target = db.Column(db.Float)
    unit = db.Column(db.String(20))
    severity = db.Column(db.String(20), default="warn")
    message = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    # Set when the reading came back inside its target. Kept rather than
    # deleted: "we were over the line this morning" is worth knowing.
    resolved_at = db.Column(db.DateTime)
    read_at = db.Column(db.DateTime)
    read_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # When it was pushed out of the app (WhatsApp), so it is sent once.
    sent_at = db.Column(db.DateTime)

    read_by = db.relationship("User")

    @property
    def is_open(self):
        return self.resolved_at is None

    def __repr__(self):
        return f"<KpiAlert {self.day} {self.kpi_key} {self.scope_key}>"


class KpiAlertRun(db.Model):
    """When the breaches for a day were last recalculated.

    The app has no scheduler, so the recalculation is triggered by whoever
    looks at the bell. That would mean every request of every one of the four
    gunicorn workers redoing the whole day's aggregation, so this row throttles
    it. In the database rather than in memory, because the workers are separate
    processes and an in-memory throttle would fire four times over.
    """

    __tablename__ = "kpi_alert_runs"

    day = db.Column(db.Date, primary_key=True)
    ran_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
