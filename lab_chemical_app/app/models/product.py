"""
Product Configurator Models - ProductParameter, Product, Customer, Mold
"""
import json
import os
from datetime import datetime
from app import db

APP_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app_settings.json"
)


def _load_param_order_override():
    """Return the user-configured param-type order from app_settings.json, or None."""
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        order = data.get("product_code", {}).get("param_order")
        if isinstance(order, list) and order:
            return order
    except Exception:
        pass
    return None


class ProductParameter(db.Model):
    """Configurable product parameters (CLASS, DN, ZINC_TYPE, etc.)"""
    __tablename__ = 'product_parameters'

    id = db.Column(db.Integer, primary_key=True)
    param_type = db.Column(db.String(30), nullable=False, index=True)
    # Types: CLASS, DN, ZINC_TYPE, LENGTH, INTERNAL_FINISH, EXTERNAL_FINISH, INTENT_USE, PROJECT_TYPE
    code = db.Column(db.String(20), nullable=False)
    name_en = db.Column(db.String(100), nullable=False)
    name_ar = db.Column(db.String(100))
    description_en = db.Column(db.String(200))
    description_ar = db.Column(db.String(200))
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    # INTENT_USE only: which Application standards this intended use offers —
    # 'sewage' (ISO 8179 / EN 598), 'water' (ISO 2531 / EN 545), or 'both'.
    # AWWA is offered either way. Untagged behaves as 'both', so nothing is
    # hidden until somebody has said which standards apply.
    application_group = db.Column(db.String(10))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('param_type', 'code', name='uix_param_type_code'),
    )

    PARAM_TYPES = [
        'CLASS', 'DN', 'ZINC_TYPE', 'LENGTH', 'PCS_STAGES_PASS',
        'INTERNAL_FINISH', 'EXTERNAL_FINISH',
        'INTENT_USE', 'PROJECT_TYPE'
    ]

    PARAM_TYPE_LABELS = {
        'CLASS': ('الفئة', 'Class'),
        'DN': ('القطر الاسمي', 'DN'),
        'ZINC_TYPE': ('نوع الزنك', 'Zinc Type'),
        'LENGTH': ('الطول', 'Length'),
        'PCS_STAGES_PASS': ('# PCs/#Stages pass', '# PCs/#Stages pass'),
        'INTERNAL_FINISH': ('التشطيب الداخلي', 'Internal Finish'),
        'EXTERNAL_FINISH': ('التشطيب الخارجي', 'External Finish'),
        'INTENT_USE': ('الاستخدام المقصود', 'Intended Use'),
        'PROJECT_TYPE': ('رقم تسلسلي', 'Serial'),
    }

    def __repr__(self):
        return f'<ProductParameter {self.param_type}:{self.code}>'


