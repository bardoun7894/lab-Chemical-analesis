"""
User Model for Authentication
"""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class User(UserMixin, db.Model):
    """User model for authentication and authorization"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100))
    full_name_ar = db.Column(db.String(100))  # Arabic name
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(20), nullable=False, default='viewer')
    department = db.Column(db.String(50))  # Lab, Production, QC
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # User roles
    ROLE_ADMIN = 'admin'
    ROLE_SUPERVISOR = 'supervisor'
    ROLE_OPERATOR = 'operator'
    ROLE_VIEWER = 'viewer'

    ROLES = [ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_OPERATOR, ROLE_VIEWER]

    # Departments
    DEPT_LAB = 'Lab'
    DEPT_PRODUCTION = 'Production'
    DEPT_QC = 'QC'
    DEPT_ADMIN = 'Admin'

    DEPARTMENTS = [DEPT_LAB, DEPT_PRODUCTION, DEPT_QC, DEPT_ADMIN]

    def set_password(self, password):
        """Set password hash"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)

    def update_last_login(self):
        """Update last login timestamp"""
        self.last_login = datetime.utcnow()

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_supervisor(self):
        return self.role in [self.ROLE_ADMIN, self.ROLE_SUPERVISOR]

    @property
    def can_edit(self):
        """Can user edit data"""
        return self.role in [self.ROLE_ADMIN, self.ROLE_SUPERVISOR, self.ROLE_OPERATOR]

    @property
    def can_approve(self):
        """Can user approve decisions"""
        return self.role in [self.ROLE_ADMIN, self.ROLE_SUPERVISOR]

    @property
    def display_name(self):
        """Get display name (prefer full name over username)"""
        return self.full_name or self.username

    def __repr__(self):
        return f'<User {self.username}>'

    @staticmethod
    def create_default_admin():
        """Create default admin user if none exists"""
        from app import db
        if not User.query.filter_by(role=User.ROLE_ADMIN).first():
            admin = User(
                username='admin',
                full_name='Administrator',
                full_name_ar='مدير النظام',
                role=User.ROLE_ADMIN,
                department=User.DEPT_ADMIN,
                is_active=True
            )
            admin.set_password('admin123')  # Change in production!
            db.session.add(admin)
            db.session.commit()
            return admin
        return None
