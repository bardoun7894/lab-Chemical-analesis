"""Ovality (Annealing) and Visual (Finish) numbers on the pipe detail page.

Both JSON profiles were only ever rendered inside the edit-form popup, so a
supervisor reading /stages/<id> could see CCM dimensions and coating thickness
but never the ovality X/Y/% or the visual checklist. The detail page now
mirrors them read-only, the same way dimension_profile is mirrored.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User


class DetailOvalityVisualTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.pipe = Pipe(
            production_date=date(2026, 8, 20), ladle_id="DOV1",
            pipe_code="DOV1-P1", no_code="DOV1N1", arrange_pipe=1,
            diameter=300, pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

        annealing = ProductionStage.name_for_code("annealing")
        finish = ProductionStage.name_for_code("finish")

        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=annealing,
            ovality_profile={"points": {
                "ID": {"x": 101.5, "y": 99.5, "ovality": 1.0},
                "D3": {"x": 200.0, "y": 190.0, "ovality": 2.56},
            }},
        ))
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=finish,
            visual_profile={"marking": True, "ovality": False,
                            "straightness": True, "internal_finish": False,
                            "external_finish": True},
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _get_detail(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        resp = self.client.get(f"/stages/{self.pipe.id}")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_ovality_numbers_rendered(self):
        html = self._get_detail()
        for token in ("101.5", "99.5", "1.0%", "200.0", "190.0", "2.56%"):
            self.assertIn(token, html, f"missing ovality value {token}")
        # Columns are the symbols DN300 defines, not an invented D1-D15 run.
        self.assertIn(">d1<", html)
        self.assertIn(">S1<", html)
        self.assertNotIn(">D15<", html)
        # A reading saved under a retired D-column keeps its own column, or the
        # upgrade would read as data loss on every pipe annealed before it.
        self.assertIn(">D3<", html)
        self.assertIn("Ovality = (X - Y) / (X + Y)", html)

    def test_visual_checklist_rendered(self):
        html = self._get_detail()
        for label in ("Marking", "Straightness", "Internal finish",
                      "External finish"):
            self.assertIn(label, html)
        self.assertIn("bi-check-circle-fill text-success", html)
        self.assertIn("bi-x-circle text-danger", html)

    def test_absent_profiles_render_nothing(self):
        PipeStage.query.delete()
        db.session.commit()
        html = self._get_detail()
        self.assertNotIn("Ovality = (X - Y) / (X + Y)", html)


if __name__ == "__main__":
    unittest.main()
