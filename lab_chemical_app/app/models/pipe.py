"""
Pipe and Stage Models
"""

import re
from datetime import datetime

from sqlalchemy import event

from app import db

# A No. Code is one letter followed by exactly four digits (N8739). The field
# was free text until 2026-08-31, so historical rows may not match — validate
# on write only.
NO_CODE_PATTERN = re.compile(r"^[A-Za-z][0-9]{4}$")
NO_CODE_HINT = "must be one letter followed by four digits (e.g. N8739)"


def normalize_no_code(raw):
    """Validate and upper-case a No. Code.

    Returns ``(code, error)``: ``code`` is the normalized value when valid,
    otherwise ``None`` and ``error`` carries a user-facing message.
    """
    code = (raw or "").strip()
    if not code:
        return None, "No. Code is required."
    if not NO_CODE_PATTERN.match(code):
        return None, f"No. Code '{code}' is invalid — it {NO_CODE_HINT}."
    return code.upper(), None


class _DynamicStages:
    """Class descriptor: ``Pipe.STAGES`` always returns the live, DB-backed
    stage list (custom stages included), falling back to the built-in
    defaults when the production_stages table is unavailable.

    Keeps every legacy ``Pipe.STAGES`` call site (admin dropdowns, reports,
    analytics, exports…) working with custom stages without touching them.
    """

    def __get__(self, obj, objtype=None):
        from app.models.stage import ProductionStage

        return ProductionStage.active_names()


