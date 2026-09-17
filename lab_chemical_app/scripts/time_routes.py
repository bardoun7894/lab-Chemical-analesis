"""
Time the heavy screens, so caching is a decision about numbers.

Caching is not free: a cached report is a report that can be wrong, and on a QC
system a stale decision screen is worse than a slow one. So the question is not
"would a cache help" but "is anything actually slow enough to need one".

Each route is fetched twice and the second timing reported, so one-off import
and connection-pool warmup does not get counted as page cost.

Usage:
    DATABASE_URL=... python scripts/time_routes.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402

ROUTES = [
    "/",
    "/stages/",
    "/reports/",
    "/reports/non-conformance",
    "/reports/rework-report",
    "/reports/andon",
    "/reports/traceability-tree",
    "/admin/permissions",
    "/admin/products",
    "/orders/",
]


def main():
    app = create_app("production")
    with app.app_context():
        from app.models.user import User

        admin = User.query.filter_by(role="super_admin").first() or User.query.first()
        uid = admin.id

    results = []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

        for route in ROUTES:
            client.get(route)  # warm up, not measured
            start = time.perf_counter()
            resp = client.get(route)
            elapsed = (time.perf_counter() - start) * 1000
            results.append((elapsed, route, resp.status_code, len(resp.data)))

    print(f"{'ms':>9}  {'code':>4}  {'KB':>7}  route")
    print("-" * 60)
    for elapsed, route, code, size in sorted(results, reverse=True):
        print(f"{elapsed:9.0f}  {code:>4}  {size/1024:7.0f}  {route}")

    slow = [r for r in results if r[0] > 1000]
    print()
    if slow:
        print(f"{len(slow)} route(s) over 1s — worth caching or indexing:")
        for elapsed, route, _, _ in sorted(slow, reverse=True):
            print(f"  {elapsed:.0f} ms  {route}")
    else:
        print("Nothing over 1s. A cache would add staleness without buying speed.")


if __name__ == "__main__":
    main()
