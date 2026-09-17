"""CCM Dimensions popup in the Stage Console (client request 2026-08-09 —
the operator enters data from /stages/console, where the popup did not
exist).

The modal lives INSIDE the CCM stage form so the console autosave
serializes the dimension cells with the rest of the row; update_stage
parses the stage_CCM_dim_* keys from the JSON payload.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User


class ConsoleCcmDimensionsTestCase(unittest.TestCase):
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

        # pipes.ladle_id is a foreign key to chemical_analyses.ladle_id, so the
        # ladle has to exist before the pipe. SQLite never enforced it.
        db.session.add(
            ChemicalAnalysis(test_date=date(2026, 4, 14), ladle_no=1, ladle_id="G1")
        )
        db.session.commit()

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

    def _ccm(self):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name="CCM"
        ).first()

    def test_console_pane_has_dimension_modal_inside_ccm_form(self):
        self._login()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn('id="consoleCcmDimensionModal"', html)
        self.assertIn('id="consoleCcmAddSample"', html)
        # 7 thickness positions x 3 readings + 15 diameter points for S1
        self.assertIn('name="stage_CCM_dim_thick_p1_1"', html)
        self.assertIn('name="stage_CCM_dim_thick_p7_3"', html)
        self.assertIn('name="stage_CCM_dim_dia_S1_15"', html)
        # modal must sit inside the CCM stage form so autosave serializes it
        ccm_form = html.split('data-stage="CCM"', 1)[1]
        self.assertIn('id="consoleCcmDimensionModal"', ccm_form.split("</form>", 1)[0])

    def test_update_stage_saves_dimension_profile_from_console(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/CCM",
            json={
                "decision": "Accept",
                "stage_CCM_dim_thick_S1_1": "7.5",
                "stage_CCM_dim_thick_S1_21": "8.1",
                "stage_CCM_dim_dia_S1_1": "300",
                "stage_CCM_dim_dia_S2_15": "299.5",
            },
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        profile = self._ccm().dimension_profile
        # Thickness is stored per position since 2026-08-22; a console payload
        # in the legacy flat shape is folded into it, cell 1 -> position "1"
        # reading 1 and cell 21 -> position "6" reading 3.
        thick = profile["thickness"]["positions"]
        dia = profile["diameter"]["samples"]
        self.assertEqual(thick["1"][0], 7.5)
        self.assertEqual(thick["6"][2], 8.1)
        self.assertIsNone(thick["4"][1])  # untouched cells stay empty
        self.assertEqual(dia["S1"][0], 300.0)
        self.assertEqual(dia["S2"][14], 299.5)

    def test_update_stage_resave_without_dims_preserves_profile(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/CCM",
            json={"decision": "Accept", "stage_CCM_dim_thick_S1_1": "7.5"},
        )
        before = self._ccm().dimension_profile

        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/CCM",
            json={"notes": "checked"},  # no dim keys — must not wipe the grid
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        stage = self._ccm()
        self.assertEqual(stage.dimension_profile, before)
        self.assertEqual(stage.notes, "checked")

    def test_console_pane_prefills_saved_dimensions(self):
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="CCM",
            dimension_profile={
                "thickness": {"samples": {"S1": [7.5] + [None] * 20}},
                "diameter": {"samples": {"S1": [300.0] + [None] * 14}},
            },
        ))
        db.session.commit()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        # A legacy flat profile still renders its numbers, folded into the
        # position grid (cell 1 -> position "1", reading 1).
        self.assertIn('name="stage_CCM_dim_thick_p1_1"', html)
        self.assertIn("7.5", html)
        self.assertIn("300", html)


    def test_console_save_preserves_thickness_standard_band(self):
        """The Min/Nominal/Max band lives in the same section as the readings,
        so rewriting the section from a screen that does not render the band
        used to drop it."""
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="CCM",
            dimension_profile={
                "thickness": {"positions": {}, "standard": 9.0,
                              "standard_min": 8.5, "standard_max": 9.5},
            },
        ))
        db.session.commit()

        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/CCM",
            json={"decision": "Accept", "stage_CCM_dim_thick_p1_1": "9.2"},
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))

        thick = self._ccm().dimension_profile["thickness"]
        self.assertEqual(thick["positions"]["1"][0], 9.2)
        self.assertEqual(thick["standard"], 9.0)
        self.assertEqual(thick["standard_min"], 8.5)
        self.assertEqual(thick["standard_max"], 9.5)

    def test_console_pane_renders_the_thickness_band(self):
        self._login()
        html = self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn('name="stage_CCM_dim_thick_std_min"', html)
        self.assertIn('name="stage_CCM_dim_thick_std_max"', html)
        self.assertIn('data-cmp="S1"', html)


if __name__ == "__main__":
    unittest.main()
