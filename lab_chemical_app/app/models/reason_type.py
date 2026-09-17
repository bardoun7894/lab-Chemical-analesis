"""
Reason Type Model - Admin-configurable decision/change reasons.

A single GLOBAL list (not scoped per-stage, unlike StageDecisionType /
StageDefectType). Managed under Settings > Reason Types and surfaced as a
quick-pick dropdown next to the free-text "reason for change" fields on the
chemical and mechanical decision forms. The free-text field is always kept as
the source of truth; a reason is only ever inserted into it, never enforced.
"""

from datetime import datetime
from app import db


class ReasonType(db.Model):
    """Admin-configurable global reason (Settings > Reason Types)."""

    __tablename__ = "reason_types"

    id = db.Column(db.Integer, primary_key=True)
    name_en = db.Column(db.String(150), nullable=False)
    name_ar = db.Column(db.String(150), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint("name_en", name="uix_reason_name_en"),
        db.UniqueConstraint("name_ar", name="uix_reason_name_ar"),
    )

    @classmethod
    def active_list(cls):
        """Ordered ``[(name_en, name_ar), ...]`` of active reasons.

        Returns ``[]`` (never raises) if the table is missing or the DB is
        unavailable, so form templates can always call it during boot / CLI.
        """
        try:
            rows = (
                cls.query.filter_by(is_active=True)
                .order_by(cls.sort_order, cls.id)
                .all()
            )
            return [(r.name_en, r.name_ar) for r in rows]
        except Exception:
            return []

    def __repr__(self):
        return f"<ReasonType {self.name_en}>"
