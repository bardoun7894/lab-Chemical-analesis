"""
One-way data migration: SQLite -> PostgreSQL.

The schema is NOT translated by hand. It is created from the SQLAlchemy models
themselves (``db.create_all()`` against the Postgres URL), so what lands in
Postgres is by construction the schema the ORM expects. Only the rows are
copied.

Rows are read through the same ``db.metadata`` table objects on both sides,
which is what makes the type conversion correct: SQLite stores DATETIME as an
ISO string and BOOLEAN as 0/1, and the SQLite dialect turns both back into real
Python ``datetime``/``bool`` values on the way out, which the Postgres dialect
then binds as ``timestamp``/``boolean``. Reading with a raw ``sqlite3`` cursor
would hand Postgres the raw strings and integers and fail on every such column.

Tables are copied in ``metadata.sorted_tables`` order (topological by foreign
key) so parents exist before children. Sequences are re-synced afterwards,
because the rows carry their original primary keys and Postgres would otherwise
hand out id=1 on the next insert.

Usage:
    SOURCE_SQLITE=/path/to/lab_chemical.db \
    TARGET_POSTGRES=postgresql+psycopg2://lab:pw@host:5432/lab_chemical \
    python scripts/migrate_sqlite_to_postgres.py [--wipe]
"""

import os
import sys

from sqlalchemy import create_engine, inspect, select, text


def _fail(msg):
    print(f"ERROR: {msg}")
    raise SystemExit(1)


SOURCE = os.environ.get("SOURCE_SQLITE")
TARGET = os.environ.get("TARGET_POSTGRES")

if not SOURCE or not os.path.exists(SOURCE):
    _fail(f"SOURCE_SQLITE missing or not a file: {SOURCE!r}")
if not TARGET:
    _fail("TARGET_POSTGRES is required")

# This assignment MUST happen before ``app.config`` is imported. Config reads
# DATABASE_URL in the class body, i.e. once at import time, and falls back to
# SQLite when it is unset — so importing the app first would quietly point this
# whole migration back at a SQLite file and "succeed" against the wrong target.
os.environ["DATABASE_URL"] = TARGET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402


def main():
    source, target = SOURCE, TARGET
    wipe = "--wipe" in sys.argv

    app = create_app("production")

    # Belt and braces: prove the engine really is Postgres before writing.
    resolved = app.config["SQLALCHEMY_DATABASE_URI"]
    if not resolved.startswith(("postgresql://", "postgresql+")):
        _fail(f"target engine is not Postgres, refusing to run: {resolved.split('@')[0]}")

    src_engine = create_engine(f"sqlite:///{source}")

    with app.app_context():
        print(f"source : {source}")
        print(f"target : {target.rsplit('@', 1)[-1]}")
        print()

        if wipe:
            print("[wipe] dropping existing target schema")
            db.drop_all()

        print("[schema] create_all() from the ORM models")
        db.create_all()
        created = sorted(inspect(db.engine).get_table_names())
        print(f"         {len(created)} tables in target")

        src_tables = set(inspect(src_engine).get_table_names())
        orphans = sorted(src_tables - {t.name for t in db.metadata.sorted_tables})
        if orphans:
            print(f"[note]   in SQLite but not in the ORM, skipped: {', '.join(orphans)}")

        print()
        print("[copy] table                          rows")
        totals = {}
        src_inspector = inspect(src_engine)
        with src_engine.connect() as src, db.engine.begin() as dst:
            for table in db.metadata.sorted_tables:
                if table.name not in src_tables:
                    print(f"       {table.name:<30}     -   (absent in source)")
                    continue

                # Only columns present on BOTH sides; a column the ORM gained
                # after the SQLite file was last migrated simply stays default.
                src_cols = {c["name"] for c in src_inspector.get_columns(table.name)}
                cols = [c for c in table.columns if c.name in src_cols]
                dropped = [c.name for c in table.columns if c.name not in src_cols]

                rows = [dict(r._mapping) for r in src.execute(select(*cols))]
                if rows:
                    dst.execute(table.insert(), rows)

                totals[table.name] = len(rows)
                note = f"  (source lacks: {', '.join(dropped)})" if dropped else ""
                print(f"       {table.name:<30} {len(rows):>5}{note}")

        print()
        print("[sequences] re-syncing to max(id)")
        resynced = 0
        with db.engine.begin() as conn:
            for table in db.metadata.sorted_tables:
                for col in table.primary_key.columns:
                    seq = conn.execute(
                        text("SELECT pg_get_serial_sequence(:t, :c)"),
                        {"t": table.name, "c": col.name},
                    ).scalar()
                    if not seq:
                        continue
                    conn.execute(
                        text(
                            f"SELECT setval('{seq}', "
                            f"COALESCE((SELECT MAX({col.name}) FROM {table.name}), 0) + 1, false)"
                        )
                    )
                    resynced += 1
        print(f"            {resynced} sequences")

        print()
        print("[verify] row-count parity, source vs target")
        bad = []
        with src_engine.connect() as src, db.engine.connect() as dst:
            for name, copied in sorted(totals.items()):
                a = src.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
                b = dst.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
                if a != b:
                    bad.append((name, a, b))
        if bad:
            for name, a, b in bad:
                print(f"         MISMATCH {name}: sqlite={a} postgres={b}")
            _fail(f"{len(bad)} table(s) did not match")

        print(f"         OK - {len(totals)} tables, {sum(totals.values())} rows identical")
        print()
        print("MIGRATION COMPLETE")


if __name__ == "__main__":
    main()
