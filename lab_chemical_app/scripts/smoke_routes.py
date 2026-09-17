"""
Authenticated smoke test over the app's main screens.

The production admin password is not known, so this logs in the way the session
does rather than the way a browser does: it pushes ``_user_id`` into the Flask
test-client session directly, which is exactly what ``flask_login`` reads.

Every route is fetched and its status recorded. A 500 is a failure; a 302 to
the login page means the session trick stopped working and is also treated as a
failure, because a silently-unauthenticated pass would make the whole run
meaningless.

Usage:
    DATABASE_URL=... python scripts/smoke_routes.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402

ROUTES = [
    "/",
    "/stages/",
    "/stages/console",
    "/chemical/",
    "/mechanical/",
    "/orders/",
    "/warehouse/",
    "/reports/",
    "/reports/traceability-tree",
    "/reports/non-conformance",
    "/reports/rework-report",
    "/reports/andon",
    "/analytics/",
    "/admin/",
    "/admin/stages",
    "/admin/settings",
    "/admin/products",
    "/admin/permissions",
]


def main():
    app = create_app("production")
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    print(f"engine: {uri.split('@')[-1] if '@' in uri else uri}")

    with app.app_context():
        from app.models.user import User

        admin = User.query.filter_by(role="super_admin").first() or User.query.first()
        if admin is None:
            print("ERROR: no user to authenticate as")
            return 1
        uid = admin.id
        print(f"acting as: {admin.username} ({admin.role})")
        print()

    failures = []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

        for route in ROUTES:
            try:
                resp = client.get(route, follow_redirects=False)
                code = resp.status_code
                loc = resp.headers.get("Location", "")
                if code >= 500:
                    failures.append((route, code, "server error"))
                    mark = "FAIL"
                elif code in (301, 302) and "login" in loc:
                    failures.append((route, code, f"bounced to login: {loc}"))
                    mark = "FAIL"
                else:
                    mark = "ok  "
                size = len(resp.data)
                print(f"  {mark} {code}  {route:<34} {size:>8} bytes")
            except Exception as exc:  # noqa: BLE001
                failures.append((route, "EXC", repr(exc)))
                print(f"  FAIL EXC {route}")
                traceback.print_exc(limit=3)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for route, code, why in failures:
            print(f"  {route} -> {code} {why}")
        return 1

    print(f"All {len(ROUTES)} routes OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
