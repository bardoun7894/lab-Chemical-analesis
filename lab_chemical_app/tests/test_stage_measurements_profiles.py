"""Stage Measurements report shows the JSON profile numbers, not a ✓ flag.

The report flagged thickness_profile / dimension_profile as "yes" and ignored
ovality_profile / visual_profile entirely, so the numbers an operator typed in
the popups were unreadable outside the edit form. Rows now carry the raw JSON
(for the HTML grids) and export_rows carry a flattened string per profile,
since xlsxwriter cannot write a dict into a cell.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


class StageMeasurementProfilesTest(unittest.TestCase):
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

        pipe = Pipe(
            production_date=date(2026, 8, 20), ladle_id="SM1",
            pipe_code="SM1-P1", no_code="SM1N1", arrange_pipe=1,
            diameter=300, pipe_class="K9",
        )
        db.session.add(pipe)
        db.session.commit()
        self.pipe = pipe

        db.session.add(PipeStage(
            pipe_id=pipe.id,
            stage_name=ProductionStage.name_for_code("annealing"),
            ovality_profile={"points": {
                "ID": {"x": 101.5, "y": 99.5, "ovality": 1.0},
                "D3": {"x": 200.0, "y": 190.0, "ovality": 2.56},
            }},
        ))
        db.session.add(PipeStage(
            pipe_id=pipe.id,
            stage_name=ProductionStage.name_for_code("finish"),
            visual_profile={"marking": True, "ovality": False,
                            "straightness": True, "internal_finish": False,
                            "external_finish": True},
        ))
        db.session.add(PipeStage(
            pipe_id=pipe.id,
            stage_name=ProductionStage.name_for_code("coating"),
            thickness_profile={"cement": [3.5, 3.9, 3.7, 3.2, 3.5, 3.6],
                               "coating": [70.0, 70.4, 69.2, 69.0, 70.1, 70.0]},
        ))
        db.session.add(PipeStage(
            pipe_id=pipe.id,
            stage_name=ProductionStage.name_for_code("ccm"),
            dimension_profile={"thickness": {"samples": {"S1": [5.1, 5.0]}},
                               "diameter": {"samples": {"S1": [35.0, 137.0]}}},
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_ovality_and_visual_rows_are_included(self):
        data = analytics_service.stage_measurements({})
        stages = {r["stage"] for r in data["rows"]}
        self.assertIn(ProductionStage.name_for_code("annealing"), stages)
        self.assertIn(ProductionStage.name_for_code("finish"), stages)
        self.assertEqual(data["summary"]["measured_rows"], 4)

    def test_rows_carry_raw_json(self):
        data = analytics_service.stage_measurements({})
        ann = next(r for r in data["rows"] if r["ovality_profile"])
        self.assertEqual(ann["ovality_profile"]["points"]["ID"]["x"], 101.5)

    def test_export_rows_flatten_every_profile_to_a_string(self):
        data = analytics_service.stage_measurements({})
        flat = {}
        for r in data["export_rows"]:
            for key in ("ovality_profile", "visual_profile",
                        "thickness_profile", "dimension_profile"):
                if r[key]:
                    flat[key] = r[key]
        self.assertIn("ID X=101.5 Y=99.5 1%", flat["ovality_profile"])
        self.assertIn("D3 X=200 Y=190 2.56%", flat["ovality_profile"])
        self.assertIn("marking: yes", flat["visual_profile"])
        self.assertIn("ovality: no", flat["visual_profile"])
        self.assertIn("cement: 3.5/3.9/3.7/3.2/3.5/3.6",
                      flat["thickness_profile"])
        # Thickness exports per position (metre) since 2026-08-22; a legacy
        # flat row folds into position 1.
        self.assertIn("thickness 1m: 5.1,5", flat["dimension_profile"])
        # Every exported cell must be a scalar — xlsxwriter cannot write dicts.
        for r in data["export_rows"]:
            for k, v in r.items():
                self.assertNotIsInstance(v, (dict, list), f"{k} is not scalar")

    def test_html_renders_the_numbers(self):
        self._login()
        resp = self.client.get("/reports/stage-measurements")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("101.5", html)
        self.assertIn("2.56%", html)
        self.assertIn("70.4", html)
        self.assertIn("bi-check-circle-fill text-success", html)

    def test_xlsx_export_succeeds(self):
        self._login()
        resp = self.client.get("/reports/export/stage-measurements.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(len(resp.data) > 0)


if __name__ == "__main__":
    unittest.main()
