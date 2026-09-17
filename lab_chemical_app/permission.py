"""
Permission Models - Fine-grained access control
"""
from sqlalchemy.exc import IntegrityError

from app import db


# Action verbs. Each (module, screen) row carries exactly one of these; it is
# what the permission matrix renders in each cell.
ACTION_READ = 'read'
ACTION_CREATE = 'create'
ACTION_WRITE = 'write'
ACTION_DELETE = 'delete'
ACTION_EXPORT = 'export'
ACTION_EXECUTE = 'execute'

ACTION_LABELS = {
    ACTION_READ: ('Read', 'قراءة'),
    ACTION_CREATE: ('Create', 'إنشاء'),
    ACTION_WRITE: ('Write', 'تعديل'),
    ACTION_DELETE: ('Delete', 'حذف'),
    ACTION_EXPORT: ('Export', 'تصدير'),
    ACTION_EXECUTE: ('Run', 'تنفيذ'),
}

# The authoritative screen -> action taxonomy. Adding a screen here is enough:
# seed_default_permissions() is idempotent and self-healing, so a new row is
# created on the next boot rather than silently falling through to a denial.
#
# Admin screens are split CRUD-style (list/add/edit/delete) so a role can be
# granted "view" without "delete", and the UI can hide the destructive buttons.
# Execute verbs (toggle, move, test) are explicit so they can be granted or
# revoked independently of edit.
MODULES = {
    'chemical': {
        'list': ACTION_READ, 'add': ACTION_CREATE, 'edit': ACTION_WRITE,
        'delete': ACTION_DELETE, 'export': ACTION_EXPORT, 'auto_decide': ACTION_EXECUTE,
    },
    'mechanical': {
        'list': ACTION_READ, 'add': ACTION_CREATE, 'edit': ACTION_WRITE,
        'delete': ACTION_DELETE, 'export': ACTION_EXPORT, 'retest': ACTION_EXECUTE,
    },
    'stages': {
        'list': ACTION_READ, 'add': ACTION_CREATE, 'edit': ACTION_WRITE,
        'delete': ACTION_DELETE, 'tracking': ACTION_READ, 'delivery': ACTION_READ,
        'shift_dashboard': ACTION_READ,
    },
    'reports': {
        'list': ACTION_READ, 'view': ACTION_READ, 'export': ACTION_EXPORT,
        'daily_production': ACTION_READ, 'chemical_analysis': ACTION_READ,
        'defect_summary': ACTION_READ,
    },
    'stickers': {
        'list': ACTION_READ, 'generate': ACTION_CREATE,
        'bulk_print': ACTION_EXECUTE, 'batch': ACTION_EXECUTE,
    },
    'orders': {
        'list': ACTION_READ, 'add': ACTION_CREATE, 'edit': ACTION_WRITE,
        'delete': ACTION_DELETE, 'progress': ACTION_READ, 'print_stickers': ACTION_EXECUTE,
    },
    'analytics': {
        'pivot': ACTION_READ, 'kpis': ACTION_READ, 'dashboards': ACTION_READ,
        'kpis_manage': ACTION_WRITE, 'dashboards_manage': ACTION_WRITE,
        'query_manager': ACTION_EXECUTE, 'import': ACTION_CREATE,
    },
    'chatbot': {
        'view': ACTION_READ, 'send': ACTION_EXECUTE,
    },
    # AI features are kept in their own module so they can be locked down to
    # admin only, regardless of who has access to the chemical/mechanical
    # analysis screens. Supervisor may auto-decide (rules-only) but only admin
    # can call the AI endpoints that hit an external LLM.
    'ai': {
        'chemical_analysis': ACTION_EXECUTE,
        'mechanical_analysis': ACTION_EXECUTE,
        'settings_list': ACTION_READ,
        'settings_edit': ACTION_WRITE,
        'settings_test': ACTION_EXECUTE,
    },
    'main': {
        'dashboard': ACTION_READ, 'attachments': ACTION_WRITE,
    },
    'admin': {
        # Read-only access to settings / audit
        'audit_list': ACTION_READ,
        'permissions_list': ACTION_READ,
        'settings_list': ACTION_READ,
        'element_rules_list': ACTION_READ,
        'mechanical_rules_list': ACTION_READ,
        'email_settings_list': ACTION_READ,
        'sticker_settings_list': ACTION_READ,
        'ai_settings_list': ACTION_READ,
        'product_code_order_list': ACTION_READ,
        # CRUD for entities
        'users_list': ACTION_READ, 'users_add': ACTION_CREATE,
        'users_edit': ACTION_WRITE, 'users_delete': ACTION_DELETE,
        'customers_list': ACTION_READ, 'customers_add': ACTION_CREATE,
        'customers_edit': ACTION_WRITE, 'customers_delete': ACTION_DELETE,
        'machines_list': ACTION_READ, 'machines_add': ACTION_CREATE,
        'machines_edit': ACTION_WRITE, 'machines_delete': ACTION_DELETE,
        'machines_toggle': ACTION_EXECUTE,
        'furnaces_list': ACTION_READ, 'furnaces_add': ACTION_CREATE,
        'furnaces_edit': ACTION_WRITE, 'furnaces_delete': ACTION_DELETE,
        'furnaces_toggle': ACTION_EXECUTE,
        'molds_list': ACTION_READ, 'molds_add': ACTION_CREATE,
        'molds_edit': ACTION_WRITE, 'molds_delete': ACTION_DELETE,
        'products_list': ACTION_READ, 'products_add': ACTION_CREATE,
        'products_edit': ACTION_WRITE, 'products_delete': ACTION_DELETE,
        'product_parameters_list': ACTION_READ, 'product_parameters_add': ACTION_CREATE,
        'product_parameters_edit': ACTION_WRITE, 'product_parameters_delete': ACTION_DELETE,
        'decision_types_list': ACTION_READ, 'decision_types_add': ACTION_CREATE,
        'decision_types_edit': ACTION_WRITE, 'decision_types_delete': ACTION_DELETE,
        'defect_types_list': ACTION_READ, 'defect_types_add': ACTION_CREATE,
        'defect_types_edit': ACTION_WRITE, 'defect_types_delete': ACTION_DELETE,
        'production_stages_list': ACTION_READ, 'production_stages_add': ACTION_CREATE,
        'production_stages_edit': ACTION_WRITE, 'production_stages_delete': ACTION_DELETE,
        'production_stages_toggle': ACTION_EXECUTE, 'production_stages_move': ACTION_EXECUTE,
        # Settings write
        'settings_edit': ACTION_WRITE,
        'element_rules_edit': ACTION_WRITE,
        'mechanical_rules_edit': ACTION_WRITE,
        'email_settings_edit': ACTION_WRITE, 'email_settings_test': ACTION_EXECUTE,
        'sticker_settings_edit': ACTION_WRITE,
        'ai_settings_edit': ACTION_WRITE, 'ai_settings_test': ACTION_EXECUTE,
        'product_code_order_edit': ACTION_WRITE,
        'permissions_edit': ACTION_WRITE,
    },
}

