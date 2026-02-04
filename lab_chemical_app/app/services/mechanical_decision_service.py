"""
Mechanical Decision Service - Automatic decision calculation based on mechanical_rules.json
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

# Map form field names to property codes in JSON
PROPERTY_MAP = {
    'tensile_strength': 'tensile_strength',
    'elongation': 'elongation',
    'nodularity_percent': 'nodularity_percent',
    'ferrite': 'ferrite',
    'nodule_count': 'nodule_count',
    'carbides': 'carbides',
    'hardness': 'hardness',
}

# Reverse map: property code -> form field name
CODE_TO_FIELD = {v: k for k, v in PROPERTY_MAP.items()}


def clear_rules_cache():
    """Clear the cached rules to reload from file"""
    load_mechanical_rules.cache_clear()


@lru_cache(maxsize=1)
def load_mechanical_rules():
    """Load and cache mechanical rules from JSON file"""
    rules_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data',
        'mechanical_rules.json'
    )

    with open(rules_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Convert to dict keyed by property code for faster lookup
    rules_dict = {}
    for rule in data.get('rules', []):
        rules_dict[rule['property']] = {
            'name': rule.get('name', rule['property']),
            'name_ar': rule.get('name_ar', rule['property']),
            'unit': rule.get('unit', ''),
            'ranges': rule['ranges']
        }

    return rules_dict


def load_mechanical_rules_raw():
    """Load mechanical rules from JSON file without caching (for admin)"""
    rules_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data',
        'mechanical_rules.json'
    )

    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {'rules': [], 'error': str(e)}


def save_mechanical_rules(rules_data):
    """Save mechanical rules to JSON file"""
    rules_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data',
        'mechanical_rules.json'
    )

    with open(rules_path, 'w', encoding='utf-8') as f:
        json.dump(rules_data, f, ensure_ascii=False, indent=2)

    # Clear cache after saving
    clear_rules_cache()


def get_property_decision(property_code, value):
    """
    Get the decision for a single mechanical property based on its value.

    Args:
        property_code: Property code (e.g., 'tensile_strength', 'elongation')
        value: The property value (float)

    Returns:
        dict with 'decision', 'priority', 'in_spec' (True if in optimal range)
    """
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    rules = load_mechanical_rules()

    if property_code not in rules:
        return None

    ranges = rules[property_code]['ranges']

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


def calculate_auto_decision(property_values):
    """
    Calculate the automatic decision based on all property values.
    Uses worst-case (highest priority) decision.

    Args:
        property_values: dict mapping field names to values
                        e.g., {'tensile_strength': 45.5, 'elongation': 12, ...}

    Returns:
        dict with:
        - recommended_decision: the worst-case decision string
        - decision_priority: numeric priority (1-4)
        - property_decisions: per-property breakdown
        - worst_properties: list of properties causing the worst decision
    """
    property_decisions = {}
    worst_priority = 0
    worst_decision = None
    worst_properties = []

    rules = load_mechanical_rules()

    for field_name, value in property_values.items():
        # Skip empty values
        if value is None or value == '':
            continue

        # Get property code
        property_code = PROPERTY_MAP.get(field_name)
        if not property_code:
            continue

        # Get decision for this property
        result = get_property_decision(property_code, value)
        if result is None:
            continue

        # Get property info for display
        property_info = rules.get(property_code, {})

        property_decisions[property_code] = {
            'value': float(value),
            'decision': result['decision'],
            'priority': result['priority'],
            'in_spec': result['in_spec'],
            'name': property_info.get('name', property_code),
            'name_ar': property_info.get('name_ar', property_code),
            'unit': property_info.get('unit', '')
        }

        # Track worst case
        if result['priority'] > worst_priority:
            worst_priority = result['priority']
            worst_decision = result['decision']
            worst_properties = [property_code]
        elif result['priority'] == worst_priority:
            worst_properties.append(property_code)

    # If no properties were evaluated, return None
    if not property_decisions:
        return {
            'recommended_decision': None,
            'decision_priority': 0,
            'property_decisions': {},
            'worst_properties': []
        }

    return {
        'recommended_decision': worst_decision,
        'decision_priority': worst_priority,
        'property_decisions': property_decisions,
        'worst_properties': worst_properties
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


def get_acceptance_criteria():
    """Get acceptance criteria from the rules file"""
    rules_data = load_mechanical_rules_raw()
    return rules_data.get('acceptance_criteria', {})


def validate_property(property_code, value):
    """
    Validate a single property value against acceptance criteria.

    Returns:
        dict with 'valid', 'message', 'min', 'max' for the acceptance range
    """
    if value is None or value == '':
        return {'valid': True, 'message': 'No value provided'}

    try:
        value = float(value)
    except (TypeError, ValueError):
        return {'valid': False, 'message': 'Invalid number'}

    rules_data = load_mechanical_rules_raw()
    acceptance = rules_data.get('acceptance_criteria', {})

    # Map property codes to acceptance criteria keys
    criteria_map = {
        'tensile_strength': 'tensile_strength',
        'elongation': 'elongation',
        'nodularity_percent': 'nd',
        'ferrite': 'ferrite',
        'nodule_count': 'nc',
        'carbides': 'carbides',
        'hardness': 'hardness',
    }

    criteria_key = criteria_map.get(property_code)
    if not criteria_key or criteria_key not in acceptance:
        return {'valid': True, 'message': 'No criteria defined'}

    criteria = acceptance[criteria_key]
    condition = criteria.get('condition', '')

    # Parse condition
    if condition.startswith('>='):
        min_val = float(condition.replace('>=', '').strip())
        valid = value >= min_val
        return {
            'valid': valid,
            'message': f'Must be >= {min_val}' if not valid else 'OK',
            'min': min_val,
            'max': None
        }
    elif condition.startswith('>'):
        min_val = float(condition.replace('>', '').strip())
        valid = value > min_val
        return {
            'valid': valid,
            'message': f'Must be > {min_val}' if not valid else 'OK',
            'min': min_val,
            'max': None
        }
    elif condition.startswith('<='):
        max_val = float(condition.replace('<=', '').strip())
        valid = value <= max_val
        return {
            'valid': valid,
            'message': f'Must be <= {max_val}' if not valid else 'OK',
            'min': None,
            'max': max_val
        }
    elif condition.startswith('<'):
        max_val = float(condition.replace('<', '').strip())
        valid = value < max_val
        return {
            'valid': valid,
            'message': f'Must be < {max_val}' if not valid else 'OK',
            'min': None,
            'max': max_val
        }

    return {'valid': True, 'message': 'Unknown condition'}
