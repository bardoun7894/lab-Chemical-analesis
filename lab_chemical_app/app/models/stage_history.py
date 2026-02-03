"""
Stage History Model - Tracks all changes to pipe stages
"""
from datetime import datetime
from app import db


class PipeStageHistory(db.Model):
    """History of all changes to pipe stages"""
    __tablename__ = 'pipe_stage_history'

    id = db.Column(db.Integer, primary_key=True)
    pipe_stage_id = db.Column(db.Integer, db.ForeignKey('pipe_stages.id'), nullable=False)
    pipe_id = db.Column(db.Integer, db.ForeignKey('pipes.id'), nullable=False)
    stage_name = db.Column(db.String(50), nullable=False)

    # Snapshot of stage data at time of change
    decision = db.Column(db.String(100))
    reason = db.Column(db.Text)
    machine_id = db.Column(db.Integer)
    machine_code = db.Column(db.String(20))  # Store code for historical reference
    has_defect = db.Column(db.Boolean, default=False)
    defect_type = db.Column(db.String(100))
    defect_reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    measurement_value = db.Column(db.Float)
    measurement_type = db.Column(db.String(50))
    stage_date = db.Column(db.Date)
    stage_time = db.Column(db.Time)

    # Change tracking
    action = db.Column(db.String(20), default='update')  # create, update, delete
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    pipe_stage = db.relationship('PipeStage', backref='history')
    pipe = db.relationship('Pipe', backref='stage_history')
    changed_by = db.relationship('User', backref='stage_changes')

    def __repr__(self):
        return f'<PipeStageHistory {self.stage_name} - {self.action} at {self.changed_at}>'

    @classmethod
    def create_from_stage(cls, stage, action='update', user_id=None):
        """Create a history record from a PipeStage object"""
        history = cls(
            pipe_stage_id=stage.id,
            pipe_id=stage.pipe_id,
            stage_name=stage.stage_name,
            decision=stage.decision,
            reason=stage.reason,
            machine_id=stage.machine_id,
            machine_code=stage.machine.machine_code if stage.machine else None,
            has_defect=stage.has_defect,
            defect_type=stage.defect_type,
            defect_reason=stage.defect_reason,
            notes=stage.notes,
            measurement_value=stage.measurement_value,
            measurement_type=stage.measurement_type,
            stage_date=stage.stage_date,
            stage_time=stage.stage_time,
            action=action,
            changed_by_id=user_id
        )
        return history
