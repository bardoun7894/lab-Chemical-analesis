"""
Admin Routes - User Management
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app import db
from app.models.user import User

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


def get_roles():
    """Get list of roles for forms"""
    return [
        {'value': User.ROLE_ADMIN, 'label': 'Admin', 'label_ar': 'مدير'},
        {'value': User.ROLE_SUPERVISOR, 'label': 'Supervisor', 'label_ar': 'مشرف'},
        {'value': User.ROLE_OPERATOR, 'label': 'Operator', 'label_ar': 'مشغل'},
        {'value': User.ROLE_VIEWER, 'label': 'Viewer', 'label_ar': 'مشاهد'},
    ]
