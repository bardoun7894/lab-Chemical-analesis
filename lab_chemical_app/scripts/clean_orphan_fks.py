"""
Repair dangling foreign-key references in a SQLite copy, before migrating.

Run ``audit_orphan_fks.py`` first to see what is there. This script fixes two
distinct problems and keeps them distinct in the report:

1. Empty string used where NULL was meant. ``pipes.ladle_id = ''`` is not a
   ladle, but it is also not NULL, so Postgres tries to resolve it as a foreign
   key and fails. Normalised to NULL.

2. A genuinely dangling pointer: the parent row was deleted and the child
   survived, because SQLite never enforced the constraint. The child row is
   KEPT and only the pointer is nulled — the link is already broken, nulling it
   just makes that fact representable.

No row is ever deleted. Every single change is printed with its old value, so
the report is the record of what the broken links used to point at.

This refuses to run against a database it was not pointed at explicitly, and
should be run on the migration COPY, never on the live file.

Usage:
    TARGET_SQLITE=/path/to/copy.db python scripts/clean_orphan_fks.py [--apply]

Without --apply it is a dry run.
"""

import os
import sys

from sqlalchemy import create_engine, inspect, text

TARGET = os.environ.get("TARGET_SQLITE")
if not TARGET or not os.path.exists(TARGET):
    print(f"ERROR: TARGET_SQLITE missing or not a file: {TARGET!r}")
    raise SystemExit(1)

os.environ["DATABASE_URL"] = f"sqlite:///{TARGET}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402


def main():
    apply = "--apply" in sys.argv
    app = create_app("production")
    engine = create_engine(f"sqlite:///{TARGET}")
    tables = set(inspect(engine).get_table_names())

    print(f"target: {TARGET}")
    print(f"mode  : {'APPLY (writes)' if apply else 'DRY RUN (no writes)'}")
    print()

    empties = 0
    dangling = 0

    with app.app_context(), engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in tables:
                continue
            live_cols = {c["name"] for c in inspect(engine).get_columns(table.name)}

            for fk in table.foreign_keys:
                child, parent = fk.parent, fk.column
                if child.name not in live_cols or parent.table.name not in tables:
                    continue
                if not child.nullable:
                    continue

                bad = conn.execute(
                    text(
                        f'SELECT rowid AS rid, "{child.name}" AS v '
                        f'FROM "{table.name}" c '
                        f'WHERE c."{child.name}" IS NOT NULL '
                        f'  AND NOT EXISTS (SELECT 1 FROM "{parent.table.name}" p '
                        f'                  WHERE p."{parent.name}" = c."{child.name}")'
                    )
                ).all()
                if not bad:
                    continue

                for row in bad:
                    kind = "empty-string" if str(row.v).strip() == "" else "dangling"
                    if kind == "empty-string":
                        empties += 1
                    else:
                        dangling += 1
                    print(
                        f"  {table.name}.{child.name} rowid={row.rid} "
                        f"[{kind}] {row.v!r} -> NULL"
                    )

                if apply:
                    conn.execute(
                        text(
                            f'UPDATE "{table.name}" SET "{child.name}" = NULL '
                            f'WHERE rowid IN ({",".join(str(r.rid) for r in bad)})'
                        )
                    )

    print()
    print(f"empty-string -> NULL : {empties}")
    print(f"dangling     -> NULL : {dangling}")
    print(f"rows deleted         : 0")
    if not apply:
        print()
        print("Dry run. Re-run with --apply to write these changes.")


if __name__ == "__main__":
    main()
