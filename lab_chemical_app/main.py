"""
Lab Chemical Analysis Application
Main entry point
"""

import sys
import os

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import init_db, get_session
from database.seed_data import seed_all
from database.models import (
    Furnace, Machine, DefectType, DecisionType,
    ElementSpecification, Shift, Engineer,
    ChemicalAnalysis, Pipe, PipeStage, MechanicalTest
)


def initialize_application():
    """Initialize database and seed data"""
    print("=" * 50)
    print("Lab Chemical Analysis Application")
    print("=" * 50)

    # Create all tables
    db_path = init_db()
    print(f"\nDatabase created at: {db_path}")

    # Seed reference data
    seed_all()

    return db_path


def verify_database():
    """Verify database was created correctly"""
    session = get_session()
    try:
        print("\n=== Database Verification ===\n")

        # Count records in each table
        tables = [
            ("Furnaces", Furnace),
            ("Machines", Machine),
            ("Defect Types", DefectType),
            ("Decision Types", DecisionType),
            ("Element Specifications", ElementSpecification),
            ("Shifts", Shift),
            ("Engineers", Engineer),
            ("Chemical Analyses", ChemicalAnalysis),
            ("Pipes", Pipe),
            ("Pipe Stages", PipeStage),
            ("Mechanical Tests", MechanicalTest),
        ]

        print(f"{'Table':<25} {'Count':>10}")
        print("-" * 37)

        for name, model in tables:
            count = session.query(model).count()
            print(f"{name:<25} {count:>10}")

        print("-" * 37)

        # Show sample data
        print("\n=== Sample Data ===\n")

        print("Furnaces:")
        for f in session.query(Furnace).all():
            print(f"  - {f.furnace_code}: {f.furnace_name}")

        print("\nElement Specifications:")
        for s in session.query(ElementSpecification).all():
            range_str = f"{s.min_value or ''}-{s.max_value or ''}"
            print(f"  - {s.element_code} ({s.element_name}): {range_str} {s.unit}")

        print("\n=== Verification Complete ===")

    finally:
        session.close()


def main():
    """Main function"""
    # Initialize
    initialize_application()

    # Verify
    verify_database()

    print("\nDatabase is ready!")
    print("You can now build the UI to interact with the data.")


if __name__ == '__main__':
    main()
