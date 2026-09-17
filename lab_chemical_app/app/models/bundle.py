"""
Bundle model — a delivery-time grouping of pipes.

Pipes are physically bundled together for shipment. Each bundle has an external
bundle number, is linked to a sales order + customer + delivery receipt, and
aggregates multiple PipeStage delivery records.
"""
from datetime import datetime
from app import db


class Bundle(db.Model):
    __tablename__ = 'bundles'

    id = db.Column(db.Integer, primary_key=True)
    bundle_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    sales_order = db.Column(db.String(100), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), index=True)
    customer_name = db.Column(db.String(200))
    delivery_receipt = db.Column(db.String(100))
    delivery_date = db.Column(db.Date, index=True)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    customer = db.relationship('Customer', backref='bundles')
    created_by = db.relationship('User', backref='created_bundles')

    def __repr__(self):
        return f'<Bundle {self.bundle_number}>'
