"""
Pipe and Stage Models
"""
from datetime import datetime
from app import db


class Pipe(db.Model):
    """Pipes - Sheet 3 Main Production Data"""
    __tablename__ = 'pipes'

    id = db.Column(db.Integer, primary_key=True)

    # Production Info
    production_date = db.Column(db.Date, nullable=False, index=True)
    shift = db.Column(db.Integer)
    shift_engineer = db.Column(db.String(100))
    manufacturing_order = db.Column(db.String(50))

    # Pipe Identification
    pipe_code = db.Column(db.String(50))
    diameter = db.Column(db.Integer)  # DN: 300, 500, 600
    pipe_type = db.Column(db.String(20))  # K9, C25, Fittings
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'))
    mold_number = db.Column(db.String(20))
    iso_weight = db.Column(db.Float)
    no_code = db.Column(db.String(50), unique=True, index=True)  # N8739, N8740...
    arrange_pipe = db.Column(db.Integer)  # Sequence 1-6

    # Link to Chemical Analysis
    ladle_id = db.Column(db.String(20), db.ForeignKey('chemical_analyses.ladle_id'))

    # Measurements
    thickness = db.Column(db.Float)
    actual_weight = db.Column(db.Float)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    machine = db.relationship('Machine', back_populates='pipes')
    chemical_analysis = db.relationship('ChemicalAnalysis', back_populates='pipes')
    stages = db.relationship('PipeStage', back_populates='pipe', lazy='dynamic',
                            cascade='all, delete-orphan')

    # Production stages
    STAGES = ['CCM', 'Annealing', 'Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating', 'Finish']

    def get_stage(self, stage_name):
        """Get specific stage data"""
        return PipeStage.query.filter_by(pipe_id=self.id, stage_name=stage_name).first()

    def get_all_stages_status(self):
        """Get status of all stages"""
        stages_status = {}
        for stage_name in self.STAGES:
            stage = self.get_stage(stage_name)
            if stage:
                stages_status[stage_name] = {
                    'decision': stage.decision,
                    'has_defect': stage.has_defect,
                    'completed': True
                }
            else:
                stages_status[stage_name] = {
                    'decision': None,
                    'has_defect': False,
                    'completed': False
                }
        return stages_status

    @property
    def current_stage(self):
        """Get the current/latest stage"""
        for stage_name in reversed(self.STAGES):
            stage = self.get_stage(stage_name)
            if stage and stage.decision:
                return stage_name
        return self.STAGES[0]  # Default to first stage

    @property
    def final_decision(self):
        """Get final product decision"""
        finish_stage = self.get_stage('Finish')
        return finish_stage.decision if finish_stage else None

    def __repr__(self):
        return f'<Pipe {self.no_code}>'


class PipeStage(db.Model):
    """Pipe Stages - 8 stages per pipe"""
    __tablename__ = 'pipe_stages'

    id = db.Column(db.Integer, primary_key=True)
    pipe_id = db.Column(db.Integer, db.ForeignKey('pipes.id'), nullable=False)
    stage_name = db.Column(db.String(50), nullable=False)

    # Stage timestamps
    stage_date = db.Column(db.Date)
    stage_time = db.Column(db.Time)

    # Stage measurements
    measurement_value = db.Column(db.Float)  # Zinc Slide, Cement thick, etc.
    measurement_type = db.Column(db.String(50))

    # Quality Control
    decision = db.Column(db.String(20))
    reason = db.Column(db.Text)
    has_defect = db.Column(db.Boolean, default=False)
    defect_type_id = db.Column(db.Integer, db.ForeignKey('defect_types.id'))
    defect_reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    pipe = db.relationship('Pipe', back_populates='stages')
    defect_type = db.relationship('DefectType', back_populates='pipe_stages')

    # Unique constraint
    __table_args__ = (
        db.UniqueConstraint('pipe_id', 'stage_name', name='uix_pipe_stage'),
    )

    # Stage-specific measurement types
    MEASUREMENT_TYPES = {
        'Zinc': 'Zinc Slide',
        'Cement': 'Cement Thickness',
        'Coating': 'Coating Thickness',
        'Finish': 'Length'
    }

    def __repr__(self):
        return f'<PipeStage {self.pipe_id}:{self.stage_name}>'
