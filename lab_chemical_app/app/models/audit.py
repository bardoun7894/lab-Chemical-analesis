"""
Audit Log Model - Tracks all changes to sensitive data
"""
from datetime import datetime
from app import db


class AuditLog(db.Model):
    """Audit trail for all data changes"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)  # CREATE, UPDATE, DELETE
    field_name = db.Column(db.String(100))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    reason = db.Column(db.Text)  # "Reason for change" from edit forms
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = db.relationship('User', backref='audit_logs')

    def __repr__(self):
        return f'<AuditLog {self.table_name}:{self.record_id} {self.action}>'

    @classmethod
    def log_change(cls, table_name, record_id, action, field_name=None,
                   old_value=None, new_value=None, reason=None, user_id=None):
        """Create an audit log entry"""
        entry = cls(
            table_name=table_name,
            record_id=record_id,
            action=action,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            reason=reason,
            user_id=user_id
        )
        db.session.add(entry)
        return entry

    @classmethod
    def log_create(cls, table_name, record_id, user_id=None, reason=None):
        """Log a record creation"""
        return cls.log_change(table_name, record_id, 'CREATE',
                            user_id=user_id, reason=reason)

    @classmethod
    def log_update(cls, table_name, record_id, changes, user_id=None, reason=None):
        """Log multiple field changes for an update"""
        entries = []
        for field, (old, new) in changes.items():
            entry = cls.log_change(
                table_name, record_id, 'UPDATE',
                field_name=field, old_value=old, new_value=new,
                reason=reason, user_id=user_id
            )
            entries.append(entry)
        return entries

    @classmethod
    def log_delete(cls, table_name, record_id, user_id=None, reason=None):
        """Log a record deletion"""
        return cls.log_change(table_name, record_id, 'DELETE',
                            user_id=user_id, reason=reason)
