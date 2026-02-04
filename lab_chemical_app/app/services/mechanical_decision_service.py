"""
Mechanical Decision Service - Simple ACCEPT/REJECT based on acceptance criteria
Logic: ALL criteria must pass → ACCEPT, ANY criterion fails → REJECT
"""
import json
import os
from functools import lru_cache


def clear_rules_cache():
    """Clear the cached rules to reload from file"""
    load_mechanical_config.cache_clear()


@lru_cache(maxsize=1)
def load_mechanical_config():
    """Load and cache mechanical config from JSON file"""
    rules_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'data',
        'mechanical_rules.json'
    )

    with open(rules_path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
        return {'acceptance_criteria': {}, 'error': str(e)}


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


def parse_condition(condition_str, value):
    """Parse and evaluate a condition string against a value"""
    condition_str = condition_str.strip()

    if condition_str.startswith('>='):
        threshold = float(condition_str.replace('>=', '').strip())
        return value >= threshold
    elif condition_str.startswith('>'):
        threshold = float(condition_str.replace('>', '').strip())
        return value > threshold
    elif condition_str.startswith('<='):
        threshold = float(condition_str.replace('<=', '').strip())
        return value <= threshold
    elif condition_str.startswith('<'):
        threshold = float(condition_str.replace('<', '').strip())
        return value < threshold

    return False


def validate_property(property_code, value):
    """
    Validate a single property against acceptance criteria.

    Returns:
        dict with 'valid', 'message', 'condition'
    """
    if value is None or value == '':
        return {'valid': True, 'message': 'No value', 'condition': None}

    try:
        value = float(value)
    except (TypeError, ValueError):
        return {'valid': False, 'message': 'Invalid number', 'condition': None}

    config = load_mechanical_config()
    acceptance = config.get('acceptance_criteria', {})

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

    criteria_key = criteria_map.get(property_code.lower())
    if not criteria_key or criteria_key not in acceptance:
        return {'valid': True, 'message': 'No criteria', 'condition': None}

    criteria = acceptance[criteria_key]
    condition = criteria.get('condition', '')
    property_name = criteria.get('property', property_code)

    valid = parse_condition(condition, value)

    if valid:
        message = f'✓ {property_name}: {value} meets {condition}'
    else:
        message = f'✗ {property_name}: {value} must be {condition}'

    return {
        'valid': valid,
        'message': message,
        'condition': condition,
        'unit': criteria.get('unit', ''),
        'property_name': property_name
    }


def calculate_auto_decision(property_values):
    """
    Calculate ACCEPT/REJECT decision based on acceptance criteria ONLY.
    Simple logic: ALL criteria must pass → ACCEPT, ANY fails → REJECT

    Returns:
        dict with:
        - recommended_decision: 'ACCEPT' or 'REJECT'
        - all_pass: boolean
        - property_results: validation results for each property
        - failed_properties: list of properties that failed
        - passed_properties: list of properties that passed
    """
    config = load_mechanical_config()
    acceptance = config.get('acceptance_criteria', {})

    # Map form field names to criteria keys
    field_to_criteria = {
        'tensile_strength': 'tensile_strength',
        'elongation': 'elongation',
        'nodularity_percent': 'nd',
        'ferrite': 'ferrite',
        'nodule_count': 'nc',
        'carbides': 'carbides',
        'hardness': 'hardness',
    }

    property_results = {}
    failed_properties = []
    passed_properties = []
    evaluated_count = 0

    for field_name, value in property_values.items():
        if value is None or value == '':
            continue

        criteria_key = field_to_criteria.get(field_name)
        if not criteria_key or criteria_key not in acceptance:
            continue

        try:
            value_float = float(value)
        except (TypeError, ValueError):
            continue

        # Validate the property
        validation = validate_property(field_name, value_float)
        criteria = acceptance[criteria_key]

        property_results[field_name] = {
            'value': value_float,
            'valid': validation['valid'],
            'condition': criteria.get('condition', ''),
            'unit': criteria.get('unit', ''),
            'property_name': criteria.get('property', field_name),
            'message': validation['message']
        }

        evaluated_count += 1

        if validation['valid']:
            passed_properties.append(field_name)
        else:
            failed_properties.append(field_name)

    # If no properties were evaluated, return None
    if evaluated_count == 0:
        return {
            'recommended_decision': None,
            'all_pass': None,
            'property_results': {},
            'failed_properties': [],
            'passed_properties': [],
            'summary': 'No properties to evaluate'
        }

    # Simple decision: ALL must pass → ACCEPT, ANY fails → REJECT
    all_pass = (len(failed_properties) == 0)
    recommended_decision = 'ACCEPT' if all_pass else 'REJECT'

    # Build summary message
    if all_pass:
        summary = f'✓ ACCEPT - All {evaluated_count} criteria passed'
    else:
        summary = f'✗ REJECT - {len(failed_properties)} of {evaluated_count} criteria failed'

    return {
        'recommended_decision': recommended_decision,
        'all_pass': all_pass,
        'property_results': property_results,
        'failed_properties': failed_properties,
        'passed_properties': passed_properties,
        'summary': summary,
        'evaluated_count': evaluated_count
    }


def get_acceptance_criteria():
    """Get acceptance criteria from config"""
    config = load_mechanical_config()
    return config.get('acceptance_criteria', {})


def get_all_criteria_info():
    """Get formatted information about all acceptance criteria"""
    criteria = get_acceptance_criteria()
    result = []

    for key, info in criteria.items():
        result.append({
            'key': key,
            'property': info.get('property', key),
            'condition': info.get('condition', ''),
            'unit': info.get('unit', ''),
            'display': f"{info.get('property', key)} {info.get('condition', '')} {info.get('unit', '')}"
        })

    return result