class Pipe(db.Model):
    """Pipes - Sheet 3 Main Production Data"""

    __tablename__ = "pipes"

    id = db.Column(db.Integer, primary_key=True)

    # Production Info
    production_date = db.Column(db.Date, nullable=False, index=True)
    shift = db.Column(db.Integer)
    shift_engineer = db.Column(db.String(100))
    manufacturing_order = db.Column(db.String(50))

    # Link to Product. The column has been here since the beginning but the
    # relationship never was, so `pipe.product` in a template resolved to
    # Jinja's Undefined and the Product Specifications card rendered blank on
    # every pipe — including the ones that do carry a product_id.
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    product = db.relationship("Product", foreign_keys=[product_id])

    # Link to Production Order (امر الانتاج)
    production_order_id = db.Column(
        db.Integer, db.ForeignKey("production_orders.id"), index=True
    )

    # Pipe Identification
    pipe_code = db.Column(db.String(50))
    diameter = db.Column(db.Integer)  # DN: 300, 500, 600
    pipe_class = db.Column(db.String(20))  # K9, C25, Fittings
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"))
    mold_number = db.Column(db.String(20))
    iso_weight = db.Column(db.Float)
    no_code = db.Column(
        db.String(50), index=True
    )  # N8739, N8740... (batch identifier, not unique)
    arrange_pipe = db.Column(db.Integer)  # Sequence 1-6
    # The number the warehouse system knows this pipe by. Typed or scanned in
    # at the Finish stage, printed as the second barcode on the sticker, and
    # the key the storekeeper scans to reach the receiving screen. Unique and
    # indexed because it is a scan key, not a label: pipe_code is neither, and
    # that is exactly why it cannot serve as one.
    warehouse_barcode = db.Column(db.String(32), unique=True, index=True)

    # Link to Chemical Analysis
    ladle_id = db.Column(db.String(20), db.ForeignKey("chemical_analyses.ladle_id"))

    # Measurements
    thickness = db.Column(db.Float)  # Socket Thickness 1
    thickness_socket_2 = db.Column(db.Float)
    thickness_spigot = db.Column(db.Float)  # Spigot Thickness 1
    thickness_spigot_2 = db.Column(db.Float)
    actual_weight = db.Column(db.Float)

    # Decision Engine Fields
    mechanical_test_role = db.Column(db.String(20))  # FIRST, LAST, ANY, ALL, null
    lab_decision = db.Column(db.String(20))  # WAITING, ACCEPT, REJECT, HOLD, BLOCKED(terminal تالف), FROZEN(recoverable mechanical freeze)
    lab_decision_reason = db.Column(db.Text)
    cascade_from = db.Column(
        db.String(100)
    )  # Track which pipe/sample the decision cascaded from
    final_decision_value = db.Column(
        db.String(20), index=True
    )  # Real DB column for final decision
    final_decision_reason = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    modified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    machine = db.relationship("Machine", back_populates="pipes")
    created_by = db.relationship(
        "User", foreign_keys=[created_by_id], backref="created_pipes"
    )
    modified_by = db.relationship(
        "User", foreign_keys=[modified_by_id], backref="modified_pipes"
    )
    # chemical_analysis and production_order relationships defined via backref
    stages = db.relationship(
        "PipeStage", back_populates="pipe", lazy="dynamic", cascade="all, delete-orphan"
    )
    mechanical_tests = db.relationship("MechanicalTest", backref="pipe", lazy="dynamic")

    # Production stages — dynamic via _DynamicStages (DB-backed, includes
    # custom stages from Stage Management; falls back to these defaults).
    STAGES = _DynamicStages()
    DEFAULT_STAGES = [
        "Melting Ladle",
        "CCM",
        "Annealing",
        "Lab Approval",
        "Zinc",
        "Cutting",
        "Hydrotest",
        "Cement",
        "Coating",
        "Finish",
        "Delivery",
    ]

    def _stage_map(self):
        """All of this pipe's stage rows, keyed by stage name, loaded once.

        ``stages`` is a dynamic relationship, so it never loads eagerly and
        every ``get_stage`` used to be its own SELECT. Callers ask per stage
        name in a loop — ``get_all_stages_status`` walks all ~13 active stages —
        so a single pipe cost 13 queries and a list of pipes multiplied that.

        The memo is dropped on every flush that touches a PipeStage (see
        ``_drop_pipe_stage_memo`` at the bottom of this module), so a write path
        that saves a stage and then re-reads it sees the new value.
        """
        cached = self.__dict__.get("_stage_map_memo")
        if cached is None:
            cached = {}
            for row in self.stages.all():
                # Keep the first row per name, matching the previous
                # .filter_by(...).first() behaviour when duplicates exist.
                cached.setdefault(row.stage_name, row)
            self.__dict__["_stage_map_memo"] = cached
        return cached

    def effective_product(self):
        """The product this pipe is made to.

        Most pipes carry no product_id of their own — 49 of the 64 on prod —
        because the product is chosen on the production order and the pipe is
        registered against the order. So the order's product answers for them,
        and a product set directly on the pipe overrides it.
        """
        if self.product is not None:
            return self.product
        order = self.production_order
        return order.product if order is not None else None

    def get_stage(self, stage_name):
        """Get specific stage data"""
        return self._stage_map().get(stage_name)

    @classmethod
    def prefill_stage_maps(cls, pipes):
        """Fill the per-pipe stage memo for a whole list in one query.

        `stages` is a dynamic relationship, so it cannot be eager-loaded and
        every pipe otherwise fetches its own rows the first time something
        asks where it is standing — a list of 500 pipes asking that becomes
        500 queries. The machines come along for the ride: naming the stage
        a pipe is standing at usually means naming its machine too.
        """
        from sqlalchemy.orm import joinedload

        by_pipe = {pipe.id: {} for pipe in pipes if pipe.id is not None}
        if not by_pipe:
            return
        rows = (
            PipeStage.query.options(joinedload(PipeStage.machine))
            .filter(PipeStage.pipe_id.in_(by_pipe))
            .all()
        )
        for row in rows:
            # First row per name, matching _stage_map()'s own rule.
            by_pipe[row.pipe_id].setdefault(row.stage_name, row)
        for pipe in pipes:
            if pipe.id in by_pipe:
                pipe.__dict__["_stage_map_memo"] = by_pipe[pipe.id]

    def get_all_stages_status(self):
        """Get status of all stages"""
        from app.models.stage import ProductionStage
        stages_status = {}
        for stage_name in ProductionStage.active_names():
            stage = self.get_stage(stage_name)
            if stage:
                stages_status[stage_name] = {
                    "decision": stage.decision,
                    "has_defect": stage.has_defect,
                    "completed": True,
                }
            else:
                stages_status[stage_name] = {
                    "decision": None,
                    "has_defect": False,
                    "completed": False,
                }
        return stages_status

    @property
    def current_stage(self):
        """Get the current/latest stage"""
        from app.models.stage import ProductionStage
        active = ProductionStage.active_names()
        for stage_name in reversed(active):
            stage = self.get_stage(stage_name)
            if stage and stage.decision:
                return stage_name
        return active[0] if active else self.STAGES[0]  # Default to first stage

    @property
    def final_decision(self):
        """Get final product decision - prefer stored value, fallback to Finish stage"""
        if self.final_decision_value:
            return self.final_decision_value
        from app.models.stage import ProductionStage
        finish_name = ProductionStage.name_for_code("finish")
        finish_stage = self.get_stage(finish_name)
        return finish_stage.decision if finish_stage else None

    def __repr__(self):
        return f"<Pipe {self.no_code}>"


