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

    # Link to Production Order (امر الانتاج)
    production_order_id = db.Column(db.Integer, db.ForeignKey('production_orders.id'), index=True)

    # Pipe Identification
    pipe_code = db.Column(db.String(50))
    diameter = db.Column(db.Integer)  # DN: 300, 500, 600
    pipe_class = db.Column(db.String(20))  # K9, C25, Fittings
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
    # chemical_analysis and production_order relationships defined via backref
    stages = db.relationship('PipeStage', back_populates='pipe', lazy='dynamic',
                            cascade='all, delete-orphan')
    mechanical_tests = db.relationship('MechanicalTest', backref='pipe', lazy='dynamic')

    # Production stages
    STAGES = ['Melting Ladle', 'CCM', 'Annealing', 'Lab', 'Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating', 'Finish']

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
    decision = db.Column(db.String(100))
    reason = db.Column(db.Text)
    has_defect = db.Column(db.Boolean, default=False)
    defect_type_id = db.Column(db.Integer, db.ForeignKey('defect_types.id'))
    defect_type = db.Column(db.String(100))  # Stage-specific defect type
    defect_reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Machine used for this stage (for production stages like Zinc, Cutting, Hydrotest, Cement, Coating)
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    pipe = db.relationship('Pipe', back_populates='stages')
    defect_type_obj = db.relationship('DefectType', back_populates='pipe_stages', foreign_keys=[defect_type_id])
    machine = db.relationship('Machine', backref='pipe_stages')

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

    # Stage-specific decisions (Melting Ladle, CCM, Annealing, Lab, Zinc, Cutting, Hydrotest, Cement, Coating, Finish)
    STAGE_DECISIONS = {
        'Melting Ladle': [
            ('Inspect Last pipes', 'فحص أخيرة فقط'),
            ('Inspect 1st and Last pipes', 'فحص أولى وأخيرة'),
            ('Inspect 100%', 'فحص الشحنة 100%'),
            ('Reject', 'تالف')
        ],
        'CCM': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Accept with remark', 'قبول مع ملاحظة'),
            ('DownGrade', 'تخفيض درجة'),
            ('Hold', 'حجز')
        ],
        'Annealing': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Accept with remark', 'قبول مع ملاحظة'),
            ('Hold', 'حجز'),
            ('Reheat treatment', 'إعادة معالجة حرارية'),
            ('Resample', 'إعادة عينة')
        ],
        'Lab': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Accept with remark', 'قبول مع ملاحظة'),
            ('Hold', 'حجز'),
            ('Reheat treatment', 'إعادة معالجة حرارية'),
            ('Resample', 'إعادة عينة')
        ],
        'Zinc': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز'),
            ('Rework', 'إعادة عمل'),
            ('Micro-structure', 'فحص البنية المجهرية')
        ],
        'Cutting': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز'),
            ('Retest', 'إعادة اختبار')
        ],
        'Hydrotest': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز'),
            ('Rework', 'إعادة عمل')
        ],
        'Cement': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز'),
            ('Rework', 'إعادة عمل')
        ],
        'Coating': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز'),
            ('Rework', 'إعادة عمل')
        ],
        'Finish': [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز')
        ]
    }

    # Stage-specific defects
    STAGE_DEFECTS = {
        'Melting Ladle': [
            ('Out of specification', 'خارج المواصفات'),
            ('دلاليـك', 'Dalek'),
            ('رينـــكل', 'Wrinkle'),
            ('تكتل معدن/عتبة', 'Metal clustering/threshold'),
            ('طى معدن', 'Metal folding'),
            ('شروخ', 'Cracks'),
            ('حفر', 'Pits'),
            ('توريق LA', 'LA Flaking'),
            ('سمك عالى', 'High thickness'),
            ('سمك ضعيف', 'Low thickness'),
            ('جرافيت', 'Graphite'),
            ('جلخ/خبط SL', 'Grinding/slag'),
            ('تطعيم', 'Inoculation'),
            ('كسر فى الرأس', 'Head break'),
            ('بدون مصد', 'Without socket'),
            ('D4', 'D4'),
            ('Short pipe', 'أنبوب قصير'),
            ('تحليل شحنة', 'Batch analysis'),
            ('بيضاوى', 'Oval'),
            ('تقوس CU', 'Curvature'),
            ('عيب مناولة', 'Handling defect'),
            ('Other', 'أخرى')
        ],
        'CCM': [
            ('دلاليـك', 'Dalek'),
            ('رينـــكل', 'Wrinkle'),
            ('تكتل معدن/عتبة', 'Metal clustering/threshold'),
            ('طى معدن', 'Metal folding'),
            ('شروخ', 'Cracks'),
            ('حفر', 'Pits'),
            ('توريق LA', 'LA Flaking'),
            ('سمك عالى', 'High thickness'),
            ('سمك ضعيف', 'Low thickness'),
            ('جرافيت', 'Graphite'),
            ('جلخ/خبط SL', 'Grinding/slag'),
            ('تطعيم', 'Inoculation'),
            ('كسر فى الرأس', 'Head break'),
            ('بدون مصد', 'Without socket'),
            ('D4', 'D4'),
            ('بيضاوى', 'Oval'),
            ('تقوس CU', 'Curvature'),
            ('عيب مناولة', 'Handling defect'),
            ('Other', 'أخرى')
        ],
        'Annealing': [
            ('مناوله', 'Handling'),
            ('شرخ', 'Crack'),
            ('بيضاوى', 'Oval'),
            ('Other', 'أخرى')
        ],
        'Lab': [
            ('زهر رمادى', 'Gray cast'),
            ('خواص ميكانيكيه', 'Mechanical properties'),
            ('رينج تيست', 'Ring test'),
            ('Other', 'أخرى')
        ],
        'Zinc': [],  # No specific defects listed
        'Cutting': [
            ('مناوله', 'Handling'),
            ('خواص ميكانيكيه', 'Mechanical properties'),
            ('انتفاخ', 'Bulging'),
            ('كسر على المكبس', 'Break on press'),
            ('قصر طول', 'Short length'),
            ('شروخ', 'Cracks'),
            ('Other', 'أخرى')
        ],
        'Hydrotest': [
            ('مناوله', 'Handling'),
            ('تسريب', 'Leakage'),
            ('Other', 'أخرى')
        ],
        'Cement': [
            ('سمك', 'Thickness'),
            ('تالف دهان', 'Paint damage'),
            ('تالف اسمنت', 'Cement damage'),
            ('Other', 'أخرى')
        ],
        'Coating': [
            ('مناوله', 'Handling'),
            ('تالف دهان', 'Paint damage'),
            ('تالف اسمنت', 'Cement damage'),
            ('Other', 'أخرى')
        ],
        'Finish': [
            ('مناوله', 'Handling'),
            ('Other', 'أخرى')
        ]
    }

    @classmethod
    def get_decisions_for_stage(cls, stage_name):
        """Get allowed decisions for a specific stage"""
        return cls.STAGE_DECISIONS.get(stage_name, [
            ('Accept', 'قبول'),
            ('Reject', 'رفض'),
            ('Hold', 'حجز')
        ])

    @classmethod
    def get_defects_for_stage(cls, stage_name):
        """Get allowed defects for a specific stage"""
        return cls.STAGE_DEFECTS.get(stage_name, [('Other', 'أخرى')])

    def __repr__(self):
        return f'<PipeStage {self.pipe_id}:{self.stage_name}>'
