"""Settings > Dimension Standards — completing the transcribed TA 1012 table.

The nominals were read off a scan; several tolerance cells were unreadable and
left blank. This screen is where they get filled in and the document marked
verified, so the operator is never stuck with a symbol that can show a
deviation but never a verdict.
"""

import json
import os
import shutil
import unittest

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.user import User
from app.services import dimension_standard_service as std


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="root", full_name="Root", role="super_admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        # The screen writes to the real config file — keep a copy and restore
        # it so a test run never leaves the standard edited.
        self.backup = std.STANDARDS_FILE + ".testbak"
        shutil.copy2(std.STANDARDS_FILE, self.backup)

    def tearDown(self):
        shutil.move(self.backup, std.STANDARDS_FILE)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _read_file(self):
        with open(std.STANDARDS_FILE, encoding="utf-8") as fh:
            return json.load(fh)


class IndexTest(_Base):
    def test_lists_every_dn_with_its_missing_tolerance_count(self):
        self._login()
        html = self.client.get(
            "/admin/settings/dimension-standards").get_data(as_text=True)
        self.assertIn("DN300", html)
        self.assertIn("DN1000", html)
        self.assertIn("Tolerance missing", html)

    def test_unverified_table_warns(self):
        self._login()
        html = self.client.get(
            "/admin/settings/dimension-standards").get_data(as_text=True)
        self.assertIn("has not been checked yet", html)

    def test_linked_from_settings(self):
        self._login()
        html = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("/admin/settings/dimension-standards", html)


class EditTest(_Base):
    def test_form_prefills_the_saved_values(self):
        self._login()
        html = self.client.get(
            "/admin/settings/dimension-standards/300").get_data(as_text=True)
        self.assertIn('name="nominal_d1"', html)
        self.assertIn('value="326"', html)
        self.assertIn('name="tol_minus_d1"', html)

    def test_symbol_without_tolerance_is_flagged_in_the_form(self):
        self._login()
        html = self.client.get(
            "/admin/settings/dimension-standards/300").get_data(as_text=True)
        self.assertIn("no tolerance", html)

    def test_saving_a_tolerance_makes_the_limits_resolvable(self):
        self.assertIsNone(std.limits_for(300, "S1"))
        self._login()
        resp = self.client.post(
            "/admin/settings/dimension-standards/300",
            data={"nominal_S1": "7.2", "tol_plus_S1": "0.9",
                  "tol_minus_S1": "0.9"},
        )
        self.assertEqual(resp.status_code, 302)
        limits = std.limits_for(300, "S1")
        self.assertAlmostEqual(limits["lsl"], 6.3)
        self.assertAlmostEqual(limits["usl"], 8.1)

    def test_blank_stays_none_and_never_becomes_zero(self):
        self._login()
        self.client.post(
            "/admin/settings/dimension-standards/300",
            data={"nominal_d1": "326", "tol_plus_d1": "1.0",
                  "tol_minus_d1": "3.3", "nominal_r1": "", "tol_plus_r1": ""},
        )
        entry = self._read_file()["by_dn"]["300"]["r1"]
        self.assertIsNone(entry["nominal"])
        self.assertIsNone(entry["tol_plus"])

    def test_unknown_dn_redirects(self):
        self._login()
        resp = self.client.get("/admin/settings/dimension-standards/9999")
        self.assertEqual(resp.status_code, 302)

    def test_editing_one_dn_leaves_the_others_alone(self):
        before = self._read_file()["by_dn"]["800"]
        self._login()
        self.client.post("/admin/settings/dimension-standards/300",
                         data={"nominal_d1": "999"})
        self.assertEqual(self._read_file()["by_dn"]["800"], before)


class VerifyTest(_Base):
    def test_marking_verified_flips_the_flag(self):
        self.assertFalse(std.is_verified())
        self._login()
        resp = self.client.post("/admin/settings/dimension-standards/verify",
                                data={"verified": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(std.is_verified())

    def test_un_verifying_flips_it_back(self):
        self._login()
        self.client.post("/admin/settings/dimension-standards/verify",
                         data={"verified": "1"})
        self.client.post("/admin/settings/dimension-standards/verify",
                         data={"verified": "0"})
        self.assertFalse(std.is_verified())

    def test_verifying_does_not_disturb_the_values(self):
        before = self._read_file()["by_dn"]
        self._login()
        self.client.post("/admin/settings/dimension-standards/verify",
                         data={"verified": "1"})
        self.assertEqual(self._read_file()["by_dn"], before)


if __name__ == "__main__":
    unittest.main()
