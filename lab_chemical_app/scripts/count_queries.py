"""
Count the SQL statements a page actually issues.

A slow page is either doing expensive work once or cheap work thousands of
times, and the fix is completely different in each case. Timing alone cannot
tell you which. This hooks SQLAlchemy's cursor events and counts, so the answer
is a number rather than a guess.

Repeated statements are reported separately: 400 executions of the same SELECT
with different parameters is the signature of N+1, and it is the one worth
fixing first.

Usage:
    DATABASE_URL=... python scripts/count_queries.py [route ...]
"""

import os
import re
import sys
from collections import Counter

from sqlalchemy import event

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402

DEFAULT_ROUTES = ["/orders/", "/stages/", "/reports/andon", "/"]


def _normalise(sql):
    """Collapse a statement to its shape so repeats group together."""
    sql = " ".join(sql.split())
    sql = re.sub(r"%\([a-zA-Z0-9_]+\)s", "?", sql)
    sql = re.sub(r"\b\d+\b", "N", sql)
    return sql[:110]


def main():
    routes = sys.argv[1:] or DEFAULT_ROUTES
    app = create_app("production")

    with app.app_context():
        from app.models.user import User

        admin = User.query.filter_by(role="super_admin").first() or User.query.first()
        uid = admin.id

    counter = Counter()
    recording = {"on": False}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if recording["on"]:
            counter[_normalise(statement)] += 1

    # db.engine resolves through the app context, so the listener can only be
    # attached inside one.
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _before)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

        for route in routes:
            client.get(route)  # warm caches so imports are not counted
            counter.clear()
            recording["on"] = True
            resp = client.get(route)
            recording["on"] = False

            total = sum(counter.values())
            print(f"\n{route}  ->  {resp.status_code}, {total} queries")
            if total == 0:
                continue
            for sql, n in counter.most_common(4):
                flag = "  <-- N+1" if n >= 10 else ""
                print(f"    {n:>5}x  {sql}{flag}")


if __name__ == "__main__":
    main()
