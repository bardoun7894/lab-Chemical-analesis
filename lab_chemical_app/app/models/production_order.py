"""
Production Order Model - امر انتاج / امر شغل
Tracks batches of pipes for production
"""
from datetime import datetime
from app import db


class ProductionOrder(db.Model):
    """Production Order - امر الانتاج"""
    __tablename__ = 'production_orders'

    id = db.Column(db.Integer, primary_key=True)

    # Order Identification
    order_number = db.Column(db.String(50), unique=True, nullable=False, index=True)  # رقم امر الانتاج

    # Customer Info
    customer_name = db.Column(db.String(200))  # اسم العميل
    customer_code = db.Column(db.String(50))

    # Order Details
    target_quantity = db.Column(db.Integer, nullable=False)  # الكمية المطلوبة
    sales_number = db.Column(db.String(100))  # رقم أمر البيع
    diameter = db.Column(db.Integer)  # DN: 300, 500, 600
    pipe_class = db.Column(db.String(20))  # K9, C25, Fittings

    # Product Specifications
    product_code = db.Column(db.String(100))  # كود المنتج
    product_description = db.Column(db.Text)  # وصف المنتج
    product_weight = db.Column(db.Float)  # الوزن (كجم)
    product_length = db.Column(db.Float)  # الطول (متر)

    # Dates
    order_date = db.Column(db.Date, nullable=False, default=datetime.utcnow)  # تاريخ الامر
    start_date = db.Column(db.Date)  # تاريخ بدء الانتاج
    expected_end_date = db.Column(db.Date)  # تاريخ الانتهاء المتوقع
    actual_end_date = db.Column(db.Date)  # تاريخ الانتهاء الفعلي

    # Status
    status = db.Column(db.String(20), default='pending')  # pending, in_progress, completed, cancelled
    priority = db.Column(db.String(20), default='normal')  # low, normal, high, urgent

    # Notes
    notes = db.Column(db.Text)
    specifications = db.Column(db.Text)  # مواصفات خاصة

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships (use string references for forward references)
    created_by = db.relationship('User', backref='created_orders')
    pipes = db.relationship('Pipe', backref='production_order', lazy='dynamic')
    chemical_analyses = db.relationship('ChemicalAnalysis', backref='production_order', lazy='dynamic')

    # Status choices
    STATUS_CHOICES = [
        ('pending', 'قيد الانتظار'),
        ('in_progress', 'جاري التنفيذ'),
        ('completed', 'مكتمل'),
        ('cancelled', 'ملغي')
    ]

    PRIORITY_CHOICES = [
        ('low', 'منخفض'),
        ('normal', 'عادي'),
        ('high', 'عالي'),
        ('urgent', 'عاجل')
    ]

    @property
    def produced_quantity(self):
        """Get count of pipes produced for this order"""
        return self.pipes.count()

    @property
    def completed_quantity(self):
        """Get count of pipes that passed all stages"""
        count = 0
        for pipe in self.pipes:
            finish_stage = pipe.get_stage('Finish')
            if finish_stage and finish_stage.decision == 'ACCEPT':
                count += 1
        return count

    @property
    def rejected_quantity(self):
        """Get count of rejected pipes"""
        count = 0
        for pipe in self.pipes:
            # Check if any stage has REJECT decision
            for stage_name in pipe.STAGES:
                stage = pipe.get_stage(stage_name)
                if stage and stage.decision == 'REJECT':
                    count += 1
                    break
        return count

    @property
    def progress_percentage(self):
        """Calculate production progress percentage"""
        if self.target_quantity == 0:
            return 0
        return min(100, int((self.completed_quantity / self.target_quantity) * 100))

    @property
    def is_completed(self):
        """Check if order is completed"""
        return self.completed_quantity >= self.target_quantity

    def generate_order_number(self):
        """Generate order number: PO-YYYYMMDD-XXX"""
        today = datetime.utcnow()
        prefix = f"PO-{today.strftime('%Y%m%d')}"

        # Find last order with same prefix
        last_order = ProductionOrder.query.filter(
            ProductionOrder.order_number.like(f"{prefix}-%")
        ).order_by(ProductionOrder.id.desc()).first()

        if last_order:
            # Extract sequence number and increment
            try:
                seq = int(last_order.order_number.split('-')[-1])
                seq += 1
            except:
                seq = 1
        else:
            seq = 1

        return f"{prefix}-{seq:03d}"

    def __repr__(self):
        return f'<ProductionOrder {self.order_number}>'