class Product(db.Model):
    """Product definition built from parameters"""
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    product_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    barcode = db.Column(db.String(32))
    description_en = db.Column(db.Text)
    description_ar = db.Column(db.Text)

    # FK references to parameters
    class_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    dn_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    zinc_type_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    length_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    pcs_stages_pass_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    internal_finish_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    external_finish_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    intent_use_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))
    project_type_param_id = db.Column(db.Integer, db.ForeignKey('product_parameters.id'))

    weight_kg = db.Column(db.Float)
    length_m = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)

    # Application block — the standard this product is built to plus the wall,
    # cement-lining and coating thicknesses it must hold. The product owns the
    # specification; a production order copies it and may then edit it for a
    # customer who wants something different. Shape and parsing live in
    # app/services/application_spec_service.py.
    application_profile = db.Column(db.JSON)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    class_param = db.relationship('ProductParameter', foreign_keys=[class_param_id])
    dn_param = db.relationship('ProductParameter', foreign_keys=[dn_param_id])
    zinc_type_param = db.relationship('ProductParameter', foreign_keys=[zinc_type_param_id])
    length_param = db.relationship('ProductParameter', foreign_keys=[length_param_id])
    pcs_stages_pass_param = db.relationship('ProductParameter', foreign_keys=[pcs_stages_pass_param_id])
    internal_finish_param = db.relationship('ProductParameter', foreign_keys=[internal_finish_param_id])
    external_finish_param = db.relationship('ProductParameter', foreign_keys=[external_finish_param_id])
    intent_use_param = db.relationship('ProductParameter', foreign_keys=[intent_use_param_id])
    project_type_param = db.relationship('ProductParameter', foreign_keys=[project_type_param_id])

    # Fixed prefix for product descriptions
    DESCRIPTION_PREFIX_EN = "Ductile Iron - Centrifugally Casted Socket Spigot"
    DESCRIPTION_PREFIX_AR = "ماسورة زهر مرن رأس و ذيل - طرد مركزي"

    # Parameter order for code and description generation.
    # Format: DN + Class + Zinc + Length + InternalFinish + ExternalFinish + IntentUse + Special(00)
    # Example: 10C40Z1360LHC00 (DN=10, Class=C40, Zinc=Z1, Length=360, Internal=L, External=H, IntentUse=C, Special=00)
    # Default order used for code/description generation. Overridable via
    # app_settings.json → product_code.param_order (list of PARAM_TYPES).
    DEFAULT_PARAM_ORDER = [
        ('dn_param', 'dn_param_id', 'DN'),
        ('class_param', 'class_param_id', 'CLASS'),
        ('zinc_type_param', 'zinc_type_param_id', 'ZINC_TYPE'),
        ('length_param', 'length_param_id', 'LENGTH'),
        ('pcs_stages_pass_param', 'pcs_stages_pass_param_id', 'PCS_STAGES_PASS'),
        ('internal_finish_param', 'internal_finish_param_id', 'INTERNAL_FINISH'),
        ('external_finish_param', 'external_finish_param_id', 'EXTERNAL_FINISH'),
        ('intent_use_param', 'intent_use_param_id', 'INTENT_USE'),
        ('project_type_param', 'project_type_param_id', 'PROJECT_TYPE'),
    ]

    # Legacy alias (dn_param, dn_param_id) tuples — used by callers expecting 2-tuples.
    PARAM_ORDER = [(attr, fk) for attr, fk, _ in DEFAULT_PARAM_ORDER]

    PARAM_TYPE_TO_FIELDS = {pt: (attr, fk) for attr, fk, pt in DEFAULT_PARAM_ORDER}

    # Code widths are 0 (natural) — parameter codes render exactly as stored,
    # no zero-padding is inserted. Enter codes with the desired shape directly.
    PARAM_CODE_WIDTH = {
        'DN': 0,
        'CLASS': 0,
        'ZINC_TYPE': 0,
        'LENGTH': 0,
        'PCS_STAGES_PASS': 0,
        'INTERNAL_FINISH': 0,
        'EXTERNAL_FINISH': 0,
        'INTENT_USE': 0,
        'PROJECT_TYPE': 0,
    }

    def standard_labels(self):
        """The Application standards ticked on this product, as labels."""
        from app.services import application_spec_service
        return application_spec_service.standard_labels(self.application_profile)

    def application_group(self):
        """Sewage or water — whichever this product's Intended Use selects.

        Intended Use is the field the operator actually sets, so it is what
        decides which standards the Application block offers. The group stored
        on the profile is only a cache of that decision, written whenever the
        block was last saved, and it goes stale the moment Intended Use is
        changed on its own. Returns None when Intended Use says neither, in
        which case the stored group stands.
        """
        from app.services.application_spec_service import (
            SPEC_GROUPS, group_for_intent)
        group = group_for_intent(self.intent_use_param)
        return group if group in SPEC_GROUPS else None

    @classmethod
    def get_param_order(cls):
        """Return the active ordered list of (attr, fk_attr, param_type).

        Uses app_settings.json → product_code.param_order when set, otherwise
        falls back to DEFAULT_PARAM_ORDER. Unknown or missing types are ignored;
        any types missing from the override are appended in default order.
        """
        override = _load_param_order_override()
        if not override:
            return list(cls.DEFAULT_PARAM_ORDER)
        seen = set()
        ordered = []
        for pt in override:
            fields = cls.PARAM_TYPE_TO_FIELDS.get(pt)
            if fields and pt not in seen:
                ordered.append((fields[0], fields[1], pt))
                seen.add(pt)
        for attr, fk, pt in cls.DEFAULT_PARAM_ORDER:
            if pt not in seen:
                ordered.append((attr, fk, pt))
        return ordered

    def _resolve_params(self, ordered=None):
        """Resolve parameter relationships in the given (attr, fk, type) order."""
        if ordered is None:
            ordered = self.get_param_order()
        resolved = []
        for attr, id_field, param_type in ordered:
            param = getattr(self, attr, None)
            if not param:
                param_id = getattr(self, id_field, None)
                if param_id:
                    param = ProductParameter.query.get(param_id)
            resolved.append((param, param_type))
        return resolved

    def generate_product_code(self):
        """Auto-generate product code by concatenating padded parameter codes
        following the active (possibly user-reordered) param sequence.

        Empty slots are skipped entirely — they no longer emit zero padding.

        If the product has PROJECT_TYPE (serial), strips it from the base,
        queries existing codes with same prefix, and auto-increments to
        avoid duplicates.
        """
        parts = []
        serial_code = None
        for param, param_type in self._resolve_params():
            if not (param and param.code):
                continue
            width = self.PARAM_CODE_WIDTH.get(param_type, 0)
            code = str(param.code).strip()
            if width and len(code) < width:
                code = code.zfill(width)
            if param_type == 'PROJECT_TYPE':
                serial_code = code
            else:
                parts.append(code)

        if not parts:
            self.product_code = None
            return

        base_code = ''.join(parts)

        if not serial_code:
            # No serial — use base code directly
            self.product_code = base_code
            return

        serial_len = len(serial_code)

        # Keep existing code as-is when it already fits this base and stays
        # unique — covers both a bare base (legacy, no serial) and a valid
        # serial suffix. This prevents editing a product from retroactively
        # rewriting its stored code (e.g. bare "…LHB" → "…LHB00").
        if self.product_code and self.product_code.startswith(base_code):
            suffix = self.product_code[len(base_code):]
            if suffix == '' or (suffix.isdigit() and len(suffix) == serial_len):
                dup = type(self).query.filter(
                    type(self).product_code == self.product_code
                )
                if self.id:
                    dup = dup.filter(type(self).id != self.id)
                if not dup.first():
                    return

        # Find the max serial already used for this base. A bare base with no
        # suffix counts as serial 0 so a new same-config product continues the
        # sequence (→ 01) instead of restarting at 00 alongside the bare one.
        existing = type(self).query.filter(
            db.or_(
                type(self).product_code == base_code,
                type(self).product_code.like(f"{base_code}{'_' * serial_len}"),
            )
        ).all()

        max_serial = -1
        for p in existing:
            if self.id is not None and p.id == self.id:
                continue
            suffix = p.product_code[len(base_code):]
            if suffix == '':
                serial = 0
            elif suffix.isdigit() and len(suffix) == serial_len:
                serial = int(suffix)
            else:
                continue
            if serial > max_serial:
                max_serial = serial

        next_serial = max_serial + 1
        self.product_code = f'{base_code}{next_serial:0{serial_len}d}'

    def generate_description(self):
        """Auto-generate descriptions: fixed prefix + all parameter descriptions"""
        parts_en = []
        parts_ar = []
        for param, _ in self._resolve_params():
            if param:
                desc_en = param.description_en or param.name_en
                desc_ar = param.description_ar or param.name_ar or ''
                if desc_en:
                    parts_en.append(desc_en)
                if desc_ar:
                    parts_ar.append(desc_ar)

        if parts_en:
            self.description_en = self.DESCRIPTION_PREFIX_EN + ' ' + ' '.join(parts_en)
        else:
            self.description_en = self.DESCRIPTION_PREFIX_EN

        if parts_ar:
            self.description_ar = self.DESCRIPTION_PREFIX_AR + ' ' + ' '.join(parts_ar)
        else:
            self.description_ar = self.DESCRIPTION_PREFIX_AR

    @property
    def dn_value(self):
        """Nominal diameter as an integer (e.g. 800 for DN800).

        Derived from the DN parameter's human-readable fields, NOT its code.
        The DN ``code`` (e.g. 'P80') is a compact token used only to assemble
        the product code and does NOT encode the true nominal diameter, so
        parsing it gives wrong values (P80 -> 80 instead of 800). We read
        ``name_en`` first ('DN800', 'Pipe DN800xxxxxx', 'D100'), then fall back
        to ``description_en`` then ``code``.
        """
        import re
        if not self.dn_param:
            return None
        m = re.search(r'(?:DN|D)\s*(\d+)', str(self.dn_param.name_en or ''))
        if m:
            return int(m.group(1))
        for src in (self.dn_param.description_en, self.dn_param.code):
            m = re.search(r'(\d+)', str(src or ''))
            if m:
                return int(m.group(1))
        return None

    @property
    def dn_label(self):
        """Human-readable DN label, e.g. 'DN800'. Empty string when unknown."""
        value = self.dn_value
        return f'DN{value}' if value is not None else ''

    def __repr__(self):
        return f'<Product {self.product_code}>'


class Customer(db.Model):
    """Customer management"""
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name_en = db.Column(db.String(200), nullable=False)
    name_ar = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    production_orders = db.relationship('ProductionOrder', backref='customer_ref', lazy='dynamic')

    def __repr__(self):
        return f'<Customer {self.code}: {self.name_en}>'


class Mold(db.Model):
    """Mold reference table"""
    __tablename__ = 'molds'

    id = db.Column(db.Integer, primary_key=True)
    mold_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    diameter = db.Column(db.Integer, index=True)  # DN this mold is compatible with
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Mold {self.mold_number}>'
