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


# Example usage
if __name__ == '__main__':
    # Sample specifications
    specs = [
        {'element_code': 'C', 'min_value': 3.0, 'max_value': 3.9},
        {'element_code': 'Si', 'min_value': 1.86, 'max_value': 2.7},
        {'element_code': 'Mg', 'min_value': 0.031, 'max_value': 0.07},
        {'element_code': 'Cu', 'min_value': None, 'max_value': 0.1},
        {'element_code': 'S', 'min_value': None, 'max_value': 0.02},
    ]

    # Sample analysis - should pass
    good_analysis = {
        'C': 3.5,
        'Si': 2.1,
        'Mg': 0.05,
        'Cu': 0.08,
        'S': 0.015,
    }

    print("Testing GOOD analysis:")
    is_valid, failures, warnings = validate_chemical_analysis(good_analysis, specs)
    print(f"  Pass: {is_valid}")
    print(f"  Failures: {failures}")
    print(f"  Warnings: {warnings}")

    # Sample analysis - should fail
    bad_analysis = {
        'C': 4.5,  # Too high
        'Si': 1.5,  # Too low
        'Mg': 0.05,
        'Cu': 0.15,  # Too high
        'S': 0.015,
    }

    print("\nTesting BAD analysis:")
    is_valid, failures, warnings = validate_chemical_analysis(bad_analysis, specs)
    print(f"  Pass: {is_valid}")
    print(f"  Failures: {failures}")
    print(f"  Warnings: {warnings}")

    decision, reason = get_decision_for_analysis(is_valid, failures)
    print(f"  Decision: {decision}")
    print(f"  Reason: {reason}")
