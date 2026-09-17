"""
Exercise the write path against whatever engine DATABASE_URL points at.

Reads passing proves the schema is readable; it does not prove that an INSERT
gets a sane primary key. After a bulk copy that carries original ids across,
Postgres sequences still sit at 1 unless they were re-synced, so the first real
insert collides with an existing row. That failure only shows up on write, which
is why this exists.

Creates a scratch row, updates it, reads it back, then deletes it and confirms
it is gone. It only ever touches rows it created itself.

Usage:
    DATABASE_URL=... python scripts/smoke_write.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402


def main():
    app = create_app("production")
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    print(f"engine: {uri.split('@')[-1] if '@' in uri else uri}")
    print()

    return run(app)


def run(app):
    with app.app_context():
        # Customers is a small, self-contained table with no children, so a
        # scratch row here cannot cascade into production data.
        from sqlalchemy import text

        marker = f"__SMOKE_{datetime.utcnow():%Y%m%d%H%M%S}__"

        before = db.session.execute(text("SELECT COUNT(*) FROM customers")).scalar()
        maxid = db.session.execute(text("SELECT COALESCE(MAX(id),0) FROM customers")).scalar()
        print(f"customers before: {before} rows, max id {maxid}")

        db.session.execute(
            text("INSERT INTO customers (name_en, code) VALUES (:n, :c)"),
            {"n": marker, "c": marker[:20]},
        )
        db.session.commit()

        row = db.session.execute(
            text("SELECT id, name_en FROM customers WHERE name_en = :n"), {"n": marker}
        ).first()
        if row is None:
            print("FAIL: inserted row not found")
            return 1
        new_id = row.id
        print(f"insert  -> id {new_id}")
        if new_id <= maxid:
            print(f"FAIL: sequence handed out id {new_id} which is <= existing max {maxid}")
            print("      the sequence was not re-synced after the bulk copy")
            db.session.execute(text("DELETE FROM customers WHERE id = :i"), {"i": new_id})
            db.session.commit()
            return 1

        db.session.execute(
            text("UPDATE customers SET name_en = :n2 WHERE id = :i"),
            {"n2": marker + "_UPD", "i": new_id},
        )
        db.session.commit()
        got = db.session.execute(
            text("SELECT name_en FROM customers WHERE id = :i"), {"i": new_id}
        ).scalar()
        print(f"update  -> {got}")
        if got != marker + "_UPD":
            print("FAIL: update did not take")
            return 1

        db.session.execute(text("DELETE FROM customers WHERE id = :i"), {"i": new_id})
        db.session.commit()

        after = db.session.execute(text("SELECT COUNT(*) FROM customers")).scalar()
        print(f"delete  -> customers back to {after} rows")
        if after != before:
            print(f"FAIL: row count {after} != original {before}")
            return 1

        print()
        print("Write path OK (insert / sequence / update / delete), no residue left")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
