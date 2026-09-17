"""
Customer certificates issued against a production order.

Three forms so far, and they share almost everything: the customer, the chosen
pipes, and the two references that live only on the customer's own paperwork
(their order number and the project name). So one table with a type, rather than
a table per form.

* ``warranty``  — QC-01-F-28, adds a warranty start date and a period
* ``work_test`` — QC-01-F-26, adds the chemical and mechanical results
* ``mtc``       — the material test certificate, one row per heat, printed with
  either the standard limits or the ladle's own measured values

The certificate number is a year-scoped serial issued once, at save time, so a
reprint always carries the same number.
"""
from datetime import datetime

from app import db


# Pipes chosen for a certificate. A pipe can appear on more than one certificate
# — a reissue, a split shipment, or a warranty and a work test for the same
# delivery — so this is a plain many-to-many.
certificate_pipes = db.Table(
    'certificate_pipes',
    db.Column('certificate_id', db.Integer,
              db.ForeignKey('certificates.id', ondelete='CASCADE'),
              primary_key=True),
    db.Column('pipe_id', db.Integer,
              db.ForeignKey('pipes.id', ondelete='CASCADE'),
              primary_key=True),
)

# Orders a certificate covers. `production_order_id` stays the primary order —
# everything downstream (print context, the list page, certificate_service)
# still reads it — but a delivery can draw pipes from more than one order, so
# the actual coverage is this table, derived from whichever pipes got ticked.
certificate_orders = db.Table(
    'certificate_orders',
    db.Column('certificate_id', db.Integer,
              db.ForeignKey('certificates.id', ondelete='CASCADE'),
              primary_key=True),
    db.Column('production_order_id', db.Integer,
              db.ForeignKey('production_orders.id', ondelete='CASCADE'),
              primary_key=True),
)


class CertificatePipeClaim(db.Model):
    """Exclusive ordinary-certificate claim for one pipe and form type.

    The presentation link in ``certificate_pipes`` intentionally permits
    reissues.  This separate row is the database-enforced invariant that two
    ordinary certificates of the same type cannot race each other onto the
    same pipe.
    """
    __tablename__ = 'certificate_pipe_claims'
    __table_args__ = (
        db.UniqueConstraint(
            'pipe_id', 'cert_type',
            name='uq_certificate_pipe_claims_pipe_type'),
        db.UniqueConstraint(
            'certificate_id', 'pipe_id',
            name='uq_certificate_pipe_claims_certificate_pipe'),
    )

    id = db.Column(db.Integer, primary_key=True)
    certificate_id = db.Column(
        db.Integer, db.ForeignKey('certificates.id', ondelete='CASCADE'),
        nullable=False, index=True)
    pipe_id = db.Column(
        db.Integer, db.ForeignKey('pipes.id', ondelete='CASCADE'),
        nullable=False, index=True)
    cert_type = db.Column(db.String(20), nullable=False)

    certificate = db.relationship('Certificate', back_populates='pipe_claims')
    pipe = db.relationship('Pipe')