# Screens granted to each role when its permission row is first created.
# Applied to newly-created rows only, so a deliberate revoke is never re-granted
# on the next boot. 'admin' and 'super_admin' get everything.
#
# Convention: each role lists only the screens it should auto-receive on
# creation of a new permission row. A role with `True` receives every screen
# in every module — including future ones — which is the right default for
# privileged roles only.
ROLE_DEFAULTS = {
    'super_admin': True,
    'admin': True,
    'supervisor': {
        'chemical': ['list', 'add', 'edit', 'export', 'auto_decide'],
        'mechanical': ['list', 'add', 'edit', 'export', 'retest'],
        'stages': ['list', 'add', 'edit', 'tracking', 'delivery', 'shift_dashboard'],
        'reports': ['list', 'view', 'export', 'daily_production', 'chemical_analysis', 'defect_summary'],
        'stickers': ['list', 'generate', 'bulk_print', 'batch'],
        'orders': ['list', 'add', 'edit', 'progress', 'print_stickers'],
        'analytics': ['pivot', 'kpis', 'dashboards', 'kpis_manage',
                      'dashboards_manage', 'query_manager', 'import'],
        'chatbot': ['view', 'send'],
        'main': ['dashboard', 'attachments'],
        # Supervisors never see admin screens (incl. AI).
    },
    'operator': {
        'chemical': ['list', 'add', 'edit'],
        'mechanical': ['list', 'add', 'edit'],
        'stages': ['list', 'add', 'edit', 'tracking', 'delivery', 'shift_dashboard'],
        'reports': ['list', 'view', 'daily_production', 'chemical_analysis', 'defect_summary'],
        'stickers': ['list', 'generate', 'batch'],
        'orders': ['list', 'progress'],
        'analytics': ['pivot', 'kpis', 'dashboards'],
        'chatbot': ['view', 'send'],
        'main': ['dashboard'],
        # Operators never see admin screens and cannot auto-decide.
    },
    'viewer': {
        'chemical': ['list'],
        'mechanical': ['list'],
        'stages': ['list', 'tracking', 'shift_dashboard'],
        'reports': ['list', 'view', 'daily_production', 'chemical_analysis', 'defect_summary'],
        'stickers': ['list'],
        'orders': ['list'],
        'analytics': ['pivot', 'kpis', 'dashboards'],
        'chatbot': ['view'],
        'main': ['dashboard'],
    },
}


