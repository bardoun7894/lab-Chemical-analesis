"""
Stage Defect Type Model - Configurable defect types for each production stage
"""
from datetime import datetime
from app import db


class StageDefectType(db.Model):
    """Configurable defect types for production stages"""
    __tablename__ = 'stage_defect_types'

    id = db.Column(db.Integer, primary_key=True)
    stage_name = db.Column(db.String(50), nullable=False, index=True)
    defect_name_en = db.Column(db.String(100), nullable=False)
    defect_name_ar = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<StageDefectType {self.stage_name}: {self.defect_name_en}>'
