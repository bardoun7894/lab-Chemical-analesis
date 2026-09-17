"""
Lightweight migration helper for SQLite.

The app relies on db.create_all(), which creates missing tables but never
alters existing ones. When a column is added to a model, existing databases
will miss it. This module applies incremental ALTER TABLE statements at app
startup for the handful of columns we need to add on top of create_all().

Each entry is idempotent — it checks the live table schema via PRAGMA before
running the ALTER, so re-running is a no-op once the column exists.
"""
from sqlalchemy import inspect, text
from app import db


# (table_name, column_name, DDL fragment for SQLite ALTER TABLE ADD COLUMN)
PENDING_MIGRATIONS = [
    # Mold filtering + description for pipe-form DN filter
    ('molds', 'diameter', 'INTEGER'),
    ('molds', 'description', 'VARCHAR(200)'),

    # Audit metadata on production orders, chemical, mechanical, pipe stages
    ('production_orders', 'modified_by_id', 'INTEGER'),
    ('chemical_analyses', 'modified_by_id', 'INTEGER'),
    ('chemical_analyses', 'updated_at', 'DATETIME'),
    ('pipes', 'modified_by_id', 'INTEGER'),
    ('pipes', 'updated_at', 'DATETIME'),

    # ('mechanical_tests', 'retest_count', 'INTEGER DEFAULT 0') was here. No
    # model ever declared the column and nothing read it — all 79 production
    # rows held the default 0 — so it existed only because this list added it.
    # Alembic revision b1f4a2c73d90 drops it; the entry has to go at the same
    # time or the next boot would add it straight back.

    # Product parameter: # PCs/#Stages pass (inserted after LENGTH in the code)
    ('products', 'pcs_stages_pass_param_id', 'INTEGER'),

    # Scannable product barcode (Code128 on the sticker). Added 2026-07-12;
    # the model column was lost in a later sync while the routes/form kept
    # referencing product.barcode, so barcodes silently failed to save.
    ('products', 'barcode', 'VARCHAR(32)'),

    # Stable, language-independent code per built-in stage so the user can
    # rename the display `name` (and `name_ar`) without breaking code paths
    # that filter PipeStage.stage_name etc.
    ('production_stages', 'code', 'VARCHAR(20)'),
    # Flag distinguishing built-in stages from custom ones. Older DBs predate
    # this column; backfill_codes() and seeding query it, so ensure it exists
    # before those run (a missing column made them throw and silently skip the
    # Lab Approval insert on partially-migrated prod DBs).
    ('production_stages', 'is_builtin', 'BOOLEAN DEFAULT 0'),

    # Per-stage scoping for defect reasons (added 2026-07-25, mirroring
    # stage_defect_types.stage_name). NULL/'' on legacy rows = applies to
    # every stage, so existing global rows keep working.
    ('defect_reasons', 'stage_name', 'VARCHAR(50)'),

    # Casting temperature (°C) recorded on the CCM stage (added 2026-07-25).
    ('pipe_stages', 'temperature', 'FLOAT'),
    ('pipe_stage_history', 'temperature', 'FLOAT'),

    # Per-meter cement/coating thickness on the Coating stage and per-point
    # dimension measurements on the CCM stage — both stored as JSON text.
    ('pipe_stages', 'thickness_profile', 'TEXT'),
    ('pipe_stages', 'dimension_profile', 'TEXT'),

    # Ovality measurements on the Annealing stage (added 2026-08-16, DrAlaa).
    # {"points": {"ID"|"D1".."D15": {"x", "y", "ovality"}}} — ovality % is
    # stored precomputed as (x - y) / (x + y) * 100.
    ('pipe_stages', 'ovality_profile', 'TEXT'),

    # Ladle melt weight (kg) on the chemical analysis (added 2026-08-17,
    # DrAlaa). Weight of the pour itself — independent of product/pipe weights.
    ('chemical_analyses', 'weight', 'FLOAT'),

    # Visual inspection profile for Finish stage (added 2026-08-17).
    # {"marking": [m1..m6], "ovality": [m1..m6], "straightness": [m1..m6],
    #  "internal_finish": [m1..m6], "external_finish": [m1..m6]}
    ('pipe_stages', 'visual_profile', 'TEXT'),

    # Zinc coating-mass sheet on the Zinc stage (added 2026-08-27).
    # {"m1", "m2", "area", "c", "mass"} — the two sample weights in
    # grams, the stripped area in m2, the sheet's C factor, and the
    # resulting coating mass in g/m2, computed at save time.
    ('pipe_stages', 'zinc_profile', 'TEXT'),

    # Ring deflection test on the Cutting stage (added 2026-08-29).
    # {"force", "od_initial", "od_final", "od_diff", "deflection"} — the
    # applied force in kN, the outside diameter before and after the
    # squeeze in mm, and the two derived rows, computed at save time.
    ('pipe_stages', 'ring_profile', 'TEXT'),

    # Per-pipe warehouse barcode (added 2026-08-29). The number the
    # warehouse system knows the pipe by; scanned to open the receiving
    # screen. UNIQUE cannot be added by ALTER on SQLite, so the constraint
    # is created separately in PENDING_INDEXES below.
    ('pipes', 'warehouse_barcode', 'VARCHAR(32)'),

    # Application block on the production order (added 2026-08-27):
    # which standard the run is built to, and the wall / cement /
    # coating thicknesses it must hold. Stored as JSON text so a
    # standard added later is a key, not a migration.
    ('production_orders', 'application_profile', 'TEXT'),

    # Application block on the product — the standard it is built to plus the
    # wall / cement / coating thicknesses. Same JSON shape as the order's
    # application_profile; an order copies it on product select. 2026-08-29.
    ('products', 'application_profile', 'TEXT'),

    # INTENT_USE parameters carry which standards group they offer —
    # 'sewage' | 'water' | 'both'. Drives the Application block's checkboxes
    # on the product form. 2026-08-29.
    ('product_parameters', 'application_group', 'VARCHAR(10)'),
]


# (index_name, table_name, DDL body) — created after the columns above, for
# constraints SQLite's ALTER TABLE cannot express. Idempotent via IF NOT EXISTS.
PENDING_INDEXES = [
    ('ix_pipes_warehouse_barcode', 'pipes',
     'CREATE UNIQUE INDEX IF NOT EXISTS ix_pipes_warehouse_barcode '
     'ON pipes (warehouse_barcode)'),
]


def _column_exists(table_name, column_name):
    inspector = inspect(db.engine)
    try:
        cols = [c['name'] for c in inspector.get_columns(table_name)]
    except Exception:
        return False
    return column_name in cols


def _table_exists(table_name):
    inspector = inspect(db.engine)
    return table_name in inspector.get_table_names()


def apply_pending_migrations():
    """Apply any missing column additions. Safe to run on every boot."""
    applied = []
    with db.engine.begin() as conn:
        for table, column, ddl in PENDING_MIGRATIONS:
            if not _table_exists(table):
                # Table not yet created — create_all() will handle it with the new schema.
                continue
            if _column_exists(table, column):
                continue
            try:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
                applied.append(f'{table}.{column}')
            except Exception:
                # Ignore if the backend rejects it (e.g. column already added in parallel)
                pass

        for index_name, table, ddl in PENDING_INDEXES:
            if not _table_exists(table):
                continue
            try:
                conn.execute(text(ddl))
                applied.append(index_name)
            except Exception:
                # A pre-existing duplicate would fail the UNIQUE build. Leave
                # the index off rather than refusing to boot — the app still
                # works, and the duplicate is visible in the data.
                pass
    return applied
