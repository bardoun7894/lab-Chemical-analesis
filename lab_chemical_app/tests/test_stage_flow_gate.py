"""Sequential stage gating: a decision needs the previous stage accepted.

Off by default — the app shipped with exactly one gate (Zinc, after Lab
Approval) carrying a comment not to generalise it, so turning this on has to
be a deliberate act rather than a silent behaviour change.
"""

import json
import os
import shutil
import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes import admin as admin_routes
from app.services import stage_flow_service


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

        self.store = admin_routes.APP_SETTINGS_PATH
        self.backup = self.store + ".flowbak"
        if os.path.exists(self.store):
            shutil.copy2(self.store, self.backup)

        self.names = ProductionStage.active_names()
        self.first, self.second = self.names[0], self.names[1]

        self.pipe = Pipe(production_date=date(2026, 8, 23), ladle_id="F1",
                         pipe_code="F1-P1", no_code="F1N1", arrange_pipe=1,
                         diameter=300, pipe_class="K9")
        db.session.add(self.pipe)
        db.session.commit()

    def tearDown(self):
        if os.path.exists(self.backup):
            shutil.move(self.backup, self.store)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _set_enabled(self, on):
        settings = admin_routes.load_app_settings()
        settings.setdefault(stage_flow_service.SETTINGS_KEY, {})[
            stage_flow_service.REQUIRE_PREVIOUS] = on
        admin_routes.save_app_settings(settings)

    def _stage(self, name, decision=None):
        st = PipeStage(pipe_id=self.pipe.id, stage_name=name, decision=decision)
        db.session.add(st)
        db.session.commit()
        db.session.refresh(self.pipe)
        return st

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class DefaultOffTest(_Base):
    def test_disabled_by_default(self):
        self.assertFalse(stage_flow_service.is_enabled({}))

    def test_nothing_is_blocked_while_off(self):
        self._set_enabled(False)
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, self.second, "Accept"))


class RuleTest(_Base):
    def setUp(self):
        super().setUp()
        self._set_enabled(True)

    def test_first_stage_is_never_blocked(self):
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, self.first, "Accept"))

    def test_blocked_when_the_previous_stage_has_no_decision(self):
        reason = stage_flow_service.blocking_reason(
            self.pipe, self.second, "Accept")
        self.assertIsNotNone(reason)
        self.assertIn(self.first, reason)

    def test_allowed_once_the_previous_stage_is_accepted(self):
        self._stage(self.first, "Accept")
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, self.second, "Accept"))

    def test_a_rejected_predecessor_blocks(self):
        self._stage(self.first, "Reject")
        reason = stage_flow_service.blocking_reason(
            self.pipe, self.second, "Accept")
        self.assertIn("مرفوضة", reason)

    def test_hold_is_not_an_acceptance(self):
        self._stage(self.first, "Hold")
        self.assertIsNotNone(stage_flow_service.blocking_reason(
            self.pipe, self.second, "Accept"))

    def test_a_save_with_no_decision_passes(self):
        """Measurements and notes stay open — blocking them would push the
        shop floor back to paper."""
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, self.second, None))
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, self.second, ""))

    def test_previous_stage_follows_stage_management_order(self):
        self.assertIsNone(stage_flow_service.previous_stage(self.first))
        self.assertEqual(stage_flow_service.previous_stage(self.second),
                         self.first)

    def test_an_unknown_stage_name_is_not_gated(self):
        self.assertIsNone(stage_flow_service.previous_stage("Nope"))
        self.assertIsNone(stage_flow_service.blocking_reason(
            self.pipe, "Nope", "Accept"))


class ConsoleTest(_Base):
    def test_console_refuses_an_out_of_order_decision(self):
        self._set_enabled(True)
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.second}",
            json={"decision": "Accept"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["success"])
        self.assertIn(self.first, resp.get_json()["error"])

    def test_console_allows_it_once_the_previous_is_accepted(self):
        self._set_enabled(True)
        self._stage(self.first, "Accept")
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.second}",
            json={"decision": "Accept"})
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))

    def test_console_is_unaffected_while_the_setting_is_off(self):
        self._set_enabled(False)
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.second}",
            json={"decision": "Accept"})
        self.assertTrue(resp.get_json()["success"])


class SettingsScreenTest(_Base):
    def test_screen_renders_and_lists_the_order(self):
        self._login()
        html = self.client.get(
            "/admin/settings/stage-flow").get_data(as_text=True)
        self.assertEqual(html.count("require_previous_approval") > 0, True)
        self.assertIn(self.first, html)

    def test_toggling_persists(self):
        self._login()
        self.client.post("/admin/settings/stage-flow",
                         data={"require_previous_approval": "1"})
        self.assertTrue(stage_flow_service.is_enabled())
        self.client.post("/admin/settings/stage-flow", data={})
        self.assertFalse(stage_flow_service.is_enabled())

    def test_linked_from_settings(self):
        self._login()
        html = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("/admin/settings/stage-flow", html)


if __name__ == "__main__":
    unittest.main()
