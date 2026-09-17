"""
Corrective-action ("Cover") record attached to a non-conforming item on the
Non-Conformance Register.

The Register rolls up four different sources (chemistry, mechanical, stage,
pipe) into one list. To avoid coupling this feature to four different
underlying tables, the natural key is the same pair the Register already uses
to identify a row: ``(source, source_code)``.
"""
from datetime import datetime

from app import db


class NonConformanceAction(db.Model):
    """One corrective-action record per register row."""

    __tablename__ = "non_conformance_actions"

    source = db.Column(db.String(20), primary_key=True)
    source_code = db.Column(db.String(64), primary_key=True)

    root_cause = db.Column(db.Text, nullable=True)
    corrective_action = db.Column(db.Text, nullable=True)
    responsible = db.Column(db.String(120), nullable=True)
    responsible_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Open")

    created_by = db.Column(db.String(64), nullable=True)
    updated_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # The four statuses the manager can pick. Kept here so the model and the
    # UI agree; the route and the template import this constant.
    STATUSES = ("Open", "In Progress", "Done", "Cancelled")
    DEFAULT_STATUS = "Open"

    def to_dict(self):
        return {
            "source": self.source,
            "source_code": self.source_code,
            "root_cause": self.root_cause or "",
            "corrective_action": self.corrective_action or "",
            "responsible": self.responsible or "",
            "responsible_date": (
                self.responsible_date.isoformat() if self.responsible_date else ""
            ),
            "status": self.status or self.DEFAULT_STATUS,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<NonConformanceAction {self.source}:{self.source_code} {self.status}>"