"""The CCM dimension grid no longer has a "Standard" thickness row
(removed per client 2026-07-26): the popup renders samples only, the
reader no longer collects dim_thick_standard_* inputs, and the pipe
detail page shows no Std row.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User


class CcmGridNoStandardRowTestCase(unittest.TestCase):
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
            pipe_code="G1-P1", no_code="G0001", arrange_pipe=1,
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

    def test_edit_form_grid_has_no_editable_standard_row(self):
        """What was removed in 2026-07-26 is an operator-typed Standard row
        that got saved into the profile. The grid does show a read-only
        Standard reference from Settings since 2026-08-23 — that is the
        opposite thing: nobody types it, and nothing collects it."""
        self._login()
        html = self.client.get(f"/stages/{self.pipe.id}/edit").get_data(as_text=True)
        self.assertNotIn("dim_thick_standard", html)
        self.assertNotIn('name="stage_CCM_dim_thick_standard_1"', html)

    def test_reader_ignores_standard_and_keeps_samples(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/edit",
            data={
                "no_code": "G0001", "production_date": "2026-04-14",
                "shift": "1", "diameter": "300", "pipe_class": "K9",
                "arrange_pipe": "1",
                "stage_CCM_dim_thick_standard_1": "9.9",   # legacy input — ignored
                "stage_CCM_dim_thick_S1_1": "7.5",
                "stage_CCM_dim_dia_S1_1": "300",
            },
        )
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True))
        stage = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name="CCM"
        ).one()
        profile = stage.dimension_profile
        self.assertNotIn("standard", profile.get("thickness", {}))
        # Legacy flat cell 1 lands at position "1", reading 1.
        self.assertEqual(profile["thickness"]["positions"]["1"][0], 7.5)
        self.assertEqual(profile["diameter"]["samples"]["S1"][0], 300.0)

    def test_detail_page_shows_no_std_row(self):
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name="CCM",
            dimension_profile={
                "thickness": {
                    "standard": [9.9] + [None] * 20,  # legacy saved data
                    "samples": {"S1": [7.5] + [None] * 20},
                },
                "diameter": {"samples": {}},
            },
        ))
        db.session.commit()
        html = self.client.get(f"/stages/{self.pipe.id}").get_data(as_text=True)
        self.assertNotIn(">Std<", html)
        self.assertIn("7.5", html)


if __name__ == "__main__":
    unittest.main()
