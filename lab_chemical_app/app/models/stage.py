"""
Production Stage Model - Admin-configurable production stages
"""

from datetime import datetime

from flask import g, has_app_context
from sqlalchemy import event

from app import db


def _stage_cache():
    """Per-request memo for stage lookups, or None outside an app context.

    active_names() and by_code() are called from the template context processor
    and from most report services, which made the stage table the single most
    queried thing in the app: 12-13 identical SELECTs on every page render,
    including pages that show no stage information at all.

    The cache lives on ``g``, so it lasts exactly one request and a stage the
    admin renames is visible on the next one. Outside a request — CLI, boot
    seeding, migrations — this returns None and every lookup goes to the
    database as before.
    """
    if not has_app_context():
        return None
    cache = g.__dict__.get("_stage_cache")
    if cache is None:
        cache = {}
        g._stage_cache = cache
    return cache

# Built-in stages seeded on first run; also the fallback when the table
# is empty or the DB is unavailable (e.g. during CLI commands).
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

# Stable, language-independent identifier for each built-in stage.
# `code` is the column the rest of the app filters on so the user can rename
# the display `name` (and `name_ar`) without breaking code paths.
DEFAULT_STAGES_AR = {
    "Melting Ladle": "بوتقة الصهر",
    "CCM": "الصب المستمر",
    "Annealing": "التلدين",
    "Lab Approval": "اعتماد المعمل",
    "Zinc": "الزنك",
    "Cutting": "القطع",
    "Hydrotest": "الاختبار الهيدروليكي",
    "Cement": "الأسمنت",
    "Coating": "الطلاء",
    "Finish": "التشطيب",
    "Delivery": "التسليم",
}

BUILTIN_CODES = {
    "Melting Ladle": "melting_ladle",
    "CCM": "ccm",
    "Annealing": "annealing",
    "Lab Approval": "lab",
    "Zinc": "zinc",
    "Cutting": "cutting",
    "Hydrotest": "hydrotest",
    "Cement": "cement",
    "Coating": "coating",
    "Finish": "finish",
    "Delivery": "delivery",
}

# Built-in stages introduced AFTER the initial seed. seed_defaults() only runs
# on an empty table, so these would never appear on an existing (prod) DB —
# ensure_added_stages() inserts each one just after its anchor stage, computing
# a sort_order between the anchor and the following stage. Map: code -> anchor code.
BUILTIN_INSERT_AFTER = {
    # (none currently) — the built-in "lab" stage is displayed as "Lab Approval";
    # there is no separate lab_approval stage.
}


