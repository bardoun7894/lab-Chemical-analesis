"""
Mechanical Test Model
"""
from datetime import datetime
from app import db


class MechanicalTest(db.Model):
    """Mechanical Tests - Sheet 4 Data"""
    __tablename__ = 'mechanical_tests'

    id = db.Column(db.Integer, primary_key=True)

    # Identification
    test_date = db.Column(db.Date, nullable=False, index=True)
    test_number = db.Column(db.Integer)
    diameter = db.Column(db.Integer)  # 300, 500, etc.
    code = db.Column(db.String(50))  # A2989, V4783
    pipe_no = db.Column(db.Integer)
    ladle_id = db.Column(db.String(20), db.ForeignKey('chemical_analyses.ladle_id'), index=True)
    day = db.Column(db.Integer)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)

    # Sample Measurements
    sample_thickness = db.Column(db.Float)
    d1 = db.Column(db.Float)
    d2 = db.Column(db.Float)
    d3 = db.Column(db.Float)
    avg_dimension = db.Column(db.Float)  # AVD
    original_length = db.Column(db.Float)  # Lo
    final_length = db.Column(db.Float)  # Lf
    area_d_squared = db.Column(db.Float)  # (A) D^2

    # Test Results
    force_kgf = db.Column(db.Float)  # F =Kgf
    tensile_strength = db.Column(db.Float)  # sigma = F/A
    elongation = db.Column(db.Float)  # E = (Lf-Lo)/Lo x100

    # Microstructure Analysis
    microstructure = db.Column(db.Text)
    percent_85 = db.Column(db.Float)  # >85%
    percent_70 = db.Column(db.Float)  # >70%
    percent_40 = db.Column(db.Float)  # >40%
    percent_1 = db.Column(db.Float)  # <1%
    nodularity_percent = db.Column(db.Float)  # %Nd
    nodule_count = db.Column(db.Integer)  # NC
    hardness = db.Column(db.Float)
    carbides = db.Column(db.Float)

    # Quality Control
    shift = db.Column(db.Integer)
    tester_name = db.Column(db.String(100))
    decision = db.Column(db.String(20))
    reason = db.Column(db.Text)
    has_defect = db.Column(db.Boolean, default=False)
    defect_reason = db.Column(db.Text)
    comments = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    chemical_analysis = db.relationship('ChemicalAnalysis', back_populates='mechanical_tests')

    def calculate_derived_values(self):
        """Calculate derived values from measurements"""
        # Calculate average dimension
        if self.d1 and self.d2 and self.d3:
            self.avg_dimension = (self.d1 + self.d2 + self.d3) / 3

        # Calculate elongation
        if self.original_length and self.final_length and self.original_length > 0:
            self.elongation = ((self.final_length - self.original_length) / self.original_length) * 100

        # Calculate tensile strength
        if self.force_kgf and self.area_d_squared and self.area_d_squared > 0:
            self.tensile_strength = self.force_kgf / self.area_d_squared

    def __repr__(self):
        return f'<MechanicalTest {self.code} - {self.test_date}>'
