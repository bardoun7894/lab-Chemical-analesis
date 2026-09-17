"""
Permission Service - @requires_permission decorator and helpers
"""
from functools import wraps

from flask import flash, redirect, url_for, current_app, request, has_request_context
from flask_login import current_user


def requires_permission(module, screen, action=None):
    """Decorator to check if current user has a specific permission"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in to access this page.', 'info')
                return redirect(url_for('auth.login'))

            # The owner bypasses the matrix; every other role is governed by it.
            if current_user.is_super_admin:
                return f(*args, **kwargs)

            if not has_permission(current_user.role, module, screen, action):
                flash('Access denied. You do not have permission for this action.', 'error')
                return redirect(url_for('main.index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def super_admin_required(f):
    """Decorator restricting a route to the app owner."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'info')
            return redirect(url_for('auth.login'))
        if not current_user.is_super_admin:
            flash('Access denied. Owner privileges required.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


def has_permission(role, module, screen, action=None):
    """Check if a role has a specific permission.

    Fails closed: a permission that has no row in the table is denied, not
    granted. `action` is optional because (module, screen) is unique in the
    taxonomy — callers pin it only when they want the verb asserted too.
    """
    from app.models.permission import Permission, RolePermission
    from app.models.user import User

    if role == User.ROLE_SUPER_ADMIN:
        return True

    query = Permission.query.filter_by(module=module, screen=screen)
    if action:
        query = query.filter_by(action=action)
    perm = query.first()

    if not perm:
        current_app.logger.warning(
            'Undefined permission %s.%s requested by role=%s - denying',
            module, screen, role,
        )
        return False

    return RolePermission.query.filter_by(
        role=role, permission_id=perm.id
    ).first() is not None


def get_role_permissions(role):
    """Return the set of keys granted to a role.

    Emits both the bare 'module.screen' key and the fully qualified
    'module.screen.action' key so callers can match on either.
    """
    from app.models.permission import Permission, RolePermission

    perms = Permission.query.join(
        RolePermission, RolePermission.permission_id == Permission.id
    ).filter(RolePermission.role == role).all()

    keys = set()
    for p in perms:
        keys.add(f'{p.module}.{p.screen}')
        keys.add(f'{p.module}.{p.screen}.{p.action}')
    return keys


def granted_keys_for_current_user():
    """The current user's granted permission keys, or None when they are the
    owner (everything granted).

    Cached on the request object rather than on `g`: a rendered page calls can()
    a few dozen times, but `g` outlives a single request whenever an app context
    is pushed around it, which would serve one user's grants to the next.
    """
    from app.models.user import User

    if not current_user.is_authenticated:
        return set()
    if current_user.role == User.ROLE_SUPER_ADMIN:
        return None  # sentinel: owner, everything granted

    if not has_request_context():
        return get_role_permissions(current_user.role)

    cached = getattr(request, '_permission_keys', None)
    if cached is None or cached[0] != current_user.role:
        cached = (current_user.role, get_role_permissions(current_user.role))
        request._permission_keys = cached
    return cached[1]


def can(module, screen):
    """Template helper. True when the current user may reach module.screen."""
    if not current_user.is_authenticated:
        return False
    keys = granted_keys_for_current_user()
    if keys is None:
        return True
    return f'{module}.{screen}' in keys


def get_permission_matrix():
    """Full permission matrix for the admin UI, grouped by module.

    Loads every grant in a single query rather than one per (role, permission).
    """
    from app.models.permission import Permission, RolePermission
    from app.models.user import User

    permissions = Permission.query.order_by(
        Permission.module, Permission.screen
    ).all()
    roles = User.ROLES

    granted = {
        (rp.role, rp.permission_id) for rp in RolePermission.query.all()
    }

    matrix = {}
    for perm in permissions:
        matrix.setdefault(perm.module, []).append({
            'key': f'{perm.module}.{perm.screen}',
            'module': perm.module,
            'screen': perm.screen,
            'action': perm.action,
            'action_label': perm.action_label,
            'action_label_ar': perm.action_label_ar,
            'roles': {
                role: (role == User.ROLE_SUPER_ADMIN) or ((role, perm.id) in granted)
                for role in roles
            },
        })

    return matrix, roles