class ProductionStage(db.Model):
    """Admin-configurable production stage (Settings > Stage Management)"""

    __tablename__ = "production_stages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    name_ar = db.Column(db.String(100), default="")
    sort_order = db.Column(db.Integer, default=0, index=True)
    is_active = db.Column(db.Boolean, default=True)
    # Stable, language-independent identifier for built-in stages. Code paths
    # filter on `code` so the user can rename the display `name` (and `name_ar`)
    # without breaking references in PipeStage, StageDefectType, etc. Custom
    # stages have `code = None` since they have no hard-coded references.
    code = db.Column(db.String(20), index=True, nullable=True)
    # Built-in stages have code-level behavior (Annealing date/time, Finish
    # bundle, Delivery, decision cascade). They can be relabeled, renamed,
    # reordered, or deactivated but never deleted.
    is_builtin = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @classmethod
    def active_names(cls):
        """Ordered list of active stage names; falls back to DEFAULT_STAGES."""
        cache = _stage_cache()
        if cache is not None and "active_names" in cache:
            return list(cache["active_names"])

        names = None
        try:
            rows = (
                cls.query.filter_by(is_active=True)
                .order_by(cls.sort_order, cls.id)
                .all()
            )
            if rows:
                names = [r.name for r in rows]
        except Exception:
            pass
        if names is None:
            names = list(DEFAULT_STAGES)

        if cache is not None:
            cache["active_names"] = names
        # A copy, so a caller that mutates the list cannot corrupt the cache
        # for the rest of the request.
        return list(names)

    @classmethod
    def by_code(cls, code):
        """Look up a built-in stage by its stable code (e.g. 'ccm', 'annealing').

        Returns the row or None. Use `row.name` for the current display name
        (which the user may have renamed). Use `row.code` as the immutable
        identifier when you need to compare across requests.
        """
        cache = _stage_cache()

        # Load every stage in one query rather than one per code. The template
        # context processor asks for the name of all ~12 built-in codes on every
        # single page render; caching each code separately still left twelve
        # round trips, which was the most repeated query in the whole app.
        if cache is not None:
            by_code = cache.get("by_code_map")
            if by_code is None:
                try:
                    by_code = {row.code: row for row in cls.query.all() if row.code}
                except Exception:
                    return None
                cache["by_code_map"] = by_code
            return by_code.get(code)

        try:
            return cls.query.filter_by(code=code).first()
        except Exception:
            return None

    @classmethod
    def name_for_code(cls, code):
        """Return the current display name for a built-in code.

        Falls back to the original DEFAULT_STAGES entry if the DB row is gone
        (CLI, migration, fresh DB), so callers can keep using it as a stable
        string without crashing during boot.
        """
        row = cls.by_code(code)
        if row is not None:
            return row.name
        for name in DEFAULT_STAGES:
            if BUILTIN_CODES.get(name) == code:
                return name
        return code

    @classmethod
    def collapse_lab_approval(cls):
        """One-time idempotent migration: collapse the former two-stage design
        (advisory "Lab" + separate "Lab Approval") back into ONE stage.

        End state: the built-in ``lab`` stage is displayed as "Lab Approval";
        the separate ``lab_approval`` stage is removed. Existing ``pipe_stages``
        saved under the display name "Lab" are migrated to "Lab Approval" so
        historical decisions stay visible under the renamed stage.

        Safe/idempotent: no-op once done. Aborts (leaving both stages intact) if
        the extra ``lab_approval`` stage already has DECIDED pipe rows, so no
        real data is ever deleted. Raw SQL + schema-adaptive so it survives a
        partially-migrated prod DB.
        """
        from sqlalchemy import inspect as sa_inspect, text

        try:
            cols = {
                c["name"] for c in sa_inspect(db.engine).get_columns(cls.__tablename__)
            }
        except Exception:
            return
        if "name" not in cols or "code" not in cols:
            return
        tbl = cls.__tablename__
        try:
            lab = db.session.execute(
                text(f"SELECT name FROM {tbl} WHERE code = 'lab' LIMIT 1")
            ).first()
            if lab is None:
                return
            extra = db.session.execute(
                text(f"SELECT name FROM {tbl} WHERE code = 'lab_approval' LIMIT 1")
            ).first()

            # 1) Remove the extra lab_approval stage (and only its EMPTY pipe rows).
            if extra is not None:
                extra_name = extra[0]
                db.session.execute(
                    text(
                        "DELETE FROM pipe_stages WHERE stage_name = :n "
                        "AND (decision IS NULL OR decision = '')"
                    ),
                    {"n": extra_name},
                )
                still = db.session.execute(
                    text("SELECT COUNT(*) FROM pipe_stages WHERE stage_name = :n"),
                    {"n": extra_name},
                ).first()
                if still and still[0] > 0:
                    # Decided rows exist on the extra stage — do NOT collapse.
                    db.session.commit()
                    return
                db.session.execute(
                    text(f"DELETE FROM {tbl} WHERE code = 'lab_approval'")
                )

            # 2) Rename the lab stage display → "Lab Approval".
            set_ar = ", name_ar = 'اعتماد المعمل'" if "name_ar" in cols else ""
            db.session.execute(
                text(
                    f"UPDATE {tbl} SET name = 'Lab Approval'{set_ar} "
                    "WHERE code = 'lab' AND name != 'Lab Approval'"
                )
            )

            # 3) Migrate historical pipe rows "Lab" → "Lab Approval" (skip any
            #    pipe that somehow already has a "Lab Approval" row).
            db.session.execute(
                text(
                    "UPDATE pipe_stages SET stage_name = 'Lab Approval' "
                    "WHERE stage_name = 'Lab' AND pipe_id NOT IN "
                    "(SELECT pipe_id FROM pipe_stages WHERE stage_name = 'Lab Approval')"
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    @classmethod
    def seed_defaults(cls):
        """Seed built-in stages once (no-op if the table already has rows).

        Safe under concurrent gunicorn worker boot: another worker may seed
        between our count() check and commit, so a UNIQUE failure is treated
        as "already seeded" and rolled back cleanly.
        """
        if cls.query.count() > 0:
            return
        for i, name in enumerate(DEFAULT_STAGES):
            db.session.add(
                cls(
                    name=name,
                    name_ar=DEFAULT_STAGES_AR.get(name, ""),
                    code=BUILTIN_CODES.get(name),
                    sort_order=(i + 1) * 10,
                    is_active=True,
                    is_builtin=True,
                )
            )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    @classmethod
    def ensure_added_stages(cls):
        """Insert built-in stages added after the initial seed (idempotent).

        seed_defaults() is a no-op once the table has rows, so a stage added to
        DEFAULT_STAGES later would never appear on an existing DB. For each code
        in BUILTIN_INSERT_AFTER, insert it right after its anchor stage if
        missing, with a sort_order between the anchor and the next stage so
        ordering stays correct without renumbering existing rows.

        Schema-adaptive on purpose: existence and anchor lookups key on ``name``
        (the one column guaranteed present on every historical DB), and the
        INSERT writes only the columns that actually exist in the live table.
        This survives a partially-migrated prod DB whose ``code`` column is
        unbackfilled or whose ``is_builtin`` column is absent — the exact
        condition that would otherwise make this a silent no-op. Uses raw SQL
        (not the ORM) so a column the ORM maps but the table lacks can't break
        the statement.

        Safe under concurrent gunicorn worker boot: a UNIQUE(name) failure from
        another worker seeding first is rolled back cleanly.
        """
        from sqlalchemy import inspect as sa_inspect, text

        try:
            cols = {
                c["name"] for c in sa_inspect(db.engine).get_columns(cls.__tablename__)
            }
        except Exception:
            return
        if "name" not in cols or "sort_order" not in cols:
            return

        tbl = cls.__tablename__
        for code, after_code in BUILTIN_INSERT_AFTER.items():
            try:
                name = next((n for n, c in BUILTIN_CODES.items() if c == code), None)
                after_name = next(
                    (n for n, c in BUILTIN_CODES.items() if c == after_code), None
                )
                if not name or not after_name:
                    continue

                # Existence + anchor keyed on name — always present, unlike code.
                exists = db.session.execute(
                    text(f'SELECT 1 FROM {tbl} WHERE name = :n LIMIT 1'), {"n": name}
                ).first()
                if exists:
                    continue
                anchor = db.session.execute(
                    text(f'SELECT sort_order FROM {tbl} WHERE name = :n LIMIT 1'),
                    {"n": after_name},
                ).first()
                if anchor is None:
                    continue
                anchor_order = anchor[0] or 0
                nxt = db.session.execute(
                    text(f'SELECT MIN(sort_order) FROM {tbl} WHERE sort_order > :o'),
                    {"o": anchor_order},
                ).first()
                if nxt is not None and nxt[0] is not None:
                    new_order = (anchor_order + nxt[0]) // 2
                    if new_order <= anchor_order:
                        new_order = anchor_order + 1
                else:
                    new_order = anchor_order + 5

                row = {
                    "name": name,
                    "name_ar": DEFAULT_STAGES_AR.get(name, ""),
                    "sort_order": new_order,
                    "is_active": 1,
                    "is_builtin": 1,
                    "code": code,
                }
                row = {k: v for k, v in row.items() if k in cols}
                collist = ", ".join(f'"{k}"' for k in row)
                vallist = ", ".join(f":{k}" for k in row)
                db.session.execute(
                    text(f"INSERT INTO {tbl} ({collist}) VALUES ({vallist})"), row
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

    @classmethod
    def backfill_codes(cls):
        """Backfill the `code` column for built-in stages that were seeded
        before the column existed.

        Maps the current `name` back to its BUILTIN_CODES entry — works
        whether or not the user already renamed the stage, because we also
        try the live name and fall back to a sanitized version of it.
        Idempotent.
        """
        for stage in cls.query.filter(cls.is_builtin.is_(True), cls.code.is_(None)).all():
            # Exact match first (user hasn't renamed it yet)
            code = BUILTIN_CODES.get(stage.name)
            if code is None:
                # User renamed it. Best-effort: keep a stable identifier by
                # sanitizing the original name. Falls back to a unique id so
                # the row is still queryable.
                import re
                sanitized = re.sub(r"[^a-z0-9]+", "_", stage.name.lower()).strip("_")
                code = sanitized or f"builtin_{stage.id}"
            stage.code = code
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    def __repr__(self):
        return f"<ProductionStage {self.name}>"


@event.listens_for(db.session, "after_flush")
def _drop_stage_cache_on_write(session, flush_context):
    """Clear the per-request stage cache as soon as a stage row is written.

    The cache would otherwise survive to the end of a request that renamed,
    added or removed a stage — so the admin would save a rename and be shown
    the old name back. Hooking the flush means no write path has to remember
    to invalidate, including ones added later.
    """
    touched = session.new | session.dirty | session.deleted
    if not any(isinstance(obj, ProductionStage) for obj in touched):
        return
    cache = _stage_cache()
    if cache is not None:
        cache.clear()
