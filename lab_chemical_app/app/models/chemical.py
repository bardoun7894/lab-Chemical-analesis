"""
Chemical Analysis and Reference Models
Migrated from database/models.py for Flask-SQLAlchemy
"""
from datetime import datetime
from app import db


class Furnace(db.Model):
    """Furnaces - A1, A2, B1, B2"""
    __tablename__ = 'furnaces'

    id = db.Column(db.Integer, primary_key=True)
    furnace_code = db.Column(db.String(10), unique=True, nullable=False)
    furnace_name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    chemical_analyses = db.relationship('ChemicalAnalysis', back_populates='furnace', lazy='dynamic')

    def __repr__(self):
        return f'<Furnace {self.furnace_code}>'


class Machine(db.Model):
    """Machines - M10, M11, AF1, ZC1, etc."""
    __tablename__ = 'machines'

    id = db.Column(db.Integer, primary_key=True)
    machine_code = db.Column(db.String(20), unique=True, nullable=False)
    machine_name = db.Column(db.String(100))
    stage = db.Column(db.String(50))  # CCM, Annealing, Zinc, etc.
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    pipes = db.relationship('Pipe', back_populates='machine', lazy='dynamic')

    def __repr__(self):
        return f'<Machine {self.machine_code}>'


class DefectType(db.Model):
    """Defect Types"""
    __tablename__ = 'defect_types'

    id = db.Column(db.Integer, primary_key=True)
    defect_code = db.Column(db.String(50))
    defect_name_ar = db.Column(db.String(100), nullable=False)
    defect_name_en = db.Column(db.String(100))
    applies_to_stages = db.Column(db.Text)  # JSON
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    pipe_stages = db.relationship('PipeStage', back_populates='defect_type_obj', lazy='dynamic')

    @property
    def display_name(self):
        """Get display name based on language preference"""
        return self.defect_name_en or self.defect_name_ar

    def __repr__(self):
        return f'<DefectType {self.defect_name_en}>'


class DecisionType(db.Model):
    """Decision Types - Accept, Reject, Hold, etc."""
    __tablename__ = 'decision_types'

    id = db.Column(db.Integer, primary_key=True)
    decision_code = db.Column(db.String(50), unique=True, nullable=False)
    decision_name_ar = db.Column(db.String(100))
    decision_name_en = db.Column(db.String(100))
    color_code = db.Column(db.String(20))

    def __repr__(self):
        return f'<DecisionType {self.decision_code}>'


class ElementSpecification(db.Model):
    """Element Specifications - Chemical limits"""
    __tablename__ = 'element_specifications'

    id = db.Column(db.Integer, primary_key=True)
    element_code = db.Column(db.String(10), nullable=False)
    element_name = db.Column(db.String(50))
    min_value = db.Column(db.Float)
    max_value = db.Column(db.Float)
    unit = db.Column(db.String(10), default='%')

    def check_value(self, value):
        """Check if value is within specification"""
        if value is None:
            return True, 'No value'
        if self.min_value is not None and value < self.min_value:
            return False, f'Below min ({self.min_value})'
        if self.max_value is not None and value > self.max_value:
            return False, f'Above max ({self.max_value})'
        return True, 'OK'

    def __repr__(self):
        return f'<ElementSpec {self.element_code}>'


class Shift(db.Model):
    """Shifts - Morning, Afternoon, Night"""
    __tablename__ = 'shifts'

    id = db.Column(db.Integer, primary_key=True)
    shift_number = db.Column(db.Integer, unique=True, nullable=False)
    shift_name = db.Column(db.String(50))
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)

    def __repr__(self):
        return f'<Shift {self.shift_number}>'


class Engineer(db.Model):
    """Engineers - Staff reference"""
    __tablename__ = 'engineers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    name_ar = db.Column(db.String(100))
    role = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<Engineer {self.name}>'


class ChemicalAnalysis(db.Model):
    """Chemical Analysis - Sheet 1 Data"""
    __tablename__ = 'chemical_analyses'

    id = db.Column(db.Integer, primary_key=True)

    # Identification
    test_date = db.Column(db.Date, nullable=False, index=True)
    furnace_id = db.Column(db.Integer, db.ForeignKey('furnaces.id'))
    ladle_no = db.Column(db.Integer, nullable=False)
    day = db.Column(db.Integer)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
    ladle_id = db.Column(db.String(20), unique=True, index=True)

    # Chemical Elements (%)
    carbon = db.Column(db.Float)
    silicon = db.Column(db.Float)
    magnesium = db.Column(db.Float)
    copper = db.Column(db.Float)
    chromium = db.Column(db.Float)
    sulfur = db.Column(db.Float)
    manganese = db.Column(db.Float)
    phosphorus = db.Column(db.Float)
    lead = db.Column(db.Float)
    aluminum = db.Column(db.Float)

    # Calculated Values
    carbon_equivalent = db.Column(db.Float)
    manganese_equivalent = db.Column(db.Float)
    magnesium_equivalent = db.Column(db.Float)

    # Quality Control
    engineer_notes = db.Column(db.Text)
    decision = db.Column(db.String(20))
    reason = db.Column(db.Text)
    has_defect = db.Column(db.Boolean, default=False)
    defect_reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Link to Production Order (امر الانتاج)
    production_order_id = db.Column(db.Integer, db.ForeignKey('production_orders.id'), index=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships
    furnace = db.relationship('Furnace', back_populates='chemical_analyses')
    pipes = db.relationship('Pipe', backref='chemical_analysis', lazy='dynamic')
    mechanical_tests = db.relationship('MechanicalTest', backref='chemical_analysis', lazy='dynamic')
    # production_order relationship defined via backref in ProductionOrder

    def calculate_equivalents(self):
        """Calculate CE, MnE, MgE values"""
        # CE = C + Si/3 + P/2
        c = self.carbon or 0
        si = self.silicon or 0
        p = self.phosphorus or 0
        if c > 0 or si > 0 or p > 0:
            self.carbon_equivalent = c + (si / 3) + (p / 2)

        # MnE = 3*Cr + Cu + Mn + P
        cr = self.chromium or 0
        cu = self.copper or 0
        mn = self.manganese or 0
        if cr > 0 or cu > 0 or mn > 0 or p > 0:
            self.manganese_equivalent = (3 * cr) + cu + mn + p

        # MgE = Mg - S
        mg = self.magnesium or 0
        s = self.sulfur or 0
        if mg > 0:
            self.magnesium_equivalent = mg - s

    def get_element_values(self):
        """Get dict of element values for validation"""
        return {
            'C': self.carbon,
            'Si': self.silicon,
            'Mg': self.magnesium,
            'Cu': self.copper,
            'Cr': self.chromium,
            'S': self.sulfur,
            'Mn': self.manganese,
            'P': self.phosphorus,
            'Pb': self.lead,
            'Al': self.aluminum,
            'CE': self.carbon_equivalent,
            'MnE': self.manganese_equivalent,
            'MgE': self.magnesium_equivalent,
        }

    def __repr__(self):
        return f'<ChemicalAnalysis {self.ladle_id}>'