class Certificate(db.Model):
    __tablename__ = 'certificates'

    WARRANTY = 'warranty'
    WORK_TEST = 'work_test'
    MTC = 'mtc'

    # How the MTC prints its chemical figures. The same delivery goes out one
    # way or the other depending on what the customer asked for, and both are
    # equally approved — so it is a choice on the certificate, not a setting.
    MODE_STANDARD = 'standard'
    MODE_ACTUAL = 'actual'
    VALUE_MODES = {
        MODE_STANDARD: ('Standard limits', 'الحدود القياسية'),
        MODE_ACTUAL: ('Actual results', 'النتائج الفعلية'),
    }

    # Serial prefix and display name per form. The prefix is part of every
    # issued number, so it must never change once certificates exist.
    TYPES = {
        WARRANTY: {
            'prefix': 'WC',
            'form_code': 'QC-01-F-28',
            'title_en': 'Warranty Certificate',
            'title_ar': 'شهادة ضمان',
            'template': 'certificates/warranty_print.html',
        },
        WORK_TEST: {
            'prefix': 'WT',
            'form_code': 'QC-01-F-26',
            'title_en': 'Work Test Certificate',
            'title_ar': 'شهادة اختبار',
            'template': 'certificates/work_test_print.html',
        },
        MTC: {
            'prefix': 'MTC',
            'form_code': 'LAB-DOC-8.1.1',
            'title_en': 'Material Test Certificate',
            'title_ar': 'شهادة فحص المواد',
            'template': 'certificates/mtc_print.html',
        },
    }

    # The Arabic warranty clause takes its period as words, and the English one
    # has to say the same thing.
    PERIOD_WORDS = {
        1: ('one year', 'سنة'),
        2: ('two years', 'سنتين'),
        3: ('three years', 'ثلاث سنوات'),
    }

    id = db.Column(db.Integer, primary_key=True)
    certificate_no = db.Column(db.String(30), unique=True, nullable=False, index=True)
    cert_type = db.Column(db.String(20), nullable=False, default=WARRANTY, index=True)

    production_order_id = db.Column(
        db.Integer, db.ForeignKey('production_orders.id'), nullable=False, index=True)

    issue_date = db.Column(db.Date, nullable=False)

    # Warranty only: the cover runs from this date, not from the issue date.
    warranty_start_date = db.Column(db.Date)
    warranty_years = db.Column(db.Integer, nullable=False, default=3)

    # MTC only: whether the chemical figures print as the standard limits or as
    # what the ladle actually measured.
    values_mode = db.Column(db.String(20), nullable=False, default=MODE_STANDARD)

    # MTC only in the UI, but intentionally safe to persist on every type so
    # edits and reprints reproduce the originally-issued document exactly.
    show_pipe_numbers = db.Column(db.Boolean, nullable=False, default=False)

    # Exceptional replacement document. Ordinary edits/reprints keep using the
    # original row; a reissue is a new serial with an explicit audit trail.
    reissue_of_id = db.Column(
        db.Integer,
        db.ForeignKey('certificates.id', ondelete='RESTRICT'),
        nullable=True, index=True)
    reissue_reason = db.Column(db.Text)

    # The quantity in metres. Computed from the ticked pipes' Finish lengths
    # and shown on the form for confirmation; NULL prints the computed sum, a
    # value prints as-is — the client corrects it when the tape says otherwise.
    total_length_m = db.Column(db.Float)

    # Typed in: these are the customer's references, not ours.
    customer_order_no = db.Column(db.String(100))
    project_name = db.Column(db.String(200))
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    production_order = db.relationship('ProductionOrder', backref='certificates')
    created_by = db.relationship('User', backref='certificates')
    pipes = db.relationship('Pipe', secondary=certificate_pipes,
                            lazy='selectin', backref='certificates')
    reissue_of = db.relationship(
        'Certificate', remote_side=[id], foreign_keys=[reissue_of_id],
        backref=db.backref(
            'reissues', lazy='selectin', passive_deletes='all'),
    )
    pipe_claims = db.relationship(
        'CertificatePipeClaim', back_populates='certificate',
        cascade='all, delete-orphan', passive_deletes=True,
    )

    # The link table, in appearance order. Certificates issued before this
    # feature — and any created directly without going through `orders =`
    # below — have no rows here, so `orders` falls back to the single primary
    # order rather than reporting an empty list.
    _linked_orders = db.relationship('ProductionOrder', secondary=certificate_orders,
                                     lazy='selectin', backref='certificate_links',
                                     order_by='ProductionOrder.id')

    def __repr__(self):
        return f'<Certificate {self.certificate_no}>'

    @property
    def orders(self):
        if self._linked_orders:
            return list(self._linked_orders)
        return [self.production_order] if self.production_order else []

    @orders.setter
    def orders(self, value):
        self._linked_orders = list(value)

    @classmethod
    def spec(cls, cert_type):
        return cls.TYPES.get(cert_type) or cls.TYPES[cls.WARRANTY]

    @classmethod
    def generate_certificate_no(cls, cert_type=WARRANTY, when=None):
        """Next serial for the year and form: ``WC-2026-001``.

        Certificates are numbered per year, not per day — a daily reset would
        make the number meaningless on a document that outlives the shift. Each
        form counts separately, so the two never collide.
        """
        when = when or datetime.utcnow().date()
        prefix = f"{cls.spec(cert_type)['prefix']}-{when.year}"
        last = (cls.query
                .filter(cls.certificate_no.like(f'{prefix}-%'))
                .order_by(cls.id.desc())
                .first())
        serial = 1
        if last:
            try:
                serial = int(last.certificate_no.rsplit('-', 1)[1]) + 1
            except (IndexError, ValueError):
                serial = cls.query.filter(
                    cls.certificate_no.like(f'{prefix}-%')).count() + 1
        return f'{prefix}-{serial:03d}'

    @property
    def quantity(self):
        return len(self.pipes)

    def effective_total_length(self, rows):
        """The metres the certificate prints: the confirmed figure when there
        is one, else the sum over the material rows."""
        if self.total_length_m is not None:
            return round(self.total_length_m, 2)
        from app.services.certificate_service import total_metres
        return total_metres(rows)

    @property
    def is_warranty(self):
        return self.cert_type == self.WARRANTY

    @property
    def is_mtc(self):
        return self.cert_type == self.MTC

    @property
    def shows_actuals(self):
        return self.values_mode == self.MODE_ACTUAL

    @property
    def form_code(self):
        return self.spec(self.cert_type)['form_code']

    @property
    def title_en(self):
        return self.spec(self.cert_type)['title_en']

    @property
    def title_ar(self):
        return self.spec(self.cert_type)['title_ar']

    @property
    def template(self):
        return self.spec(self.cert_type)['template']

    def period_label(self, arabic=False):
        words = self.PERIOD_WORDS.get(self.warranty_years)
        if words:
            return words[1] if arabic else words[0]
        n = self.warranty_years or 0
        return f'{n} سنوات' if arabic else f'{n} years'
