"""Permission matrix: fail-closed enforcement, seed reconciliation, owner tier.

Covers the gaps found in the 2026-07-12 audit:
  - has_permission() used to fail OPEN on an unseeded row
  - the seed only ran on an empty table, so new screens were never created
  - admin.* rows were rendered in the matrix but enforced nowhere
  - the sidebar gated on hardcoded role booleans, ignoring the matrix
  - no protected owner: any admin could delete any other admin
"""

import unittest

from flask import g

from app import create_app, db
from app.models.permission import (
    Permission,
    RolePermission,
    MODULES,
    seed_default_permissions,
)
from app.models.user import User
from app.services.permission_service import has_permission, get_permission_matrix


class PermissionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.users = {}
        for role in User.ROLES:
            u = User(username=role, full_name=role, role=role, is_active=True)
            u.set_password("x")
            db.session.add(u)
            self.users[role] = u
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, role):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.users[role].id)
            sess["_fresh"] = True
        # setUp holds an app context open for db access, so Flask-Login's cached
        # user on `g` survives between client requests. Drop it or every request
        # after the first keeps the first user.
        g.pop("_login_user", None)

    def _revoke(self, role, module, screen):
        perm = Permission.query.filter_by(module=module, screen=screen).one()
        rp = RolePermission.query.filter_by(role=role, permission_id=perm.id).first()
        if rp:
            db.session.delete(rp)
            db.session.commit()

    # --- fail closed ----------------------------------------------------

    def test_undefined_permission_is_denied(self):
        """The old code returned True here, handing every role a free pass."""
        self.assertFalse(has_permission("viewer", "nosuch", "screen"))
        self.assertFalse(has_permission("admin", "nosuch", "screen"))

    def test_owner_bypasses_matrix_even_when_undefined(self):
        self.assertTrue(has_permission(User.ROLE_SUPER_ADMIN, "nosuch", "screen"))

    def test_revoked_permission_is_denied(self):
        self.assertTrue(has_permission("operator", "chemical", "add"))
        self._revoke("operator", "chemical", "add")
        self.assertFalse(has_permission("operator", "chemical", "add"))

    # --- seed reconciliation --------------------------------------------

    def test_seed_is_idempotent(self):
        before = Permission.query.count()
        seed_default_permissions()
        seed_default_permissions()
        self.assertEqual(Permission.query.count(), before)

    def test_seed_migrates_legacy_access_action_in_place(self):
        """Legacy rows carried action='access'. They must be updated, not
        duplicated, so their RolePermission children survive."""
        perm = Permission.query.filter_by(module="chemical", screen="add").one()
        original_id = perm.id
        grants_before = RolePermission.query.filter_by(permission_id=perm.id).count()

        perm.action = "access"
        db.session.commit()

        seed_default_permissions()

        migrated = Permission.query.filter_by(module="chemical", screen="add").one()
        self.assertEqual(migrated.id, original_id)
        self.assertEqual(migrated.action, "create")
        self.assertEqual(
            RolePermission.query.filter_by(permission_id=original_id).count(),
            grants_before,
        )

    def test_seed_does_not_regrant_a_deliberate_revoke(self):
        self._revoke("operator", "chemical", "add")
        seed_default_permissions()
        self.assertFalse(has_permission("operator", "chemical", "add"))

    def test_seed_creates_screens_added_after_first_boot(self):
        """The old guard (`if Permission.query.count() == 0`) meant a new screen
        was never seeded, and fail-open then made it world-accessible."""
        perm = Permission.query.filter_by(module="stickers", screen="batch").one()
        db.session.delete(perm)
        db.session.commit()
        self.assertFalse(has_permission("operator", "stickers", "batch"))

        seed_default_permissions()

        self.assertIsNotNone(
            Permission.query.filter_by(module="stickers", screen="batch").first()
        )

    def test_every_taxonomy_screen_is_seeded(self):
        for module, screens in MODULES.items():
            for screen, action in screens.items():
                perm = Permission.query.filter_by(module=module, screen=screen).first()
                self.assertIsNotNone(perm, f"{module}.{screen} not seeded")
                self.assertEqual(perm.action, action)

    # --- route enforcement ----------------------------------------------

    def test_viewer_denied_write_routes(self):
        self._login("viewer")
        for url in ("/chemical/add", "/mechanical/add", "/stages/add"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("/", resp.headers["Location"])

    def test_operator_allowed_then_denied_after_revoke(self):
        self._login("operator")
        self.assertEqual(self.client.get("/chemical/add").status_code, 200)

        self._revoke("operator", "chemical", "add")
        self.assertEqual(self.client.get("/chemical/add").status_code, 302)

    def test_admin_routes_are_now_governed_by_the_matrix(self):
        """The core bug: admin.* rows were decorative because every admin route
        used a raw is_admin check."""
        self._login("admin")
        self.assertEqual(self.client.get("/admin/users").status_code, 200)

        self._revoke("admin", "admin", "users_list")
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

    def test_supervisor_denied_admin_routes_by_default(self):
        self._login("supervisor")
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

    def test_previously_ungated_admin_routes_are_gated(self):
        self._login("viewer")
        self.assertEqual(self.client.get("/admin/api/sticker-settings").status_code, 302)
        self.assertEqual(self.client.get("/admin/molds/by-diameter/800").status_code, 302)

    def test_viewer_cannot_create_kpi_via_direct_post(self):
        """Viewer holds the analytics.kpis READ key; the mutation must sit
        behind a separate write key."""
        self._login("viewer")
        resp = self.client.post("/analytics/kpis/add", data={"name": "x"})
        self.assertEqual(resp.status_code, 302)

    # --- owner tier -------------------------------------------------------

    def test_only_owner_reaches_the_permission_matrix(self):
        self._login("admin")
        self.assertEqual(self.client.get("/admin/permissions").status_code, 302)

        self._login("super_admin")
        self.assertEqual(self.client.get("/admin/permissions").status_code, 200)

    def test_admin_cannot_toggle_permissions(self):
        self._login("admin")
        resp = self.client.post(
            "/admin/permissions/toggle",
            data={"role": "viewer", "module": "chemical", "screen": "add"},
        )
        self.assertEqual(resp.status_code, 302)

    def test_owner_role_cannot_be_toggled(self):
        self._login("super_admin")
        resp = self.client.post(
            "/admin/permissions/toggle",
            data={"role": "super_admin", "module": "chemical", "screen": "add"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["success"])

    def test_matrix_page_renders_a_csrf_token_for_the_toggle_fetch(self):
        """The toggle JS reads meta[name=csrf-token]. Without it the header is
        empty and CSRFProtect 400s every write - the matrix silently never saved.
        """
        self.app.config["WTF_CSRF_ENABLED"] = True
        self._login("super_admin")
        html = self.client.get("/admin/permissions").get_data(as_text=True)
        self.assertRegex(html, r'<meta name="csrf-token" content="[^"]+"')

    def test_toggle_round_trips(self):
        self._login("super_admin")
        payload = {"role": "viewer", "module": "chemical", "screen": "add"}

        resp = self.client.post("/admin/permissions/toggle", data=payload)
        self.assertTrue(resp.get_json()["granted"])
        self.assertTrue(has_permission("viewer", "chemical", "add"))

        resp = self.client.post("/admin/permissions/toggle", data=payload)
        self.assertFalse(resp.get_json()["granted"])
        self.assertFalse(has_permission("viewer", "chemical", "add"))

    def test_admin_cannot_delete_the_owner(self):
        self._login("admin")
        owner_id = self.users["super_admin"].id
        resp = self.client.post(f"/admin/users/{owner_id}/delete")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNotNone(db.session.get(User, owner_id))

    def test_admin_cannot_deactivate_the_owner(self):
        self._login("admin")
        owner_id = self.users["super_admin"].id
        resp = self.client.post(f"/admin/users/{owner_id}/toggle-active")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(db.session.get(User, owner_id).is_active)

    def test_admin_cannot_demote_the_owner(self):
        self._login("admin")
        owner_id = self.users["super_admin"].id
        self.client.post(
            f"/admin/users/{owner_id}/edit",
            data={"role": "viewer", "is_active": "on", "full_name": "x"},
        )
        self.assertEqual(db.session.get(User, owner_id).role, User.ROLE_SUPER_ADMIN)

    def test_last_owner_cannot_demote_themselves(self):
        self._login("super_admin")
        owner_id = self.users["super_admin"].id
        self.client.post(
            f"/admin/users/{owner_id}/edit",
            data={"role": "admin", "is_active": "on", "full_name": "x"},
        )
        self.assertEqual(db.session.get(User, owner_id).role, User.ROLE_SUPER_ADMIN)

    def test_admin_cannot_assign_the_owner_role(self):
        self._login("admin")
        self.client.post(
            "/admin/users/new",
            data={
                "username": "usurper",
                "password": "secret1",
                "role": User.ROLE_SUPER_ADMIN,
            },
        )
        self.assertIsNone(User.query.filter_by(username="usurper").first())

    def test_legacy_admin_is_promoted_to_owner(self):
        User.query.filter_by(role=User.ROLE_SUPER_ADMIN).delete()
        db.session.commit()
        self.assertIsNone(User.query.filter_by(role=User.ROLE_SUPER_ADMIN).first())

        User.create_default_owner()

        owner = User.query.filter_by(role=User.ROLE_SUPER_ADMIN).one()
        self.assertEqual(owner.username, "admin")

    # --- sidebar reflects the matrix -------------------------------------

    def test_sidebar_hides_revoked_screens(self):
        self._login("operator")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('href="/chemical/add"', html)

        self._revoke("operator", "chemical", "add")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn('href="/chemical/add"', html)

    def test_sidebar_hides_admin_section_from_viewer(self):
        self._login("viewer")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn('href="/admin/users"', html)

    def test_permissions_link_is_owner_only(self):
        self._login("admin")
        self.assertNotIn(
            'href="/admin/permissions"',
            self.client.get("/dashboard").get_data(as_text=True),
        )
        self._login("super_admin")
        self.assertIn(
            'href="/admin/permissions"',
            self.client.get("/dashboard").get_data(as_text=True),
        )

    # --- matrix shape -----------------------------------------------------

    def test_matrix_is_grouped_and_carries_action_labels(self):
        matrix, roles = get_permission_matrix()
        self.assertEqual(roles[0], User.ROLE_SUPER_ADMIN)
        self.assertIn("chemical", matrix)

        rows = {r["screen"]: r for r in matrix["chemical"]}
        self.assertEqual(rows["list"]["action_label"], "Read")
        self.assertEqual(rows["add"]["action_label"], "Create")
        self.assertEqual(rows["edit"]["action_label"], "Write")

        # The owner column is always on, whatever the table says.
        self.assertTrue(rows["add"]["roles"][User.ROLE_SUPER_ADMIN])


if __name__ == "__main__":
    unittest.main()
