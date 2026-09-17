"""
Attachment Model - Document attachments for any record
"""
from datetime import datetime
from app import db


class Attachment(db.Model):
    """Document attachments (files attached to chemical, mechanical, stage records)"""
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)  # e.g. 'chemical_analyses'
    record_id = db.Column(db.Integer, nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer)  # bytes
    mime_type = db.Column(db.String(100))
    description = db.Column(db.Text)

    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    uploaded_by = db.relationship('User', backref='uploaded_attachments')

    def __repr__(self):
        return f'<Attachment {self.filename} for {self.table_name}:{self.record_id}>'

    @classmethod
    def get_for_record(cls, table_name, record_id):
        """Get all attachments for a specific record"""
        return cls.query.filter_by(
            table_name=table_name, record_id=record_id
        ).order_by(cls.uploaded_at.desc()).all()

    @property
    def file_size_display(self):
        """Human-readable file size"""
        if not self.file_size:
            return 'Unknown'
        if self.file_size < 1024:
            return f'{self.file_size} B'
        elif self.file_size < 1024 * 1024:
            return f'{self.file_size / 1024:.1f} KB'
        else:
            return f'{self.file_size / (1024 * 1024):.1f} MB'
