"""
Production Order Model - امر انتاج / امر شغل
Tracks batches of pipes for production
"""
from datetime import datetime

from sqlalchemy import event

from app import db


# The Application block — standards plus the three thicknesses — is shared with
# the Product now, so its constants and parsing live in the service. Re-exported
# here because screens and tests have imported the name from the model since the
# block was added.
from app.services.application_spec_service import (  # noqa: F401
    APPLICATION_STANDARDS,
    standard_labels as _standard_labels,
)


class ProductionOrder(db.Model):
    """Production Order - امر الانتاج"""
    __tablename__ = 'production_orders'

    id = db.Column(db.Integer, primary_key=True)

    # Order Identification
    order_number = db.Column(db.String(50), unique=True, nullable=False, index=True)  # رقم امر الانتاج

    # Customer Info
    customer_name = db.Column(db.String(200))  # اسم العميل (legacy free-text)
    customer_code = db.Column(db.String(50))   # legacy free-text
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), index=True)

    # Product reference
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), index=True)

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

    # Application block off the order sheet — the standard the run is built to
    # plus the three thicknesses it must hold. Copied off the product when one
    # is picked, then editable per order. Shape:
    # {"standards": {"iso_8179": bool, "en_598": bool,
    #                 "iso_2531": bool, "en_545": bool, "awwa": bool},
    #  "layers": {"thickness"|"cement"|"coating":
    #             {"value", "tolerance_plus", "tolerance_minus",
    #              "min", "nominal", "max"}}}
    # Orders written before 2026-08-27 carry a single "tolerance" key;
    # application_spec_service.normalise() reads it into both sides.
    application_profile = db.Column(db.JSON)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    modified_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Relationships (use string references for forward references)
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_orders')
    modified_by = db.relationship('User', foreign_keys=[modified_by_id], backref='modified_orders')
    pipes = db.relationship('Pipe', backref='production_order', lazy='dynamic')
    chemical_analyses = db.relationship('ChemicalAnalysis', backref='production_order', lazy='dynamic')
    product = db.relationship('Product', backref='production_orders')

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
        """Count of pipes produced for this order, memoised per instance.

        The orders list renders ``progress_percentage`` four times per row and
        ``produced_quantity`` once, and every one of those used to issue its own
        ``SELECT count(*)`` — five identical queries per order, 100 on a single
        page of twenty. The count is read-only within a render, so computing it
        once per object is enough.

        The memo lives for the life of the instance. A request that adds pipes
        and then re-reads the count on the same object must refresh it (or read
        a fresh one), which no current caller does.
        """
        cached = self.__dict__.get("_produced_quantity_memo")
        if cached is None:
            cached = self.pipes.count()
            self.__dict__["_produced_quantity_memo"] = cached
        return cached

    @property
    def completed_quantity(self):
        """Get count of pipes that passed all stages.

        get_stage() now reads from a per-pipe map loaded in one query, so the
        loop below costs one SELECT per pipe rather than one per pipe per stage
        name. Memoised for the same reason as produced_quantity: the order
        detail template reads these repeatedly while rendering.
        """
        cached = self.__dict__.get("_completed_quantity_memo")
        if cached is not None:
            return cached

        from app.models.pipe import PipeStage
        from app.models.stage import ProductionStage
        finish_name = ProductionStage.name_for_code("finish")
        count = 0
        for pipe in self.pipes:
            if pipe.final_decision_value == 'ACCEPT':
                count += 1
                continue
            finish_stage = pipe.get_stage(finish_name)
            if finish_stage and PipeStage.classify_decision(finish_stage.decision) == 'accept':
                count += 1
        self.__dict__["_completed_quantity_memo"] = count
        return count

    @property
    def rejected_quantity(self):
        """Get count of rejected pipes (each pipe counted at most once)."""
        cached = self.__dict__.get("_rejected_quantity_memo")
        if cached is not None:
            return cached

        from app.models.pipe import PipeStage
        from app.models.stage import ProductionStage
        active_stage_names = ProductionStage.active_names()
        count = 0
        for pipe in self.pipes:
            if pipe.final_decision_value == 'REJECT':
                count += 1
                continue
            if pipe.final_decision_value == 'ACCEPT':
                continue
            for stage_name in active_stage_names:
                stage = pipe.get_stage(stage_name)
                if stage and PipeStage.classify_decision(stage.decision) == 'reject':
                    count += 1
                    break
        self.__dict__["_rejected_quantity_memo"] = count
        return count

    @property
    def progress_percentage(self):
        """Calculate production progress percentage"""
        if self.target_quantity == 0:
            return 0
        return min(100, int((self.produced_quantity / self.target_quantity) * 100))

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

        # The last row by id is not always the highest sequence (numbers can be
        # typed by hand), so walk forward until the number is actually free.
        while db.session.query(
            ProductionOrder.query.filter_by(
                order_number=f"{prefix}-{seq:03d}").exists()
        ).scalar():
            seq += 1

        return f"{prefix}-{seq:03d}"

    def standard_labels(self):
        """The Application standards ticked on this order, as display labels.

        Returns them in sheet order (["ISO 8179", "EN 545", ...]), empty when
        the order predates the Application block or nothing was ticked. Stage
        screens use this to show the operator which standard the run is built
        to without having to open the order.
        """
        return _standard_labels(self.effective_application())

    def effective_application(self):
        """The spec this order actually runs to.

        The figures are authored on the product, so they are read from there
        and the order only says which standard it was built to. An order saved
        before the per-standard tables existed carries no figures of its own
        and would otherwise hand the stage screens an empty band.
        """
        from app.services.application_spec_service import for_order
        return for_order(
            self.application_profile,
            self.product.application_profile if self.product else None,
            group=self.product.application_group() if self.product else None,
        )

    def __repr__(self):
        return f'<ProductionOrder {self.order_number}>'


@event.listens_for(db.session, "after_flush")
def _drop_order_quantity_memos(session, flush_context):
    """Drop the cached quantity counts when a pipe or stage row is written.

    All three quantity properties are derived from the order's pipes, so adding
    a pipe or deciding a stage changes them. Without this, a request that
    creates a pipe and then re-renders the order would show the count from
    before the write.
    """
    from app.models.pipe import Pipe, PipeStage

    if not any(
        isinstance(obj, (Pipe, PipeStage))
        for obj in (session.new | session.dirty | session.deleted)
    ):
        return
    for obj in list(session.identity_map.values()):
        if isinstance(obj, ProductionOrder):
            obj.__dict__.pop("_produced_quantity_memo", None)
            obj.__dict__.pop("_completed_quantity_memo", None)
            obj.__dict__.pop("_rejected_quantity_memo", None)
