"""
SQLAlchemy Models for Lab Chemical Analysis Application
11 Tables covering all 4 Excel sheets
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, Date, Time,
    ForeignKey, TIMESTAMP, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# ============================================
# Reference Tables (Lookup/Configuration)
# ============================================

class Furnace(Base):
    """Table 1: Furnaces - A1, A2, B1, B2"""
    __tablename__ = 'furnaces'

    id = Column(Integer, primary_key=True, autoincrement=True)
    furnace_code = Column(String(10), unique=True, nullable=False)  # A1, A2, B1, B2
    furnace_name = Column(String(100))
    is_active = Column(Boolean, default=True)

    # Relationships
    chemical_analyses = relationship("ChemicalAnalysis", back_populates="furnace")

    def __repr__(self):
        return f"<Furnace(code='{self.furnace_code}')>"


class Machine(Base):
    """Table 2: Machines - M10, M11, AF1, ZC1, etc."""
    __tablename__ = 'machines'

    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_code = Column(String(20), unique=True, nullable=False)
    machine_name = Column(String(100))
    stage = Column(String(50))  # CCM, Annealing, Zinc, etc.
    is_active = Column(Boolean, default=True)

    # Relationships
    pipes = relationship("Pipe", back_populates="machine")

    def __repr__(self):
        return f"<Machine(code='{self.machine_code}', stage='{self.stage}')>"


class DefectType(Base):
    """Table 3: Defect Types - from Sheet 2"""
    __tablename__ = 'defect_types'

    id = Column(Integer, primary_key=True, autoincrement=True)
    defect_code = Column(String(50))
    defect_name_ar = Column(String(100), nullable=False)  # Arabic name
    defect_name_en = Column(String(100))  # English name
    applies_to_stages = Column(Text)  # JSON: ["CCM", "Annealing"]
    is_active = Column(Boolean, default=True)

    # Relationships
    pipe_stages = relationship("PipeStage", back_populates="defect_type")

    def __repr__(self):
        return f"<DefectType(name_en='{self.defect_name_en}')>"


class DecisionType(Base):
    """Table 4: Decision Types - Accept, Reject, Hold, etc."""
    __tablename__ = 'decision_types'

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_code = Column(String(50), unique=True, nullable=False)
    decision_name_ar = Column(String(100))
    decision_name_en = Column(String(100))
    color_code = Column(String(20))  # For UI display

    def __repr__(self):
        return f"<DecisionType(code='{self.decision_code}')>"


class ElementSpecification(Base):
    """Table 5: Element Specifications - Chemical limits from Sheet 1 Row 12"""
    __tablename__ = 'element_specifications'

    id = Column(Integer, primary_key=True, autoincrement=True)
    element_code = Column(String(10), nullable=False)  # C, Si, Mg, etc.
    element_name = Column(String(50))
    min_value = Column(Float)
    max_value = Column(Float)
    unit = Column(String(10), default='%')

    def __repr__(self):
        return f"<ElementSpec(code='{self.element_code}', range={self.min_value}-{self.max_value})>"


class Shift(Base):
    """Table 6: Shifts - Morning, Afternoon, Night"""
    __tablename__ = 'shifts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shift_number = Column(Integer, unique=True, nullable=False)
    shift_name = Column(String(50))
    start_time = Column(Time)
    end_time = Column(Time)

    def __repr__(self):
        return f"<Shift(number={self.shift_number}, name='{self.shift_name}')>"


class Engineer(Base):
    """Table 7: Engineers - Staff reference"""
    __tablename__ = 'engineers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    name_ar = Column(String(100))  # Arabic name
    role = Column(String(50))  # Shift Engineer, Lab Technician, etc.
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<Engineer(name='{self.name}')>"


# ============================================
# Data Tables (Main Business Data)
# ============================================

class ChemicalAnalysis(Base):
    """Table 8: Chemical Analyses - Sheet 1 Data"""
    __tablename__ = 'chemical_analyses'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Identification
    test_date = Column(Date, nullable=False)
    furnace_id = Column(Integer, ForeignKey('furnaces.id'))
    ladle_no = Column(Integer, nullable=False)
    day = Column(Integer)
    month = Column(Integer)
    year = Column(Integer)
    ladle_id = Column(String(20), unique=True)  # Composite ID: 471212025

    # Chemical Elements (%)
    carbon = Column(Float)              # C
    silicon = Column(Float)             # Si
    magnesium = Column(Float)           # Mg
    copper = Column(Float)              # Cu
    chromium = Column(Float)            # Cr
    sulfur = Column(Float)              # S
    manganese = Column(Float)           # Mn
    phosphorus = Column(Float)          # P
    lead = Column(Float)                # Pb
    aluminum = Column(Float)            # Al

    # Calculated Values
    carbon_equivalent = Column(Float)    # CE
    manganese_equivalent = Column(Float) # MnE
    magnesium_equivalent = Column(Float) # MgE

    # Quality Control
    engineer_notes = Column(Text)
    decision = Column(String(20))
    reason = Column(Text)
    has_defect = Column(Boolean, default=False)
    defect_reason = Column(Text)
    notes = Column(Text)

    # Metadata
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Relationships
    furnace = relationship("Furnace", back_populates="chemical_analyses")
    pipes = relationship("Pipe", back_populates="chemical_analysis")
    mechanical_tests = relationship("MechanicalTest", back_populates="chemical_analysis")

    # Indexes
    __table_args__ = (
        Index('idx_chem_ladle_id', 'ladle_id'),
        Index('idx_chem_test_date', 'test_date'),
    )

    def __repr__(self):
        return f"<ChemicalAnalysis(ladle_id='{self.ladle_id}', date={self.test_date})>"


class Pipe(Base):
    """Table 9: Pipes - Sheet 3 Main Production Data"""
    __tablename__ = 'pipes'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Production Info
    production_date = Column(Date, nullable=False)
    shift = Column(Integer)
    shift_engineer = Column(String(100))
    manufacturing_order = Column(String(50))  # MO

    # Pipe Identification
    pipe_code = Column(String(50))
    diameter = Column(Integer)          # DN: 300, 500, 600
    pipe_type = Column(String(20))      # K9, C25, Fittings
    machine_id = Column(Integer, ForeignKey('machines.id'))
    mold_number = Column(String(20))
    iso_weight = Column(Float)
    no_code = Column(String(50), unique=True)  # N8739, N8740...
    arrange_pipe = Column(Integer)      # Sequence 1-6

    # Link to Chemical Analysis
    ladle_id = Column(String(20), ForeignKey('chemical_analyses.ladle_id'))

    # Measurements
    thickness = Column(Float)
    actual_weight = Column(Float)

    # Metadata
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Relationships
    machine = relationship("Machine", back_populates="pipes")
    chemical_analysis = relationship("ChemicalAnalysis", back_populates="pipes")
    stages = relationship("PipeStage", back_populates="pipe", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_pipes_ladle', 'ladle_id'),
        Index('idx_pipes_date', 'production_date'),
        Index('idx_pipes_no_code', 'no_code'),
    )

    def __repr__(self):
        return f"<Pipe(no_code='{self.no_code}', diameter={self.diameter})>"


class PipeStage(Base):
    """Table 10: Pipe Stages - 8 stages per pipe from Sheet 3"""
    __tablename__ = 'pipe_stages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipe_id = Column(Integer, ForeignKey('pipes.id'), nullable=False)
    stage_name = Column(String(50), nullable=False)  # CCM, Annealing, Zinc, Cutting, Hydrotest, Cement, Coating, Finish

    # Stage timestamps
    stage_date = Column(Date)
    stage_time = Column(Time)

    # Stage measurements
    measurement_value = Column(Float)   # Zinc Slide, Cement thick, Coating thick, Length
    measurement_type = Column(String(50))

    # Quality Control
    decision = Column(String(20))
    reason = Column(Text)
    has_defect = Column(Boolean, default=False)
    defect_type_id = Column(Integer, ForeignKey('defect_types.id'))
    defect_reason = Column(Text)
    notes = Column(Text)

    # Metadata
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Relationships
    pipe = relationship("Pipe", back_populates="stages")
    defect_type = relationship("DefectType", back_populates="pipe_stages")

    # Constraints and Indexes
    __table_args__ = (
        UniqueConstraint('pipe_id', 'stage_name', name='uix_pipe_stage'),
        Index('idx_stages_pipe', 'pipe_id'),
        Index('idx_stages_decision', 'decision'),
    )

    def __repr__(self):
        return f"<PipeStage(pipe_id={self.pipe_id}, stage='{self.stage_name}')>"


class MechanicalTest(Base):
    """Table 11: Mechanical Tests - Sheet 4 Data"""
    __tablename__ = 'mechanical_tests'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Identification
    test_date = Column(Date, nullable=False)
    test_number = Column(Integer)
    diameter = Column(Integer)          # 300, 500, etc.
    code = Column(String(50))           # A2989, V4783
    pipe_no = Column(Integer)
    ladle_id = Column(String(20), ForeignKey('chemical_analyses.ladle_id'))
    day = Column(Integer)
    month = Column(Integer)
    year = Column(Integer)

    # Sample Measurements
    sample_thickness = Column(Float)
    d1 = Column(Float)
    d2 = Column(Float)
    d3 = Column(Float)
    avg_dimension = Column(Float)       # AVD
    original_length = Column(Float)     # Lo
    final_length = Column(Float)        # Lf
    area_d_squared = Column(Float)      # (A) D²

    # Test Results
    force_kgf = Column(Float)           # F =Kgf
    tensile_strength = Column(Float)    # σ = F/A
    elongation = Column(Float)          # E = (Lf-Lo)/Lo x100

    # Microstructure Analysis
    microstructure = Column(Text)
    percent_85 = Column(Float)          # >85%
    percent_70 = Column(Float)          # >70%
    percent_40 = Column(Float)          # >40%
    percent_1 = Column(Float)           # <1%
    nodularity_percent = Column(Float)  # %Nd
    nodule_count = Column(Integer)      # NC
    hardness = Column(Float)
    carbides = Column(Float)

    # Quality Control
    shift = Column(Integer)
    tester_name = Column(String(100))
    decision = Column(String(20))
    reason = Column(Text)
    has_defect = Column(Boolean, default=False)
    defect_reason = Column(Text)
    comments = Column(Text)

    # Metadata
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Relationships
    chemical_analysis = relationship("ChemicalAnalysis", back_populates="mechanical_tests")

    # Indexes
    __table_args__ = (
        Index('idx_mech_ladle', 'ladle_id'),
        Index('idx_mech_date', 'test_date'),
    )

    def __repr__(self):
        return f"<MechanicalTest(code='{self.code}', date={self.test_date})>"
