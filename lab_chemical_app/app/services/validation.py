"""
Validation Services for Chemical Analysis

Validates chemical element values against specifications
and determines if analysis passes or fails.
"""

from typing import Dict, List, Tuple, Optional


def check_element_in_spec(
    element_code: str,
    value: float,
    min_value: Optional[float],
    max_value: Optional[float]
) -> Tuple[bool, str]:
    """
    Check if a single element value is within specification.

    Args:
        element_code: Element code (C, Si, Mg, etc.)
        value: The measured value
        min_value: Minimum allowed (None if no minimum)
        max_value: Maximum allowed (None if no maximum)

    Returns:
        Tuple of (is_valid, message)
    """
    if value is None:
        return True, f"{element_code}: No value"

    # Check minimum
    if min_value is not None and value < min_value:
        return False, f"{element_code}: {value} < {min_value} (below minimum)"

    # Check maximum
    if max_value is not None and value > max_value:
        return False, f"{element_code}: {value} > {max_value} (above maximum)"

    return True, f"{element_code}: {value} OK"


def validate_chemical_analysis(
    analysis_data: Dict[str, float],
    specifications: List[Dict]
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate a complete chemical analysis against specifications.

    Args:
        analysis_data: Dict mapping element codes to values
            Example: {'C': 3.5, 'Si': 2.1, 'Mg': 0.05, ...}
        specifications: List of specification dicts from database
            Each dict has: element_code, min_value, max_value

    Returns:
        Tuple of (overall_pass, failures, warnings)
        - overall_pass: True if all elements pass
        - failures: List of failure messages
        - warnings: List of warning messages
    """
    failures = []
    warnings = []

    # Map element code to spec
    spec_map = {s['element_code']: s for s in specifications}

    for element_code, value in analysis_data.items():
        if element_code not in spec_map:
            warnings.append(f"{element_code}: No specification defined")
            continue

        spec = spec_map[element_code]
        is_valid, message = check_element_in_spec(
            element_code,
            value,
            spec.get('min_value'),
            spec.get('max_value')
        )

        if not is_valid:
            failures.append(message)

    overall_pass = len(failures) == 0

    return overall_pass, failures, warnings


def get_decision_for_analysis(
    is_valid: bool,
    failures: List[str]
) -> Tuple[str, str]:
    """
    Determine decision based on validation results.

    Args:
        is_valid: Whether analysis passed all specs
        failures: List of failure messages

    Returns:
        Tuple of (decision_code, reason)
    """
    if is_valid:
        return 'ACCEPT', 'All elements within specification'

    # Determine severity of failures
    reason = '; '.join(failures)

    return 'REJECT', reason


def calculate_carbon_equivalent(carbon: float, silicon: float) -> float:
    """
    Calculate Carbon Equivalent (CE).
    Formula: CE = C + Si/3 (simplified)

    Args:
        carbon: Carbon content (%)
        silicon: Silicon content (%)

    Returns:
        Carbon Equivalent value
    """
    if carbon is None or silicon is None:
        return None
    return carbon + (silicon / 3)


def calculate_manganese_equivalent(manganese: float, sulfur: float) -> float:
    """
    Calculate Manganese Equivalent (MnE).
    Formula: MnE = Mn - 1.7*S (simplified)

    Args:
        manganese: Manganese content (%)
        sulfur: Sulfur content (%)

    Returns:
        Manganese Equivalent value
    """
    if manganese is None or sulfur is None:
        return None
    return manganese - (1.7 * sulfur)


def calculate_magnesium_equivalent(magnesium: float, sulfur: float) -> float:
    """
    Calculate Magnesium Equivalent (MgE).
    Formula: MgE = Mg - 0.76*S (residual Mg after S reaction)

    Args:
        magnesium: Magnesium content (%)
        sulfur: Sulfur content (%)

    Returns:
        Magnesium Equivalent value
    """
    if magnesium is None or sulfur is None:
        return None
    return magnesium - (0.76 * sulfur)
