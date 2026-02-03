"""
Ladle Number and ID Utilities

Ladle numbering system:
- ladle_no: Resets to 1 every day, increments with each new ladle
- ladle_id: Composite ID = ladle_no + DDMMYYYY
  Example: ladle_no=47, date=13/01/2025 -> ladle_id="4713012025"
"""

from datetime import date
from sqlalchemy import func


def generate_ladle_id(ladle_no: int, test_date: date) -> str:
    """
    Generate ladle_id from ladle number and date.

    Format: {ladle_no}{day:02d}{month:02d}{year:04d}
    Example: ladle_no=47, date=2025-01-13 -> "4713012025"

    Args:
        ladle_no: The ladle number (resets daily, starts from 1)
        test_date: The test date

    Returns:
        ladle_id string (10+ digits depending on ladle_no)
    """
    day = test_date.day
    month = test_date.month
    year = test_date.year

    # Format: ladle_no + DD + MM + YYYY
    ladle_id = f"{ladle_no}{day:02d}{month:02d}{year:04d}"

    return ladle_id


def parse_ladle_id(ladle_id: str) -> dict:
    """
    Parse ladle_id back into components.

    Args:
        ladle_id: The composite ladle ID string

    Returns:
        dict with ladle_no, day, month, year
    """
    # ladle_id format: {ladle_no}{DD}{MM}{YYYY}
    # Example: "4713012025" -> ladle_no=47, day=13, month=01, year=2025

    # Year is last 4 characters
    year = int(ladle_id[-4:])
    # Month is 2 characters before year
    month = int(ladle_id[-6:-4])
    # Day is 2 characters before month
    day = int(ladle_id[-8:-6])
    # Ladle number is everything before that
    ladle_no = int(ladle_id[:-8])

    return {
        'ladle_no': ladle_no,
        'day': day,
        'month': month,
        'year': year
    }


def get_next_ladle_number(session, test_date: date) -> int:
    """
    Get the next ladle number for a given date.
    Ladle numbers reset to 1 each day.

    Args:
        session: SQLAlchemy session
        test_date: The test date

    Returns:
        Next available ladle number for that day
    """
    from app.models.chemical import ChemicalAnalysis

    # Find the maximum ladle_no for this date
    max_ladle = session.query(func.max(ChemicalAnalysis.ladle_no)).filter(
        ChemicalAnalysis.test_date == test_date
    ).scalar()

    if max_ladle is None:
        return 1  # First ladle of the day

    return max_ladle + 1


def validate_ladle_id_format(ladle_id: str) -> bool:
    """
    Validate that a ladle_id has the correct format.

    Args:
        ladle_id: The ladle ID to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        # Must be at least 9 characters (1 digit ladle_no + 8 date digits)
        if len(ladle_id) < 9:
            return False

        parsed = parse_ladle_id(ladle_id)

        # Validate ranges
        if not (1 <= parsed['day'] <= 31):
            return False
        if not (1 <= parsed['month'] <= 12):
            return False
        if not (2020 <= parsed['year'] <= 2100):
            return False
        if parsed['ladle_no'] < 1:
            return False

        return True
    except (ValueError, IndexError):
        return False
