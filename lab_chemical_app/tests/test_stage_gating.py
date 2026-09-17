"""Stage gating (§4.3): WAITING and FROZEN freeze post-Lab production stages."""

from datetime import date
import unittest

from app import create_app, db
from app.models.pipe import Pipe
from app.models.user import User
from app.models.permission import seed_default_permissions


class StageGatingTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()
        # supervisor user with edit rights
        u = User(username="sup", full_name="Sup", role="admin")
        if hasattr(u, "set_password"):
            u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, lab_decision):
        p = Pipe(production_date=date(2026, 4, 14), ladle_id="G1",
                 pipe_code="G1-P1", no_code="G1N1", arrange_pipe=1,
                 diameter=300, pipe_class="K9", lab_decision=lab_decision)
        db.session.add(p)
        db.session.commit()
        return p

    def _post(self, pipe_id, stage_name):
        return self.client.post(
            f"/stages/{pipe_id}/stage/{stage_name}",
            data={"decision": "Accept"},
        )

    def test_frozen_blocks_post_lab_stage(self):
        self._login()
        p = self._pipe("FROZEN")
        r = self._post(p.id, "Zinc")
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("FROZEN", r.get_data(as_text=True))

    def test_waiting_blocks_post_lab_but_allows_lab(self):
        self._login()
        p = self._pipe("WAITING")
        r_zinc = self._post(p.id, "Zinc")
        self.assertEqual(r_zinc.status_code, 400, r_zinc.get_data(as_text=True))
        # Lab stage must still be allowed under WAITING (not gated)
        r_lab = self._post(p.id, "Lab Approval")
        self.assertNotEqual(
            r_lab.status_code, 400,
            "Lab Approval stage must be permitted while WAITING: " + r_lab.get_data(as_text=True),
        )

    def _post_json(self, pipe_id, stage_name, decision="Accept"):
        # The real UI posts JSON; update_stage requires application/json.
        return self.client.post(
            f"/stages/{pipe_id}/stage/{stage_name}",
            json={"decision": decision},
        )

    def test_lab_approval_stage_is_editable(self):
        """The single 'Lab Approval' stage (built-in code lab) is recordable."""
        self._login()
        p = self._pipe("ACCEPT")
        r = self._post_json(p.id, "Lab Approval", "Accept")
        self.assertNotEqual(
            r.status_code, 400,
            "Lab Approval stage must be recordable: " + r.get_data(as_text=True),
        )


if __name__ == "__main__":
    unittest.main()
