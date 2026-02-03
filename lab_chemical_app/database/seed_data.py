"""
Seed Data for Reference Tables
Run once to populate initial configuration data
"""

from .database import get_session
from .models import (
    Furnace, Machine, DefectType, DecisionType,
    ElementSpecification, Shift, Engineer
)


def seed_furnaces(session):
    """Seed 4 furnaces: A1, A2, B1, B2"""
    furnaces = [
        Furnace(furnace_code='A1', furnace_name='Furnace A1'),
        Furnace(furnace_code='A2', furnace_name='Furnace A2'),
        Furnace(furnace_code='B1', furnace_name='Furnace B1'),
        Furnace(furnace_code='B2', furnace_name='Furnace B2'),
    ]
    for f in furnaces:
        existing = session.query(Furnace).filter_by(furnace_code=f.furnace_code).first()
        if not existing:
            session.add(f)
    session.commit()
    print(f"Seeded {len(furnaces)} furnaces")


def seed_machines(session):
    """Seed machines for all stages"""
    machines = [
        # Melting
        Machine(machine_code='M10', stage='Melting'),
        Machine(machine_code='M11', stage='Melting'),
        Machine(machine_code='M12', stage='Melting'),
        Machine(machine_code='M100', stage='Melting'),
        # CCM
        Machine(machine_code='AF1', stage='CCM'),
        Machine(machine_code='ZC2', stage='CCM'),
        Machine(machine_code='ZC3', stage='CCM'),
        # Annealing
        Machine(machine_code='ZC1', stage='Annealing'),
        Machine(machine_code='CH2', stage='Annealing'),
        Machine(machine_code='CH3', stage='Annealing'),
        # Zinc
        Machine(machine_code='CH1', stage='Zinc'),
        Machine(machine_code='HT2', stage='Zinc'),
        Machine(machine_code='HT3', stage='Zinc'),
        # Cutting
        Machine(machine_code='HT1', stage='Cutting'),
        Machine(machine_code='CL2', stage='Cutting'),
        Machine(machine_code='CL3', stage='Cutting'),
        # Hydrotest
        Machine(machine_code='CL1', stage='Hydrotest'),
        Machine(machine_code='BC2', stage='Hydrotest'),
        Machine(machine_code='BC3', stage='Hydrotest'),
        # Coating
        Machine(machine_code='BC1', stage='Coating'),
    ]
    for m in machines:
        existing = session.query(Machine).filter_by(machine_code=m.machine_code).first()
        if not existing:
            session.add(m)
    session.commit()
    print(f"Seeded {len(machines)} machines")


def seed_defect_types(session):
    """Seed defect types from Sheet 2"""
    defects = [
        DefectType(defect_name_ar='Out of specification', defect_name_en='Out of specification', applies_to_stages='["all"]'),
        DefectType(defect_name_ar='رمال', defect_name_en='Sand', applies_to_stages='["Melting","CCM","Annealing"]'),
        DefectType(defect_name_ar='نقل معدن/بقايا', defect_name_en='Metal transfer/residue', applies_to_stages='["Melting","CCM"]'),
        DefectType(defect_name_ar='على معدن', defect_name_en='On metal', applies_to_stages='["Cutting"]'),
        DefectType(defect_name_ar='خروج', defect_name_en='Exit/Out', applies_to_stages='["Lab"]'),
        DefectType(defect_name_ar='حر', defect_name_en='Free/Hot', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='تريول LA', defect_name_en='LA Drip', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='سمك على', defect_name_en='Thickness over', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='سمك ضعيف', defect_name_en='Thickness low', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='خرافيت', defect_name_en='Graphite', applies_to_stages='["Lab"]'),
        DefectType(defect_name_ar='SL خط/يسيت', defect_name_en='SL Line', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='تطويز', defect_name_en='Deformation', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='كسر في الراس', defect_name_en='Head break', applies_to_stages='["Cutting"]'),
        DefectType(defect_name_ar='فوق سمك', defect_name_en='Over thickness', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='D4', defect_name_en='D4', applies_to_stages='["CCM"]'),
        DefectType(defect_name_ar='Short pipe', defect_name_en='Short pipe', applies_to_stages='["Cutting"]'),
        DefectType(defect_name_ar='تحليل قطعة', defect_name_en='Piece analysis', applies_to_stages='["Lab"]'),
        DefectType(defect_name_ar='بهيدري', defect_name_en='Byhydri', applies_to_stages='["all"]'),
        DefectType(defect_name_ar='فرن CU', defect_name_en='CU Furnace', applies_to_stages='["Melting"]'),
        DefectType(defect_name_ar='عيب منزلة', defect_name_en='Grade defect', applies_to_stages='["all"]'),
        DefectType(defect_name_ar='Other', defect_name_en='Other', applies_to_stages='["all"]'),
    ]
    for d in defects:
        existing = session.query(DefectType).filter_by(defect_name_en=d.defect_name_en).first()
        if not existing:
            session.add(d)
    session.commit()
    print(f"Seeded {len(defects)} defect types")


