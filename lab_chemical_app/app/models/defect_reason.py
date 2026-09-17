"""
Defect Reason Model - Admin-configurable defect reasons, scoped per stage.

Managed under Settings > Defect Reasons (mirrors StageDefectType's per-stage
pattern) and surfaced as the <select> choices of each stage's "Defect Reason"
field in the stages form. Rows with no stage (legacy) apply to every stage.
"""

from datetime import datetime
from app import db


class DefectReason(db.Model):
    """Admin-configurable defect reason (Settings > Defect Reasons).

    Scoped per stage (like StageDefectType). A row with an empty/NULL
    ``stage_name`` is a legacy global row and applies to every stage.
    """

    __tablename__ = "defect_reasons"

    id = db.Column(db.Integer, primary_key=True)
    stage_name = db.Column(db.String(50), nullable=True, index=True)
    name_en = db.Column(db.String(150), nullable=False)
    name_ar = db.Column(db.String(150), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint("stage_name", "name_en", name="uix_defect_reason_stage_en"),
        db.UniqueConstraint("stage_name", "name_ar", name="uix_defect_reason_stage_ar"),
    )

    @classmethod
    def _active_rows(cls):
        try:
            return (
                cls.query.filter_by(is_active=True)
                .order_by(cls.sort_order, cls.id)
                .all()
            )
        except Exception:
            return []

    @classmethod
    def active_list(cls):
        """Ordered ``[(name_en, name_ar), ...]`` of ALL active defect reasons
        (any stage). Returns ``[]`` (never raises) on DB errors."""
        return [(r.name_en, r.name_ar) for r in cls._active_rows()]

    @classmethod
    def active_by_stage(cls):
        """``{stage_name: [(name_en, name_ar), ...], ...}`` for form dropdowns.

        Legacy global rows (no stage_name) are merged into every stage's
        list. Returns ``{}`` (never raises) on DB errors.
        """
        rows = cls._active_rows()
        if not rows:
            return {}
        global_rows = [(r.name_en, r.name_ar) for r in rows if not r.stage_name]
        by_stage = {}
        for r in rows:
            if r.stage_name:
                by_stage.setdefault(r.stage_name, []).append((r.name_en, r.name_ar))
        if global_rows:
            from app.models.stage import ProductionStage

            for stage_name in ProductionStage.active_names():
                merged = global_rows + by_stage.get(stage_name, [])
                by_stage[stage_name] = merged
        return by_stage

    def __repr__(self):
        return f"<DefectReason {self.name_en}>"
