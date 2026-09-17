"""Coating thickness popup in the Stage Console (follow-up to the CCM
Dimensions console popup — the operator enters data from /stages/console,
where neither popup existed).

Same pattern as the CCM modal: the cement/coating per-meter grid lives
INSIDE the Coating stage form so the console autosave serializes the
cells; update_stage parses stage_<Coating>_thick_<layer>_<m> keys from
the JSON payload.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User


class ConsoleCoatingThicknessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.pipe = Pipe(
            production_date=date(2026, 4, 14), ladle_id="G1",
            pipe_code="G1-P1", no_code="G1N1", arrange_pipe=1,
            diameter=300, pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _coating(self):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name="Coating"
        ).first()

    def test_console_pane_has_thickness_modal_inside_coating_form(self):
        self._login()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn('id="consoleCoatingThicknessModal"', html)
        # cement is X/Y at the socket and the spigot; coating is per metre
        self.assertIn('name="stage_Coating_thick_cement_pt_spigot_4_y"', html)
        self.assertIn('name="stage_Coating_thick_coating_6"', html)
        # modal must sit inside the Coating stage form so autosave serializes it
        coating_form = html.split('data-stage="Coating"', 1)[1]
        self.assertIn(
            'id="consoleCoatingThicknessModal"', coating_form.split("</form>", 1)[0]
        )

    def test_update_stage_saves_thickness_profile_from_console(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/Coating",
            json={
                "decision": "Accept",
                "stage_Coating_thick_cement_pt_socket_1_x": "9.5",
                "stage_Coating_thick_cement_pt_spigot_4_x": "10.1",
                "stage_Coating_thick_coating_3": "0.35",
            },
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        profile = self._coating().thickness_profile
        # cement is X/Y at the socket and the spigot; coating is per metre
        self.assertEqual(profile["cement_points"]["socket_1"]["x"], 9.5)
        self.assertEqual(profile["cement_points"]["spigot_4"]["x"], 10.1)
        # a position this post never mentioned is not invented
        self.assertNotIn("socket_2", profile["cement_points"])
        self.assertEqual(profile["coating"][2], 0.35)

    def test_update_stage_resave_without_thickness_preserves_profile(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/Coating",
            json={"decision": "Accept", "stage_Coating_thick_cement_pt_socket_1_x": "9.5"},
        )
        before = self._coating().thickness_profile

        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/Coating",
            json={"notes": "checked"},  # no thick keys — must not wipe the grid
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        stage = self._coating()
        self.assertEqual(stage.thickness_profile, before)
        self.assertEqual(stage.notes, "checked")

    def test_console_save_preserves_standard_band(self):
        """A console save that carries readings but no Min/Nominal/Max must not
        drop the band — until 2026-08-29 it nulled the limits the order's spec
        had filled in, and the readings stopped being judged against anything.
        """
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="Coating",
            thickness_profile={
                "cement": [None] * 6, "coating": [None] * 6,
                "cement_std": 5.0, "cement_std_min": 4.0, "cement_std_max": 6.0,
                "coating_std": 70.0, "coating_std_min": 60.0, "coating_std_max": 80.0,
            },
        ))
        db.session.commit()

        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/Coating",
            json={"decision": "Accept", "stage_Coating_thick_cement_pt_socket_1_x": "9.5"},
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))

        profile = self._coating().thickness_profile
        self.assertEqual(profile["cement_points"]["socket_1"]["x"], 9.5)
        self.assertEqual(profile["cement_std"], 5.0)
        self.assertEqual(profile["cement_std_min"], 4.0)
        self.assertEqual(profile["cement_std_max"], 6.0)
        self.assertEqual(profile["coating_std"], 70.0)

    def test_band_posted_blank_still_clears(self):
        """Present-and-blank is a real clear — only an absent input is 'no
        opinion'. The screen that renders the band must be able to empty it."""
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="Coating",
            thickness_profile={"cement": [None] * 6, "coating": [None] * 6,
                               "cement_std": 5.0},
        ))
        db.session.commit()

        self.client.post(
            f"/stages/{self.pipe.id}/stage/Coating",
            json={"stage_Coating_thick_cement_pt_socket_1_x": "9.5",
                  "stage_Coating_thick_cement_std": ""},
        )
        self.assertIsNone(self._coating().thickness_profile["cement_std"])

    def test_console_pane_renders_the_band(self):
        self._login()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn('name="stage_Coating_thick_cement_std_min"', html)
        self.assertIn('name="stage_Coating_thick_coating_std_max"', html)
        self.assertIn('data-cmp="coating"', html)

    def test_console_pane_prefills_saved_thickness(self):
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="Coating",
            thickness_profile={
                "cement_points": {"socket_1": {"x": 9.5, "y": 9.1,
                                               "diff": 0.4, "avg": 9.3}},
                "cement_avg": 9.3,
                "coating": [None, None, 0.35, None, None, None],
            },
        ))
        db.session.commit()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn('name="stage_Coating_thick_cement_pt_socket_1_x"', html)
        self.assertIn("9.5", html)
        self.assertIn("0.35", html)


if __name__ == "__main__":
    unittest.main()
