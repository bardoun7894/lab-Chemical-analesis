"""
Report every dangling foreign-key reference in the SQLite database.

SQLite does not enforce foreign keys unless ``PRAGMA foreign_keys=ON`` is set
per connection, which this app never does, so a child row can point at a parent
id that was deleted years ago and nothing complains. Postgres enforces the
constraint at insert time, so each of these is a row that will refuse to
migrate.

This is read-only. It changes nothing; it only tells you what has to be decided.

Usage:
    SOURCE_SQLITE=/path/to/lab_chemical.db python scripts/audit_orphan_fks.py
"""

import os
import sys

from sqlalchemy import create_engine, inspect, text

# The app is only imported for its metadata (the authoritative FK graph), so
# point DATABASE_URL at the source file to keep create_app from touching
# anything else.
SOURCE = os.environ.get("SOURCE_SQLITE")
if not SOURCE or not os.path.exists(SOURCE):
    print(f"ERROR: SOURCE_SQLITE missing or not a file: {SOURCE!r}")
    raise SystemExit(1)

os.environ["DATABASE_URL"] = f"sqlite:///{SOURCE}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402


def main():
    app = create_app("production")
    engine = create_engine(f"sqlite:///{SOURCE}")
    tables = set(inspect(engine).get_table_names())

    with app.app_context(), engine.connect() as conn:
        findings = []
        checked = 0

        for table in db.metadata.sorted_tables:
            if table.name not in tables:
                continue
            live_cols = {c["name"] for c in inspect(engine).get_columns(table.name)}

            for fk in table.foreign_keys:
                child = fk.parent
                parent = fk.column
                if child.name not in live_cols or parent.table.name not in tables:
                    continue
                checked += 1

                rows = conn.execute(
                    text(
                        f'SELECT "{child.name}" AS v, COUNT(*) AS n '
                        f'FROM "{table.name}" c '
                        f'WHERE c."{child.name}" IS NOT NULL '
                        f'  AND NOT EXISTS (SELECT 1 FROM "{parent.table.name}" p '
                        f'                  WHERE p."{parent.name}" = c."{child.name}") '
                        f'GROUP BY "{child.name}" ORDER BY n DESC'
                    )
                ).all()

                if rows:
                    findings.append(
                        {
                            "table": table.name,
                            "column": child.name,
                            "parent": f"{parent.table.name}.{parent.name}",
                            "nullable": child.nullable,
                            "bad_ids": [(r.v, r.n) for r in rows],
                            "rows": sum(r.n for r in rows),
                        }
                    )

        print(f"checked {checked} foreign keys across {len(db.metadata.sorted_tables)} tables")
        print()

        if not findings:
            print("No orphaned references. The data is clean for Postgres.")
            return

        blocking = [f for f in findings if not f["nullable"]]
        total = sum(f["rows"] for f in findings)

        print(f"{len(findings)} column(s) hold {total} orphaned reference(s):")
        print()
        for f in sorted(findings, key=lambda x: -x["rows"]):
            flag = "NOT NULL - blocks migration" if not f["nullable"] else "nullable"
            ids = ", ".join(f"{v} x{n}" for v, n in f["bad_ids"][:8])
            more = "" if len(f["bad_ids"]) <= 8 else f" (+{len(f['bad_ids']) - 8} more)"
            print(f"  {f['table']}.{f['column']} -> {f['parent']}")
            print(f"      {f['rows']} row(s), {flag}")
            print(f"      missing parent ids: {ids}{more}")
            print()

        if blocking:
            print("The NOT NULL ones cannot simply be nulled; each needs either the")
            print("parent row restored or the child row removed. Decide per case.")
        else:
            print("All are nullable: they can be set to NULL without dropping any row.")


if __name__ == "__main__":
    main()