class Permission(db.Model):
    """Permission definitions"""
    __tablename__ = 'permissions'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(50), nullable=False)  # chemical, mechanical, stages, reports, admin
    screen = db.Column(db.String(50), nullable=False)   # list, add, edit, delete, export
    action = db.Column(db.String(50), nullable=False)   # read, create, write, delete, export, execute
    description = db.Column(db.String(200))

    __table_args__ = (
        db.UniqueConstraint('module', 'screen', 'action', name='uix_permission'),
    )

    # Relationships
    role_permissions = db.relationship('RolePermission', backref='permission',
                                      cascade='all, delete-orphan')

    @property
    def action_label(self):
        return ACTION_LABELS.get(self.action, (self.action.title(), self.action))[0]

    @property
    def action_label_ar(self):
        return ACTION_LABELS.get(self.action, (self.action, self.action))[1]

    def __repr__(self):
        return f'<Permission {self.module}.{self.screen}.{self.action}>'


class RolePermission(db.Model):
    """Maps roles to permissions"""
    __tablename__ = 'role_permissions'

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False, index=True)
    permission_id = db.Column(db.Integer, db.ForeignKey('permissions.id'), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('role', 'permission_id', name='uix_role_permission'),
    )

    def __repr__(self):
        return f'<RolePermission {self.role}:{self.permission_id}>'


def seed_default_permissions():
    """Reconcile the permissions table with MODULES.

    Idempotent and safe to run on every boot. Rows are matched on (module,
    screen) so a legacy action='access' row is migrated in place, keeping its id
    and its RolePermission children.

    Role defaults are applied only to rows created by this call, so a permission
    an admin deliberately revoked is never silently re-granted.

    Returns True if this call seeded, False if a concurrent worker won the race.
    """
    existing = {(p.module, p.screen): p for p in Permission.query.all()}
    wanted = {
        (module, screen): action
        for module, screens in MODULES.items()
        for screen, action in screens.items()
    }

    created = []
    for key, action in wanted.items():
        module, screen = key
        perm = existing.get(key)
        if perm is None:
            perm = Permission(module=module, screen=screen, action=action,
                              description=f'{module}.{screen} ({action})')
            db.session.add(perm)
            created.append(perm)
        elif perm.action != action:
            perm.action = action
            perm.description = f'{module}.{screen} ({action})'

    # Drop rows for screens that no longer exist (cascade removes their grants).
    for key, perm in existing.items():
        if key not in wanted:
            db.session.delete(perm)

    try:
        db.session.flush()

        for perm in created:
            for role, config in ROLE_DEFAULTS.items():
                granted = config is True or (
                    isinstance(config, dict)
                    and perm.screen in config.get(perm.module, [])
                )
                if granted:
                    db.session.add(RolePermission(role=role, permission_id=perm.id))

        db.session.commit()
        return True
    except IntegrityError:
        # Gunicorn boots this once per worker, so several run concurrently on a
        # cold start and all but one lose on the unique constraint. The winner's
        # rows are already committed; the losers just roll back. Not an error.
        db.session.rollback()
        return False
