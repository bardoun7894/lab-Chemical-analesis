"""
Lab Chemical Analysis Application
Run this file to start the Flask development server
"""

import os
import sys

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("Environment variables loaded from .env")
except ImportError:
    # If python-dotenv is not installed, load .env manually
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
        print("Environment variables loaded from .env (manual)")

from app import create_app, db
from app.models.user import User
from app.models.chemical import Furnace, Machine, DefectType, DecisionType, ElementSpecification, Shift, Engineer
from app.models.production_order import ProductionOrder


def create_default_admin():
    """Create default admin user if not exists"""
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            full_name='Administrator',
            full_name_ar='المسؤول',
            role=User.ROLE_ADMIN,
            department='IT'
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Created default admin user: admin / admin123")
    return admin


def seed_reference_data():
    """Seed reference tables if empty"""
    # Furnaces
    if Furnace.query.count() == 0:
        furnaces = [
            Furnace(furnace_code='A1', furnace_name='Furnace A1'),
            Furnace(furnace_code='A2', furnace_name='Furnace A2'),
            Furnace(furnace_code='B1', furnace_name='Furnace B1'),
            Furnace(furnace_code='B2', furnace_name='Furnace B2'),
        ]
        db.session.add_all(furnaces)
        print("Seeded furnaces")

    # Machines
    if Machine.query.count() == 0:
        machines = [
            Machine(machine_code='M10', stage='Melting'),
            Machine(machine_code='M11', stage='Melting'),
            Machine(machine_code='M12', stage='Melting'),
            Machine(machine_code='AF1', stage='CCM'),
            Machine(machine_code='ZC2', stage='CCM'),
            Machine(machine_code='ZC3', stage='CCM'),
            Machine(machine_code='CH1', stage='Annealing'),
            Machine(machine_code='CH2', stage='Annealing'),
            Machine(machine_code='HT1', stage='Zinc'),
            Machine(machine_code='HT2', stage='Zinc'),
            Machine(machine_code='CL1', stage='Cutting'),
            Machine(machine_code='BC1', stage='Hydrotest'),
            Machine(machine_code='BC2', stage='Hydrotest'),
        ]
        db.session.add_all(machines)
        print("Seeded machines")

    # Decision Types
    if DecisionType.query.count() == 0:
        decisions = [
            DecisionType(decision_code='ACCEPT', decision_name_en='Accept', decision_name_ar='قبول'),
            DecisionType(decision_code='REJECT', decision_name_en='Reject', decision_name_ar='رفض'),
            DecisionType(decision_code='HOLD', decision_name_en='Hold', decision_name_ar='انتظار'),
            DecisionType(decision_code='INSPECT_100', decision_name_en='Inspect 100%', decision_name_ar='فحص 100%'),
            DecisionType(decision_code='REWORK', decision_name_en='Rework', decision_name_ar='إعادة تشغيل'),
        ]
        db.session.add_all(decisions)
        print("Seeded decision types")

    # Element Specifications
    if ElementSpecification.query.count() == 0:
        specs = [
            ElementSpecification(element_code='C', element_name='Carbon', min_value=3.0, max_value=3.9),
            ElementSpecification(element_code='Si', element_name='Silicon', min_value=1.86, max_value=2.7),
            ElementSpecification(element_code='Mg', element_name='Magnesium', min_value=0.031, max_value=0.07),
            ElementSpecification(element_code='Cu', element_name='Copper', max_value=0.1),
            ElementSpecification(element_code='Cr', element_name='Chromium', max_value=0.1),
            ElementSpecification(element_code='S', element_name='Sulfur', max_value=0.02),
            ElementSpecification(element_code='Mn', element_name='Manganese', max_value=0.4),
            ElementSpecification(element_code='P', element_name='Phosphorus', max_value=0.059),
            ElementSpecification(element_code='Pb', element_name='Lead', max_value=0.003),
            ElementSpecification(element_code='Al', element_name='Aluminum', max_value=0.049),
            ElementSpecification(element_code='CE', element_name='Carbon Equivalent', min_value=3.62, max_value=4.83),
            ElementSpecification(element_code='MnE', element_name='Manganese Equivalent', min_value=0.1, max_value=0.85),
            ElementSpecification(element_code='MgE', element_name='Magnesium Equivalent', min_value=0.023),
        ]
        db.session.add_all(specs)
        print("Seeded element specifications")

    # Defect Types
    if DefectType.query.count() == 0:
        defects = [
            DefectType(defect_name_en='Out of specification', defect_name_ar='خارج المواصفات'),
            DefectType(defect_name_en='Sand', defect_name_ar='رمال'),
            DefectType(defect_name_en='Metal transfer', defect_name_ar='نقل معدن'),
            DefectType(defect_name_en='Thickness over', defect_name_ar='سمك عالي'),
            DefectType(defect_name_en='Thickness low', defect_name_ar='سمك ضعيف'),
            DefectType(defect_name_en='Graphite', defect_name_ar='خرافيت'),
            DefectType(defect_name_en='Head break', defect_name_ar='كسر في الراس'),
            DefectType(defect_name_en='Short pipe', defect_name_ar='أنبوب قصير'),
            DefectType(defect_name_en='Other', defect_name_ar='أخرى'),
        ]
        db.session.add_all(defects)
        print("Seeded defect types")

    # Shifts
    if Shift.query.count() == 0:
        shifts = [
            Shift(shift_number=1, shift_name='Morning'),
            Shift(shift_number=2, shift_name='Afternoon'),
            Shift(shift_number=3, shift_name='Night'),
        ]
        db.session.add_all(shifts)
        print("Seeded shifts")

    # Sample Production Orders
    if ProductionOrder.query.count() == 0:
        from datetime import date, timedelta
        today = date.today()
        orders = [
            ProductionOrder(
                order_number='PO-20260201-001',
                customer_name='Egyptian Steel Co.',
                customer_code='ESC001',
                target_quantity=100,
                diameter=500,
                pipe_class='K9',
                order_date=today - timedelta(days=5),
                start_date=today - timedelta(days=3),
                status='in_progress',
                priority='high'
            ),
            ProductionOrder(
                order_number='PO-20260201-002',
                customer_name='Cairo Water Authority',
                customer_code='CWA002',
                target_quantity=50,
                diameter=300,
                pipe_class='C25',
                order_date=today - timedelta(days=2),
                status='pending',
                priority='normal'
            ),
        ]
        db.session.add_all(orders)
        print("Seeded sample production orders")

    db.session.commit()


def init_database(app):
    """Initialize database and seed data"""
    with app.app_context():
        # Create all tables
        db.create_all()
        print("Database tables created")

        # Seed reference data
        seed_reference_data()

        # Create default admin
        create_default_admin()


if __name__ == '__main__':
    # Create the Flask app
    app = create_app()

    # Initialize database
    init_database(app)

    print("\n" + "=" * 50)
    print("Lab Chemical Analysis Application")
    print("=" * 50)
    print(f"\nStarting server at: http://127.0.0.1:9999")
    print("Default login: admin / admin123")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 50 + "\n")

    # Run the development server
    app.run(
        host='0.0.0.0',
        port=9999,
        debug=True
    )