class PipeStage(db.Model):
    """Pipe Stages - 8 stages per pipe"""

    __tablename__ = "pipe_stages"

    id = db.Column(db.Integer, primary_key=True)
    pipe_id = db.Column(db.Integer, db.ForeignKey("pipes.id"), nullable=False)
    stage_name = db.Column(db.String(50), nullable=False)

    # Stage timestamps
    stage_date = db.Column(db.Date)
    stage_time = db.Column(db.Time)

    # Stage measurements
    measurement_value = db.Column(db.Float)  # Zinc Slide, Cement thick, etc.
    measurement_type = db.Column(db.String(50))

    # Quality Control
    decision = db.Column(db.String(100))
    reason = db.Column(db.Text)
    has_defect = db.Column(db.Boolean, default=False)
    defect_type_id = db.Column(db.Integer, db.ForeignKey("defect_types.id"))
    defect_type = db.Column(db.String(100))  # Stage-specific defect type
    defect_reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Machine used for this stage (for production stages like Zinc, Cutting, Hydrotest, Cement, Coating)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"))

    # Additional fields
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    thickness_socket = db.Column(db.Float)  # Dual thickness for CCM
    thickness_spigot = db.Column(db.Float)  # Dual thickness for CCM
    temperature = db.Column(db.Float)  # Casting temperature (°C) for CCM
    # Per-meter thickness profile for Coating stage — one entry per meter of
    # the 6m pipe, plus the Min / Nominal / Max band the readings are judged
    # against (seeded from the order's Application spec):
    # {"cement": [m1..m6], "coating": [m1..m6],
    #  "<layer>_std", "<layer>_std_min", "<layer>_std_max": float or None}
    thickness_profile = db.Column(db.JSON)
    # Dimension measurements for CCM stage:
    # {"thickness": {"positions": {"1".."6": [r1, r2, r3]},
    #                "standard", "standard_min", "standard_max": float or None},
    #  "diameter":  {"samples": {"S1": [D1..D15], ...}},
    #  "ovality":   {"points": {"<symbol>": {"x", "y", "ovality"}}},
    #  "symbols":   {"d1": 326.4, ...}}
    # The legacy flat shape {"thickness": {"samples": {"S1": [1..21]}}} is
    # still readable — measurement_stats_service.thickness_matrix folds it.
    dimension_profile = db.Column(db.JSON)
    # Ovality measurements on the Annealing stage:
    # {"points": {"ID"|"D1".."D15": {"x", "y", "ovality"}}} — ovality % is
    # (x - y) / (x + y) * 100, computed once at save time (DrAlaa 2026-08-14).
    ovality_profile = db.Column(db.JSON)
    # Visual inspection checklist on the Finish stage:
    # {"marking": bool, "ovality": bool, "straightness": bool,
    #  "internal_finish": bool, "external_finish": bool}
    visual_profile = db.Column(db.JSON)
    # Zinc coating-mass sheet on the Zinc stage:
    # {"m1", "m2", "area", "c", "mass"} — sample weight before and
    # after stripping (g), the stripped area (m2), the sheet's C factor,
    # and M = C(m2 - m1) / area in g/m2, computed once at save time.
    zinc_profile = db.Column(db.JSON)
    # Ring deflection test on the Cutting stage:
    # {"force", "od_initial", "od_final", "od_diff", "deflection"} — the
    # applied force (kN), the outside diameter before and after squeezing
    # (mm), their difference and the deflection %, both computed at save time.
    ring_profile = db.Column(db.JSON)
    shift_responsible = db.Column(db.String(100))  # Shift engineer/responsible

    # Delivery stage fields
    sales_order = db.Column(db.String(100))
    delivery_customer = db.Column(db.String(200))
    delivery_receipt = db.Column(db.String(100))
    delivery_date = db.Column(db.Date)
    bundle_number = db.Column(db.String(50))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    pipe = db.relationship("Pipe", back_populates="stages")
    defect_type_obj = db.relationship(
        "DefectType", back_populates="pipe_stages", foreign_keys=[defect_type_id]
    )
    machine = db.relationship("Machine", backref="pipe_stages")
    approved_by = db.relationship(
        "User", foreign_keys=[approved_by_id], backref="approved_pipe_stages"
    )
    updated_by = db.relationship(
        "User", foreign_keys=[updated_by_id], backref="updated_pipe_stages"
    )

    # Unique constraint
    __table_args__ = (
        db.UniqueConstraint("pipe_id", "stage_name", name="uix_pipe_stage"),
    )

    # Stage-specific measurement types
    MEASUREMENT_TYPES = {
        "Zinc": "Zinc Slide",
        "Cement": "Cement Thickness",
        "Coating": "Coating Thickness",
        "Finish": "Length",
    }

    # Unit and hint for the stage grid's generic measurement box, keyed by
    # BUILT-IN STAGE CODE (not display name) so renaming a stage under Stage
    # Management does not silently strip its unit. CCM (°C) and Finish (m)
    # render their own inputs and are deliberately absent here.
    #
    # A stage with no entry keeps the plain unlabelled box it has always had.
    MEASUREMENT_UNITS = {
        "zinc": ("µm", "Zinc coating thickness", "سماكة طلاء الزنك"),
        "hydrotest": ("bar", "Test pressure", "ضغط الاختبار"),
        "cement": ("mm", "Cement lining thickness", "سماكة البطانة الأسمنتية"),
        "coating": ("µm", "External coating thickness", "سماكة الطلاء الخارجي"),
        "cutting": ("mm", "Cut length", "طول القص"),
    }

    @classmethod
    def measurement_units_by_stage(cls):
        """``{current_stage_name: (unit, label_en, label_ar)}``.

        Resolves MEASUREMENT_UNITS through the live stage table so the unit
        follows a renamed stage. Returns ``{}`` (never raises) if the stage
        table is unavailable, which just means no units are shown.
        """
        try:
            from app.models.stage import ProductionStage

            return {
                ProductionStage.name_for_code(code): spec
                for code, spec in cls.MEASUREMENT_UNITS.items()
            }
        except Exception:
            return {}

    # Stage-specific decisions (Melting Ladle, CCM, Annealing, Lab, Zinc, Cutting, Hydrotest, Cement, Coating, Finish)
    STAGE_DECISIONS = {
        "Melting Ladle": [
            ("Inspect Last pipes", "فحص أخيرة فقط"),
            ("Inspect 1st and Last pipes", "فحص أولى وأخيرة"),
            ("Inspect 100%", "فحص الشحنة 100%"),
            ("Reject", "تالف"),
        ],
        "CCM": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Accept with remark", "قبول مع ملاحظة"),
            ("DownGrade", "تخفيض درجة"),
            ("Hold", "حجز"),
        ],
        "Annealing": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Accept with remark", "قبول مع ملاحظة"),
            ("Hold", "حجز"),
            ("Reheat treatment", "إعادة معالجة حرارية"),
            ("Resample", "إعادة عينة"),
        ],
        # The built-in "lab" stage (displayed as "Lab Approval") — the single
        # supervisor lab decision stage between Annealing and Zinc.
        "Lab Approval": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Accept with remark", "قبول مع ملاحظة"),
            ("Hold", "حجز"),
            ("Reheat treatment", "إعادة معالجة حرارية"),
            ("Resample", "إعادة عينة"),
        ],
        "Zinc": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Hold", "حجز"),
            ("Rework", "إعادة عمل"),
            ("Micro-structure", "فحص البنية المجهرية"),
        ],
        "Cutting": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Hold", "حجز"),
            ("Retest", "إعادة اختبار"),
        ],
        "Hydrotest": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Hold", "حجز"),
            ("Rework", "إعادة عمل"),
        ],
        "Cement": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Hold", "حجز"),
            ("Rework", "إعادة عمل"),
        ],
        "Coating": [
            ("Accept", "قبول"),
            ("Reject", "رفض"),
            ("Hold", "حجز"),
            ("Rework", "إعادة عمل"),
        ],
        "Finish": [("Accept", "قبول"), ("Reject", "رفض"), ("Hold", "حجز")],
        "Delivery": [
            ("Delivered", "تم التسليم"),
            ("Pending", "قيد الانتظار"),
            ("Returned", "مرتجع"),
        ],
    }

    # Stage-specific defects
    STAGE_DEFECTS = {
        "Melting Ladle": [
            ("Out of specification", "خارج المواصفات"),
            ("دلاليـك", "Dalek"),
            ("رينـــكل", "Wrinkle"),
            ("تكتل معدن/عتبة", "Metal clustering/threshold"),
            ("طى معدن", "Metal folding"),
            ("شروخ", "Cracks"),
            ("حفر", "Pits"),
            ("توريق LA", "LA Flaking"),
            ("سمك عالى", "High thickness"),
            ("سمك ضعيف", "Low thickness"),
            ("جرافيت", "Graphite"),
            ("جلخ/خبط SL", "Grinding/slag"),
            ("تطعيم", "Inoculation"),
            ("كسر فى الرأس", "Head break"),
            ("بدون مصد", "Without socket"),
            ("D4", "D4"),
            ("Short pipe", "أنبوب قصير"),
            ("تحليل شحنة", "Batch analysis"),
            ("بيضاوى", "Oval"),
            ("تقوس CU", "Curvature"),
            ("عيب مناولة", "Handling defect"),
            ("Other", "أخرى"),
        ],
        "CCM": [
            ("دلاليـك", "Dalek"),
            ("رينـــكل", "Wrinkle"),
            ("تكتل معدن/عتبة", "Metal clustering/threshold"),
            ("طى معدن", "Metal folding"),
            ("شروخ", "Cracks"),
            ("حفر", "Pits"),
            ("توريق LA", "LA Flaking"),
            ("سمك عالى", "High thickness"),
            ("سمك ضعيف", "Low thickness"),
            ("جرافيت", "Graphite"),
            ("جلخ/خبط SL", "Grinding/slag"),
            ("تطعيم", "Inoculation"),
            ("كسر فى الرأس", "Head break"),
            ("بدون مصد", "Without socket"),
            ("D4", "D4"),
            ("بيضاوى", "Oval"),
            ("تقوس CU", "Curvature"),
            ("عيب مناولة", "Handling defect"),
            ("Other", "أخرى"),
        ],
        "Annealing": [
            ("مناوله", "Handling"),
            ("شرخ", "Crack"),
            ("بيضاوى", "Oval"),
            ("Other", "أخرى"),
        ],
        "Lab Approval": [
            ("زهر رمادى", "Gray cast"),
            ("خواص ميكانيكيه", "Mechanical properties"),
            ("رينج تيست", "Ring test"),
            ("Other", "أخرى"),
        ],
        "Zinc": [],  # No specific defects listed
        "Cutting": [
            ("مناوله", "Handling"),
            ("خواص ميكانيكيه", "Mechanical properties"),
            ("انتفاخ", "Bulging"),
            ("كسر على المكبس", "Break on press"),
            ("قصر طول", "Short length"),
            ("شروخ", "Cracks"),
            ("Other", "أخرى"),
        ],
        "Hydrotest": [("مناوله", "Handling"), ("تسريب", "Leakage"), ("Other", "أخرى")],
        "Cement": [
            ("سمك", "Thickness"),
            ("تالف دهان", "Paint damage"),
            ("تالف اسمنت", "Cement damage"),
            ("Other", "أخرى"),
        ],
        "Coating": [
            ("مناوله", "Handling"),
            ("تالف دهان", "Paint damage"),
            ("تالف اسمنت", "Cement damage"),
            ("Other", "أخرى"),
        ],
        "Finish": [("مناوله", "Handling"), ("Other", "أخرى")],
        "Delivery": [("تالف نقل", "Transport damage"), ("Other", "أخرى")],
    }

    @classmethod
    def get_decisions_for_stage(cls, stage_name):
        """Get allowed decisions for a specific stage"""
        return cls.STAGE_DECISIONS.get(
            stage_name, [("Accept", "قبول"), ("Reject", "رفض"), ("Hold", "حجز")]
        )

    @classmethod
    def get_defects_for_stage(cls, stage_name):
        """Get allowed defects for a specific stage"""
        return cls.STAGE_DEFECTS.get(stage_name, [("Other", "أخرى")])

    # Stored stage decisions are display strings (see STAGE_DECISIONS), not
    # the uppercase ACCEPT/REJECT used on Pipe.final_decision_value. Classify
    # any saved value into a stable bucket used by progress/reporting views.
    _REJECT_VALUES = frozenset(
        {
            "Reject",
            "REJECT",
            "rejected",
            "رفض",
            "تالف",
            "Returned",
            "مرتجع",
        }
    )
    _ACCEPT_VALUES = frozenset(
        {
            "Accept",
            "ACCEPT",
            "accepted",
            "قبول",
            "Accept with remark",
            "قبول مع ملاحظة",
            "Delivered",
            "تم التسليم",
            # Melting Ladle: "inspect …" outcomes approve the ladle, just at
            # different sampling levels — they are not rejects.
            "Inspect Last pipes",
            "فحص أخيرة فقط",
            "Inspect 1st and Last pipes",
            "فحص أولى وأخيرة",
            "Inspect 100%",
            "فحص الشحنة 100%",
        }
    )

    @staticmethod
    def split_multi(value):
        """Split a multi-pick defect field into its individual values.

        ``defect_type`` and ``defect_reason`` hold one comma-joined string
        ("Sand hole, Crack") since the stage grid went multi-select, so the
        column stayed a plain string and every existing reader kept working.
        Counting reports call this so two defects on one row count as two,
        rather than inventing a "Sand hole, Crack" bucket of their own.

        A single legacy value comes back as a one-item list; empty is [].
        """
        return [part.strip() for part in (value or "").split(",") if part.strip()]

    # --- what a measurement popup's button should say about itself ---------
    #
    # Three states, because two of them used to look identical. The button went
    # green as soon as the profile existed, and the profile exists the moment
    # the Min/Nominal/Max band is written — which the order's spec does on its
    # own, before anyone measures anything. A green button therefore promised
    # readings that were not there.

    POPUP_EMPTY = "empty"        # nothing recorded
    POPUP_STANDARD = "standard"  # a band to judge against, but no readings yet
    POPUP_DATA = "data"          # readings taken

    @staticmethod
    def _popup_state(has_readings, has_band):
        if has_readings:
            return PipeStage.POPUP_DATA
        return PipeStage.POPUP_STANDARD if has_band else PipeStage.POPUP_EMPTY

    def lining_popup_state(self):
        """Cement and coating thickness, on the Coating stage."""
        from app.services import lining_points_service as lps
        tp = self.thickness_profile or {}
        readings = any(
            lps.has_readings(tp.get(lps.points_key(layer)))
            for layer in lps.LAYERS
        ) or any(
            # rows written before the socket/spigot grid
            any(v is not None for v in (tp.get(layer) or []))
            for layer in lps.LAYERS
        )
        band = any(
            tp.get("%s%s" % (layer, suffix)) is not None
            for layer in lps.LAYERS
            for suffix in ("_std", "_std_min", "_std_max")
        )
        return self._popup_state(readings, band)

    def dimension_popup_state(self):
        """CCM wall thickness and diameter."""
        dp = self.dimension_profile or {}
        thickness = dp.get("thickness") or {}

        def any_number(container):
            """True if any leaf of a dict-of-lists or a flat list is a number.

            Covers both wall-thickness shapes: "positions" keyed by metre since
            2026-08-22, and the older flat "samples" keyed by symbol.
            """
            rows = container.values() if isinstance(container, dict) else [container]
            for row in rows:
                for value in (row or []) if isinstance(row, list) else [row]:
                    if value is not None:
                        return True
            return False

        readings = (
            any_number(thickness.get("positions") or {})
            or any_number(thickness.get("samples") or {})
            or any((pv or {}).get(axis) is not None
                   for pv in (dp.get("ovality") or {}).get("points", {}).values()
                   for axis in ("x", "y"))
        )
        band = any(thickness.get(key) is not None
                   for key in ("standard", "standard_min", "standard_max"))
        return self._popup_state(readings, band)

    def records_work(self):
        """True when a person actually did something on this stage.

        A stage row is not by itself evidence that the stage happened. Rows
        were written for the Min/Nominal/Max band, which arrives from the
        order's Application spec, and for the Finish visual checklist, whose
        boxes post whether or not they are ticked — so a pipe nobody had
        touched showed CCM, Coating and Finish as In Progress with a date.
        The parser no longer creates such rows, but the ones already written
        must not keep claiming work either.

        A date on its own is not evidence: the register form prefills it.
        """
        if (self.decision or "").strip():
            return True
        if (self.notes or "").strip():
            return True
        if self.approved_by_id or self.machine_id or self.has_defect:
            return True
        if self.ring_profile or self.zinc_profile or self.ovality_profile:
            return True
        if any((self.visual_profile or {}).values()):
            return True
        return (self.lining_popup_state() == self.POPUP_DATA
                or self.dimension_popup_state() == self.POPUP_DATA)

    @classmethod
    def classify_decision(cls, value):
        """Return 'accept' | 'reject' | 'pending' for any stored decision string."""
        if not value:
            return "pending"
        v = str(value).strip()
        if v in cls._REJECT_VALUES:
            return "reject"
        if v in cls._ACCEPT_VALUES:
            return "accept"
        # Case-insensitive fallback for legacy lowercase variants ("accept"/"reject").
        lowered = v.lower()
        if any(r.lower() == lowered for r in cls._REJECT_VALUES):
            return "reject"
        if any(a.lower() == lowered for a in cls._ACCEPT_VALUES):
            return "accept"
        return "pending"

    def __repr__(self):
        return f"<PipeStage {self.pipe_id}:{self.stage_name}>"


@event.listens_for(db.session, "after_flush")
def _drop_pipe_stage_memo(session, flush_context):
    """Invalidate cached stage maps whenever any PipeStage is written.

    Without this, a request that saves a stage decision and then re-reads it
    through get_stage() would be answered from the map loaded before the write
    — the stage screens do exactly that, so the operator would save a decision
    and be shown the previous one.

    Every Pipe in the identity map is cleared rather than just the ones whose
    rows changed: resolving a PipeStage back to its Pipe here can emit further
    lazy loads mid-flush, and the identity map is small enough per request that
    clearing all of them is the cheaper and safer trade.
    """
    if not any(
        isinstance(obj, PipeStage)
        for obj in (session.new | session.dirty | session.deleted)
    ):
        return
    for obj in list(session.identity_map.values()):
        if isinstance(obj, Pipe):
            obj.__dict__.pop("_stage_map_memo", None)