def seed_decision_types(session):
    """Seed decision types from Sheet 2"""
    decisions = [
        DecisionType(decision_code='ACCEPT', decision_name_ar='قبول', decision_name_en='Accept', color_code='#90EE90'),
        DecisionType(decision_code='REJECT', decision_name_ar='رفض', decision_name_en='Reject', color_code='#FF6B6B'),
        DecisionType(decision_code='HOLD', decision_name_ar='انتظار', decision_name_en='Hold', color_code='#FFD93D'),
        DecisionType(decision_code='INSPECT_FIRST_LAST', decision_name_ar='فحص أول وآخر', decision_name_en='Inspect 1st and Last', color_code='#87CEEB'),
        DecisionType(decision_code='INSPECT_100', decision_name_ar='فحص 100%', decision_name_en='Inspect 100%', color_code='#87CEEB'),
        DecisionType(decision_code='DOWNGRADE', decision_name_ar='تخفيض', decision_name_en='DownGrade', color_code='#FFA500'),
        DecisionType(decision_code='REHEAT_TREATMENT', decision_name_ar='إعادة معالجة', decision_name_en='Reheat Treatment', color_code='#DDA0DD'),
        DecisionType(decision_code='REWORK', decision_name_ar='إعادة تشغيل', decision_name_en='Rework', color_code='#DDA0DD'),
    ]
    for d in decisions:
        existing = session.query(DecisionType).filter_by(decision_code=d.decision_code).first()
        if not existing:
            session.add(d)
    session.commit()
    print(f"Seeded {len(decisions)} decision types")


def seed_element_specifications(session):
    """Seed element specifications from Sheet 1 Row 12"""
    specs = [
        # Element: (code, name, min, max)
        ElementSpecification(element_code='C', element_name='Carbon', min_value=3.0, max_value=3.9),
        ElementSpecification(element_code='Si', element_name='Silicon', min_value=1.86, max_value=2.7),
        ElementSpecification(element_code='Mg', element_name='Magnesium', min_value=0.031, max_value=0.07),
        ElementSpecification(element_code='Cu', element_name='Copper', min_value=None, max_value=0.1),
        ElementSpecification(element_code='Cr', element_name='Chromium', min_value=None, max_value=0.1),
        ElementSpecification(element_code='S', element_name='Sulfur', min_value=None, max_value=0.02),
        ElementSpecification(element_code='Mn', element_name='Manganese', min_value=None, max_value=0.4),
        ElementSpecification(element_code='P', element_name='Phosphorus', min_value=None, max_value=0.059),
        ElementSpecification(element_code='Pb', element_name='Lead', min_value=None, max_value=0.003),
        ElementSpecification(element_code='Al', element_name='Aluminum', min_value=None, max_value=0.049),
        ElementSpecification(element_code='CE', element_name='Carbon Equivalent', min_value=3.62, max_value=4.83),
        ElementSpecification(element_code='MnE', element_name='Manganese Equivalent', min_value=0.1, max_value=0.85),
        ElementSpecification(element_code='MgE', element_name='Magnesium Equivalent', min_value=0.023, max_value=None),
    ]
    for s in specs:
        existing = session.query(ElementSpecification).filter_by(element_code=s.element_code).first()
        if not existing:
            session.add(s)
    session.commit()
    print(f"Seeded {len(specs)} element specifications")


def seed_shifts(session):
    """Seed 3 shifts"""
    shifts = [
        Shift(shift_number=1, shift_name='Morning'),
        Shift(shift_number=2, shift_name='Afternoon'),
        Shift(shift_number=3, shift_name='Night'),
    ]
    for s in shifts:
        existing = session.query(Shift).filter_by(shift_number=s.shift_number).first()
        if not existing:
            session.add(s)
    session.commit()
    print(f"Seeded {len(shifts)} shifts")


def seed_sample_engineers(session):
    """Seed sample engineers from the Excel data"""
    engineers = [
        Engineer(name='Hamada Fawzy', name_ar='حمادة فوزي', role='Shift Engineer'),
        Engineer(name='Mahmoud Hamdy', name_ar='محمود حمدي', role='Lab Technician'),
    ]
    for e in engineers:
        existing = session.query(Engineer).filter_by(name=e.name).first()
        if not existing:
            session.add(e)
    session.commit()
    print(f"Seeded {len(engineers)} engineers")


def seed_all():
    """Run all seed functions"""
    session = get_session()
    try:
        print("\n=== Seeding Database ===\n")
        seed_furnaces(session)
        seed_machines(session)
        seed_defect_types(session)
        seed_decision_types(session)
        seed_element_specifications(session)
        seed_shifts(session)
        seed_sample_engineers(session)
        print("\n=== Seeding Complete ===\n")
    except Exception as e:
        session.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        session.close()


if __name__ == '__main__':
    seed_all()
