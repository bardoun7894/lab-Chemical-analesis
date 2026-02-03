"""
Decision Service - Automatic decision calculation based on element_rules.json
"""
import json
import os
from functools import lru_cache


# Decision priority (higher number = worse/more severe)
DECISION_PRIORITY = {
    'فحص أخيرة فقط': 1,      # Last inspection only - Best case
    'فحص أولى وأخيرة': 2,    # First and last inspection
    'فحص الشحنة 100%': 3,    # 100% batch inspection
    'تالف': 4,               # Damaged/Rejected - Worst case
}

# Map form field names to element codes in JSON
ELEMENT_MAP = {
    'carbon': 'C',
    'silicon': 'Si',
    'manganese': 'Mn',
    'magnesium': 'Mg',
    'sulfur': 'S',
    'chromium': 'Cr',
    'copper': 'Cu',
    'aluminum': 'Al',
    'phosphorus': 'P',
    'lead': 'Pb',
    'titanium': 'Ti',
    'tin': 'Sn',
    'carbon_equivalent': 'CE',
    'manganese_equivalent': 'MnE',
    'magnesium_equivalent': 'MgE',
}

# Reverse map: element code -> form field name
CODE_TO_FIELD = {v: k for k, v in ELEMENT_MAP.items()}


@lru_cache(maxsize=1)
def load_element_rules():
    """Load and cache element rules from JSON file"""
    rules_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data',
        'element_rules.json'
    )

    with open(rules_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Convert to dict keyed by element code for faster lookup
    rules_dict = {}
    for rule in data['rules']:
        rules_dict[rule['element']] = rule['ranges']

    return rules_dict


def get_element_decision(element_code, value):
    """
    Get the decision for a single element based on its value.

    Args:
        element_code: Element code (e.g., 'C', 'Si', 'CE')
        value: The element value (float)

    Returns:
        dict with 'decision' and 'in_spec' (True if in optimal range)
    """
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    rules = load_element_rules()

    if element_code not in rules:
        return None

    ranges = rules[element_code]

    for range_def in ranges:
        min_val = range_def['min']
        max_val = range_def['max']
        decision = range_def['decision']

        if min_val <= value <= max_val:
            # Check if this is the optimal range (best decision)
            in_spec = (decision == 'فحص أخيرة فقط')
            return {
                'decision': decision,
                'priority': DECISION_PRIORITY.get(decision, 0),
                'in_spec': in_spec
            }

    # Value doesn't fall in any range - treat as damaged
    return {
        'decision': 'تالف',
        'priority': DECISION_PRIORITY['تالف'],
        'in_spec': False
    }


def calculate_auto_decision(element_values):
    """
    Calculate the automatic decision based on all element values.
    Uses worst-case (highest priority) decision.

    Args:
        element_values: dict mapping field names to values
                       e.g., {'carbon': 3.5, 'silicon': 2.1, ...}

    Returns:
        dict with:
        - recommended_decision: the worst-case decision string
        - decision_priority: numeric priority (1-4)
        - element_decisions: per-element breakdown
        - worst_elements: list of elements causing the worst decision
    """
    element_decisions = {}
    worst_priority = 0
    worst_decision = None
    worst_elements = []

    for field_name, value in element_values.items():
        # Skip empty values
        if value is None or value == '':
            continue

        # Get element code
        element_code = ELEMENT_MAP.get(field_name)
        if not element_code:
            continue

        # Get decision for this element
        result = get_element_decision(element_code, value)
        if result is None:
            continue

        element_decisions[element_code] = {
            'value': float(value),
            'decision': result['decision'],
            'priority': result['priority'],
            'in_spec': result['in_spec']
        }

        # Track worst case
        if result['priority'] > worst_priority:
            worst_priority = result['priority']
            worst_decision = result['decision']
            worst_elements = [element_code]
        elif result['priority'] == worst_priority:
            worst_elements.append(element_code)

    # If no elements were evaluated, return None
    if not element_decisions:
        return {
            'recommended_decision': None,
            'decision_priority': 0,
            'element_decisions': {},
            'worst_elements': []
        }

    return {
        'recommended_decision': worst_decision,
        'decision_priority': worst_priority,
        'element_decisions': element_decisions,
        'worst_elements': worst_elements
    }


def get_decision_color(decision):
    """Get Bootstrap color class for a decision"""
    colors = {
        'فحص أخيرة فقط': 'success',      # Green - Best
        'فحص أولى وأخيرة': 'warning',    # Yellow
        'فحص الشحنة 100%': 'orange',     # Orange (custom)
        'تالف': 'danger',                 # Red - Worst
    }
    return colors.get(decision, 'secondary')


def get_all_decisions():
    """Get list of all possible decisions in order of severity"""
    return [
        {'value': 'فحص أخيرة فقط', 'label': 'فحص أخيرة فقط (Last Only)', 'priority': 1},
        {'value': 'فحص أولى وأخيرة', 'label': 'فحص أولى وأخيرة (First & Last)', 'priority': 2},
        {'value': 'فحص الشحنة 100%', 'label': 'فحص الشحنة 100% (100% Inspection)', 'priority': 3},
        {'value': 'تالف', 'label': 'تالف (Rejected)', 'priority': 4},
    ]
