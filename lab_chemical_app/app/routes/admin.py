"""
Admin Routes - User Management & Settings
"""
import os
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app import db, csrf
from app.models.user import User
from app.models.stage_defect_type import StageDefectType
from app.models.stage_decision_type import StageDecisionType
from app.models.pipe import Pipe
from app.models.chemical import Machine

# Path to element rules JSON
ELEMENT_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'element_rules.json')
MECHANICAL_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'mechanical_rules.json')
APP_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'app_settings.json')

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@login_required
@admin_required
def index():
    """Admin dashboard"""
    user_count = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()

    return render_template('admin/index.html',
                          user_count=user_count,
                          active_users=active_users)


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    """List all users"""
    users_list = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users_list)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    """Create new user"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '').strip()
        full_name_ar = request.form.get('full_name_ar', '').strip()
        role = request.form.get('role', 'viewer')
        department = request.form.get('department', '').strip()

        # Validation
        errors = []
        if not username:
            errors.append('Username is required')
        if not password:
            errors.append('Password is required')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters')
        if User.query.filter_by(username=username).first():
            errors.append('Username already exists')
        if role not in [User.ROLE_ADMIN, User.ROLE_SUPERVISOR, User.ROLE_OPERATOR, User.ROLE_VIEWER]:
            errors.append('Invalid role')

        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('admin/user_form.html',
                                  user=None,
                                  roles=get_roles())

        # Create user
        user = User(
            username=username,
            full_name=full_name or None,
            full_name_ar=full_name_ar or None,
            role=role,
            department=department or None
        )
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash(f'User {username} created successfully', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html',
                          user=None,
                          roles=get_roles())


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    """Edit user"""
    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        full_name_ar = request.form.get('full_name_ar', '').strip()
        role = request.form.get('role', 'viewer')
        department = request.form.get('department', '').strip()
        new_password = request.form.get('new_password', '')
        is_active = request.form.get('is_active') == 'on'

        # Validation
        errors = []
        if role not in [User.ROLE_ADMIN, User.ROLE_SUPERVISOR, User.ROLE_OPERATOR, User.ROLE_VIEWER]:
            errors.append('Invalid role')

        # Prevent disabling own admin account
        if user.id == current_user.id:
            if not is_active:
                errors.append('Cannot deactivate your own account')
            if role != User.ROLE_ADMIN:
                errors.append('Cannot remove admin role from your own account')

        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('admin/user_form.html',
                                  user=user,
                                  roles=get_roles())

        # Update user
        user.full_name = full_name or None
        user.full_name_ar = full_name_ar or None
        user.role = role
        user.department = department or None
        user.is_active = is_active

        # Update password if provided
        if new_password:
            if len(new_password) < 6:
                flash('Password must be at least 6 characters', 'error')
                return render_template('admin/user_form.html',
                                      user=user,
                                      roles=get_roles())
            user.set_password(new_password)

        db.session.commit()

        flash(f'User {user.username} updated successfully', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html',
                          user=user,
                          roles=get_roles())


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete user"""
    user = User.query.get_or_404(user_id)

    # Prevent deleting own account
    if user.id == current_user.id:
        flash('Cannot delete your own account', 'error')
        return redirect(url_for('admin.users'))

    username = user.username
    db.session.delete(user)
    db.session.commit()

    flash(f'User {username} deleted successfully', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_user_active(user_id):
    """Toggle user active status"""
    user = User.query.get_or_404(user_id)

    # Prevent deactivating own account
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot deactivate your own account'}), 400

    user.is_active = not user.is_active
    db.session.commit()

    status = 'activated' if user.is_active else 'deactivated'
    return jsonify({
        'success': True,
        'message': f'User {user.username} {status}',
        'is_active': user.is_active
    })


@admin_bp.route('/settings')
@login_required
@admin_required
def settings():
    """Application settings"""
    return render_template('admin/settings.html')


@admin_bp.route('/settings/element-rules')
@login_required
@admin_required
def element_rules():
    """Element rules configuration"""
    rules = load_element_rules()
    return render_template('admin/element_rules.html', rules=rules)


@admin_bp.route('/settings/element-rules/<element>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_element_rule(element):
    """Edit element rule"""
    rules = load_element_rules()

    # Find the element
    element_rule = None
    for rule in rules.get('rules', []):
        if rule['element'] == element:
            element_rule = rule
            break

    if not element_rule:
        flash(f'العنصر {element} غير موجود', 'error')
        return redirect(url_for('admin.element_rules'))

    if request.method == 'POST':
        try:
            # Get ranges from form
            new_ranges = []
            range_count = int(request.form.get('range_count', 0))

            for i in range(range_count):
                min_val = request.form.get(f'min_{i}')
                max_val = request.form.get(f'max_{i}')
                decision = request.form.get(f'decision_{i}')

                if min_val and max_val and decision:
                    new_ranges.append({
                        'min': float(min_val),
                        'max': float(max_val),
                        'decision': decision
                    })

            # Update the element's ranges
            element_rule['ranges'] = new_ranges

            # Save back to file
            save_element_rules(rules)

            flash(f'تم تحديث قواعد {element} بنجاح', 'success')
            return redirect(url_for('admin.element_rules'))

        except Exception as e:
            flash(f'خطأ: {str(e)}', 'error')

    decisions = [
        'فحص أخيرة فقط',
        'فحص أولى وأخيرة',
        'فحص الشحنة 100%',
        'تالف'
    ]

    return render_template('admin/edit_element_rule.html',
                          element=element,
                          rule=element_rule,
                          decisions=decisions)


@admin_bp.route('/api/element-rules')
@login_required
@admin_required
def api_get_element_rules():
    """API: Get all element rules"""
    rules = load_element_rules()
    return jsonify(rules)


@admin_bp.route('/api/element-rules/<element>', methods=['PUT'])
@csrf.exempt
@login_required
@admin_required
def api_update_element_rule(element):
    """API: Update element rule"""
    rules = load_element_rules()

    # Find the element
    element_rule = None
    for rule in rules.get('rules', []):
        if rule['element'] == element:
            element_rule = rule
            break

    if not element_rule:
        return jsonify({'error': f'Element {element} not found'}), 404

    try:
        data = request.get_json()
        element_rule['ranges'] = data.get('ranges', [])
        save_element_rules(rules)
        return jsonify({'success': True, 'message': f'{element} rules updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/element-rules/add', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def api_add_element():
    """API: Add new element"""
    rules = load_element_rules()
    data = request.get_json()

    element = data.get('element', '').upper()
    if not element:
        return jsonify({'error': 'Element name required'}), 400

    # Check if already exists
    for rule in rules.get('rules', []):
        if rule['element'] == element:
            return jsonify({'error': f'Element {element} already exists'}), 400

    # Add new element
    rules['rules'].append({
        'element': element,
        'ranges': data.get('ranges', [
            {'min': 0, 'max': 0.5, 'decision': 'فحص أخيرة فقط'},
            {'min': 0.51, 'max': 1, 'decision': 'تالف'}
        ])
    })

    save_element_rules(rules)
    return jsonify({'success': True, 'message': f'Element {element} added'})


@admin_bp.route('/api/element-rules/<element>', methods=['DELETE'])
@csrf.exempt
@login_required
@admin_required
def api_delete_element(element):
    """API: Delete element"""
    rules = load_element_rules()

    # Find and remove the element
    rules['rules'] = [r for r in rules.get('rules', []) if r['element'] != element]

    save_element_rules(rules)
    return jsonify({'success': True, 'message': f'Element {element} deleted'})


def load_element_rules():
    """Load element rules from JSON file"""
    try:
        with open(ELEMENT_RULES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {'rules': [], 'error': str(e)}


def save_element_rules(rules):
    """Save element rules to JSON file"""
    with open(ELEMENT_RULES_PATH, 'w', encoding='utf-8') as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def get_roles():
    """Get list of roles for forms"""
    return [
        {'value': User.ROLE_ADMIN, 'label': 'Admin', 'label_ar': 'مدير'},
        {'value': User.ROLE_SUPERVISOR, 'label': 'Supervisor', 'label_ar': 'مشرف'},
        {'value': User.ROLE_OPERATOR, 'label': 'Operator', 'label_ar': 'مشغل'},
        {'value': User.ROLE_VIEWER, 'label': 'Viewer', 'label_ar': 'مشاهد'},
    ]


# ============================================================================
# Stage Defect Types Management
# ============================================================================

@admin_bp.route('/defect-types')
@login_required
@admin_required
def defect_types():
    """Manage stage defect types"""
    stage_filter = request.args.get('stage', '')

    query = StageDefectType.query
    if stage_filter:
        query = query.filter_by(stage_name=stage_filter)

    defect_types_list = query.order_by(
        StageDefectType.stage_name,
        StageDefectType.sort_order
    ).all()

    stages = Pipe.STAGES

    return render_template('admin/defect_types.html',
                          defect_types=defect_types_list,
                          stages=stages,
                          selected_stage=stage_filter)


@admin_bp.route('/defect-types/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_defect_type():
    """Add new defect type"""
    if request.method == 'POST':
        defect_type = StageDefectType(
            stage_name=request.form['stage_name'],
            defect_name_en=request.form['defect_name_en'],
            defect_name_ar=request.form['defect_name_ar'],
            is_active=request.form.get('is_active') == 'on',
            sort_order=int(request.form.get('sort_order', 0))
        )

        try:
            db.session.add(defect_type)
            db.session.commit()
            flash('تم إضافة نوع العيب بنجاح / Defect type added successfully', 'success')
            return redirect(url_for('admin.defect_types'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    stages = Pipe.STAGES
    return render_template('admin/defect_type_form.html',
                          defect_type=None,
                          stages=stages)


@admin_bp.route('/defect-types/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_defect_type(id):
    """Edit defect type"""
    defect_type = StageDefectType.query.get_or_404(id)

    if request.method == 'POST':
        defect_type.stage_name = request.form['stage_name']
        defect_type.defect_name_en = request.form['defect_name_en']
        defect_type.defect_name_ar = request.form['defect_name_ar']
        defect_type.is_active = request.form.get('is_active') == 'on'
        defect_type.sort_order = int(request.form.get('sort_order', 0))

        try:
            db.session.commit()
            flash('تم تحديث نوع العيب بنجاح / Defect type updated successfully', 'success')
            return redirect(url_for('admin.defect_types'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    stages = Pipe.STAGES
    return render_template('admin/defect_type_form.html',
                          defect_type=defect_type,
                          stages=stages)


@admin_bp.route('/defect-types/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_defect_type(id):
    """Delete defect type"""
    defect_type = StageDefectType.query.get_or_404(id)

    try:
        db.session.delete(defect_type)
        db.session.commit()
        flash('تم حذف نوع العيب بنجاح / Defect type deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'خطأ: {str(e)}', 'danger')

    return redirect(url_for('admin.defect_types'))

# ============================================================================
# Stage Decision Types Management
# ============================================================================

@admin_bp.route('/decision-types')
@login_required
@admin_required
def decision_types():
    """Manage stage decision types"""
    stage_filter = request.args.get('stage', '')

    query = StageDecisionType.query
    if stage_filter:
        query = query.filter_by(stage_name=stage_filter)

    decision_types_list = query.order_by(
        StageDecisionType.stage_name,
        StageDecisionType.sort_order
    ).all()

    stages = Pipe.STAGES

    return render_template('admin/decision_types.html',
                          decision_types=decision_types_list,
                          stages=stages,
                          selected_stage=stage_filter)


@admin_bp.route('/decision-types/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_decision_type():
    """Add new decision type"""
    if request.method == 'POST':
        decision_type = StageDecisionType(
            stage_name=request.form['stage_name'],
            decision_name_en=request.form['decision_name_en'],
            decision_name_ar=request.form['decision_name_ar'],
            is_active=request.form.get('is_active') == 'on',
            sort_order=int(request.form.get('sort_order', 0))
        )

        try:
            db.session.add(decision_type)
            db.session.commit()
            flash('تم إضافة نوع القرار بنجاح / Decision type added successfully', 'success')
            return redirect(url_for('admin.decision_types'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    stages = Pipe.STAGES
    return render_template('admin/decision_type_form.html',
                          decision_type=None,
                          stages=stages)


@admin_bp.route('/decision-types/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_decision_type(id):
    """Edit decision type"""
    decision_type = StageDecisionType.query.get_or_404(id)

    if request.method == 'POST':
        decision_type.stage_name = request.form['stage_name']
        decision_type.decision_name_en = request.form['decision_name_en']
        decision_type.decision_name_ar = request.form['decision_name_ar']
        decision_type.is_active = request.form.get('is_active') == 'on'
        decision_type.sort_order = int(request.form.get('sort_order', 0))

        try:
            db.session.commit()
            flash('تم تحديث نوع القرار بنجاح / Decision type updated successfully', 'success')
            return redirect(url_for('admin.decision_types'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    stages = Pipe.STAGES
    return render_template('admin/decision_type_form.html',
                          decision_type=decision_type,
                          stages=stages)


@admin_bp.route('/decision-types/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_decision_type(id):
    """Delete decision type"""
    decision_type = StageDecisionType.query.get_or_404(id)

    try:
        db.session.delete(decision_type)
        db.session.commit()
        flash('تم حذف نوع القرار بنجاح / Decision type deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'خطأ: {str(e)}', 'danger')

    return redirect(url_for('admin.decision_types'))

# ============================================================================
# Machine Management
# ============================================================================

@admin_bp.route('/machines')
@login_required
@admin_required
def machines():
    """Manage machines"""
    stage_filter = request.args.get('stage', '')

    query = Machine.query
    if stage_filter:
        query = query.filter_by(stage=stage_filter)

    machines_list = query.order_by(
        Machine.stage,
        Machine.machine_code
    ).all()

    # Get unique stages from machines
    stages = db.session.query(Machine.stage).distinct().order_by(Machine.stage).all()
    stages = [s[0] for s in stages if s[0]]

    return render_template('admin/machines.html',
                          machines=machines_list,
                          stages=stages,
                          selected_stage=stage_filter)


@admin_bp.route('/machines/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_machine():
    """Add new machine"""
    if request.method == 'POST':
        machine = Machine(
            machine_code=request.form['machine_code'],
            machine_name=request.form.get('machine_name', ''),
            stage=request.form.get('stage'),
            is_active=request.form.get('is_active') == 'on'
        )

        try:
            db.session.add(machine)
            db.session.commit()
            flash('تم إضافة الماكينة بنجاح / Machine added successfully', 'success')
            return redirect(url_for('admin.machines'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    # Production stages that can have machines
    production_stages = ['Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating']

    return render_template('admin/machine_form.html',
                          machine=None,
                          stages=production_stages)


@admin_bp.route('/machines/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_machine(id):
    """Edit machine"""
    machine = Machine.query.get_or_404(id)

    if request.method == 'POST':
        machine.machine_code = request.form['machine_code']
        machine.machine_name = request.form.get('machine_name', '')
        machine.stage = request.form.get('stage')
        machine.is_active = request.form.get('is_active') == 'on'

        try:
            db.session.commit()
            flash('تم تحديث الماكينة بنجاح / Machine updated successfully', 'success')
            return redirect(url_for('admin.machines'))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    # Production stages that can have machines
    production_stages = ['Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating']

    return render_template('admin/machine_form.html',
                          machine=machine,
                          stages=production_stages)


@admin_bp.route('/machines/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_machine(id):
    """Delete machine"""
    machine = Machine.query.get_or_404(id)

    try:
        db.session.delete(machine)
        db.session.commit()
        flash('تم حذف الماكينة بنجاح / Machine deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'خطأ: {str(e)}', 'danger')

    return redirect(url_for('admin.machines'))


# ============================================================================
# Mechanical Rules Management
# ============================================================================

@admin_bp.route('/settings/mechanical-rules')
@login_required
@admin_required
def mechanical_rules():
    """Mechanical property rules configuration"""
    rules = load_mechanical_rules()
    return render_template('admin/mechanical_rules.html', rules=rules)


@admin_bp.route('/settings/mechanical-rules/update', methods=['POST'])
@login_required
@admin_required
def update_mechanical_criterion():
    """Update a mechanical acceptance criterion"""
    rules = load_mechanical_rules()

    criterion_key = request.form.get('criterion_key')
    condition = request.form.get('condition')
    unit = request.form.get('unit')

    if not criterion_key or criterion_key not in rules.get('acceptance_criteria', {}):
        flash(f'المعيار {criterion_key} غير موجود', 'error')
        return redirect(url_for('admin.mechanical_rules'))

    try:
        # Update the criterion
        rules['acceptance_criteria'][criterion_key]['condition'] = condition
        if unit:
            rules['acceptance_criteria'][criterion_key]['unit'] = unit

        # Save back to file
        save_mechanical_rules(rules)

        flash(f'تم تحديث المعيار بنجاح', 'success')
    except Exception as e:
        flash(f'خطأ: {str(e)}', 'error')

    return redirect(url_for('admin.mechanical_rules'))


@admin_bp.route('/api/mechanical-rules')
@login_required
@admin_required
def api_get_mechanical_rules():
    """API: Get all mechanical rules"""
    rules = load_mechanical_rules()
    return jsonify(rules)


@admin_bp.route('/api/mechanical-rules/<property_code>', methods=['PUT'])
@csrf.exempt
@login_required
@admin_required
def api_update_mechanical_rule(property_code):
    """API: Update mechanical property rule"""
    rules = load_mechanical_rules()

    # Find the property
    property_rule = None
    for rule in rules.get('rules', []):
        if rule['property'] == property_code:
            property_rule = rule
            break

    if not property_rule:
        return jsonify({'error': f'Property {property_code} not found'}), 404

    try:
        data = request.get_json()
        property_rule['ranges'] = data.get('ranges', [])
        save_mechanical_rules(rules)
        return jsonify({'success': True, 'message': f'{property_code} rules updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/mechanical-rules/add', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def api_add_mechanical_property():
    """API: Add new mechanical property"""
    rules = load_mechanical_rules()
    data = request.get_json()

    property_code = data.get('property', '').lower()
    if not property_code:
        return jsonify({'error': 'Property code required'}), 400

    # Check if already exists
    for rule in rules.get('rules', []):
        if rule['property'] == property_code:
            return jsonify({'error': f'Property {property_code} already exists'}), 400

    # Add new property
    rules['rules'].append({
        'property': property_code,
        'name': data.get('name', property_code),
        'name_ar': data.get('name_ar', property_code),
        'unit': data.get('unit', ''),
        'ranges': data.get('ranges', [
            {'min': 0, 'max': 50, 'decision': 'فحص أخيرة فقط'},
            {'min': 50.01, 'max': 100, 'decision': 'تالف'}
        ])
    })

    save_mechanical_rules(rules)
    return jsonify({'success': True, 'message': f'Property {property_code} added'})


@admin_bp.route('/api/mechanical-rules/<property_code>', methods=['DELETE'])
@csrf.exempt
@login_required
@admin_required
def api_delete_mechanical_property(property_code):
    """API: Delete mechanical property"""
    rules = load_mechanical_rules()

    # Find and remove the property
    rules['rules'] = [r for r in rules.get('rules', []) if r['property'] != property_code]

    save_mechanical_rules(rules)
    return jsonify({'success': True, 'message': f'Property {property_code} deleted'})


def load_mechanical_rules():
    """Load mechanical rules from JSON file"""
    try:
        with open(MECHANICAL_RULES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {'rules': [], 'error': str(e)}


def save_mechanical_rules(rules):
    """Save mechanical rules to JSON file"""
    with open(MECHANICAL_RULES_PATH, 'w', encoding='utf-8') as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


# ============================================================================
# AI Settings Management
# ============================================================================

def load_app_settings():
    """Load app settings from JSON file"""
    try:
        with open(APP_SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'ai': {'gemini_api_key': '', 'gemini_model': 'gemini-2.5-flash', 'enabled': True}}


def save_app_settings(settings):
    """Save app settings to JSON file"""
    with open(APP_SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


@admin_bp.route('/settings/ai')
@login_required
@admin_required
def ai_settings():
    """AI/Gemini settings configuration"""
    settings = load_app_settings()
    ai_settings = settings.get('ai', {})

    # Mask the API key for display (show last 4 chars only)
    api_key = ai_settings.get('gemini_api_key', '')
    masked_key = ''
    if api_key:
        masked_key = '*' * (len(api_key) - 4) + api_key[-4:] if len(api_key) > 4 else '****'

    return render_template('admin/ai_settings.html',
                          ai_settings=ai_settings,
                          masked_key=masked_key)


@admin_bp.route('/settings/ai/update', methods=['POST'])
@login_required
@admin_required
def update_ai_settings():
    """Update AI settings"""
    settings = load_app_settings()

    # Get form data
    api_key = request.form.get('gemini_api_key', '').strip()
    model = request.form.get('gemini_model', 'gemini-2.5-flash').strip()
    enabled = request.form.get('enabled') == 'on'

    # Initialize ai settings if not exists
    if 'ai' not in settings:
        settings['ai'] = {}

    # Only update API key if a new one is provided (not the masked version)
    if api_key and not api_key.startswith('*'):
        settings['ai']['gemini_api_key'] = api_key

    settings['ai']['gemini_model'] = model
    settings['ai']['enabled'] = enabled

    try:
        save_app_settings(settings)
        flash('تم تحديث إعدادات الذكاء الاصطناعي بنجاح / AI settings updated successfully', 'success')
    except Exception as e:
        flash(f'خطأ: {str(e)}', 'danger')

    return redirect(url_for('admin.ai_settings'))


# ============================================================================
# Sticker Settings Management
# ============================================================================

STICKER_IMAGES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images')

@admin_bp.route('/settings/stickers')
@login_required
@admin_required
def sticker_settings():
    """Sticker design settings"""
    settings = load_app_settings()
    sticker_settings = settings.get('sticker', {})

    # Check if logo and recycle images exist
    logo_exists = os.path.exists(os.path.join(STICKER_IMAGES_PATH, 'gcp_logo.jpg'))
    recycle_exists = os.path.exists(os.path.join(STICKER_IMAGES_PATH, 'recycle.jpg'))

    import time
    return render_template('admin/sticker_settings.html',
                          sticker_settings=sticker_settings,
                          logo_exists=logo_exists,
                          recycle_exists=recycle_exists,
                          now=int(time.time()))


@admin_bp.route('/settings/stickers', methods=['POST'])
@login_required
@admin_required
def update_sticker_settings():
    """Update sticker settings"""
    from werkzeug.utils import secure_filename

    settings = load_app_settings()

    # Initialize sticker settings if not exists
    if 'sticker' not in settings:
        settings['sticker'] = {}

    # Handle logo upload
    if 'logo' in request.files:
        logo_file = request.files['logo']
        if logo_file and logo_file.filename:
            # Validate file type
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
            ext = logo_file.filename.rsplit('.', 1)[-1].lower()
            if ext in allowed_extensions:
                # Ensure images directory exists
                os.makedirs(STICKER_IMAGES_PATH, exist_ok=True)
                # Save as gcp_logo.jpg (convert if needed)
                from PIL import Image
                img = Image.open(logo_file)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                logo_path = os.path.join(STICKER_IMAGES_PATH, 'gcp_logo.jpg')
                img.save(logo_path, 'JPEG', quality=95)
                flash('Logo uploaded successfully', 'success')
            else:
                flash('Invalid logo file type. Use JPG, PNG, or GIF.', 'warning')

    # Handle recycle image upload
    if 'recycle' in request.files:
        recycle_file = request.files['recycle']
        if recycle_file and recycle_file.filename:
            # Validate file type
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
            ext = recycle_file.filename.rsplit('.', 1)[-1].lower()
            if ext in allowed_extensions:
                # Ensure images directory exists
                os.makedirs(STICKER_IMAGES_PATH, exist_ok=True)
                # Save as recycle.jpg (convert if needed)
                from PIL import Image
                img = Image.open(recycle_file)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                recycle_path = os.path.join(STICKER_IMAGES_PATH, 'recycle.jpg')
                img.save(recycle_path, 'JPEG', quality=95)
                flash('Recycler symbol uploaded successfully', 'success')
            else:
                flash('Invalid recycle file type. Use JPG, PNG, or GIF.', 'warning')

    # Update text settings
    settings['sticker']['company_name'] = request.form.get('company_name', 'GCP Ductile Iron Pipes')
    settings['sticker']['website_url'] = request.form.get('website_url', 'www.gcpipes.com')
    settings['sticker']['website_color'] = request.form.get('website_color', '#0066CC')
    settings['sticker']['text_color'] = request.form.get('text_color', '#333333')

    # Update sizes
    settings['sticker']['sizes'] = {
        'small': [
            int(request.form.get('size_small_w', 80)),
            int(request.form.get('size_small_h', 50))
        ],
        'medium': [
            int(request.form.get('size_medium_w', 100)),
            int(request.form.get('size_medium_h', 60))
        ],
        'large': [
            int(request.form.get('size_large_w', 120)),
            int(request.form.get('size_large_h', 80))
        ],
        'gcp': [
            int(request.form.get('size_gcp_w', 140)),
            int(request.form.get('size_gcp_h', 90))
        ]
    }

    # Update layout options
    settings['sticker']['show_logo'] = request.form.get('show_logo') == 'on'
    settings['sticker']['show_recycle'] = request.form.get('show_recycle') == 'on'
    settings['sticker']['show_qr'] = request.form.get('show_qr') == 'on'
    settings['sticker']['show_website'] = request.form.get('show_website') == 'on'
    settings['sticker']['dpi'] = int(request.form.get('dpi', 300))

    try:
        save_app_settings(settings)
        flash('Sticker settings updated successfully / تم تحديث إعدادات الملصقات بنجاح', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('admin.sticker_settings'))


@admin_bp.route('/api/sticker-settings')
@login_required
def api_get_sticker_settings():
    """API: Get sticker settings"""
    settings = load_app_settings()
    return jsonify(settings.get('sticker', {}))


@admin_bp.route('/api/ai-settings/test', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def test_ai_connection():
    """Test Gemini API connection"""
    import requests

    settings = load_app_settings()
    api_key = settings.get('ai', {}).get('gemini_api_key', '')

    # If no key in settings, try environment variable
    if not api_key:
        api_key = os.environ.get('GEMINI_API_KEY', '')

    if not api_key:
        return jsonify({'success': False, 'message': 'API key not configured'})

    model = settings.get('ai', {}).get('gemini_model', 'gemini-2.5-flash')

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": "Say 'Connection successful' in Arabic"}]}],
                "generationConfig": {"maxOutputTokens": 50}
            },
            timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            return jsonify({'success': True, 'message': f'Connection successful! Response: {text[:100]}'})
        else:
            return jsonify({'success': False, 'message': f'API Error: {response.status_code} - {response.text[:200]}'})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'Connection timeout'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
