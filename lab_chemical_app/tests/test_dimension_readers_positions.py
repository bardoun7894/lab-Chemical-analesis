"""Every reader of dimension_profile must handle the positions shape.

Storage moved from a flat {"samples": {"S1": [1..21]}} to
{"positions": {"1": [r1, r2, r3], ...}} on 2026-08-22, but the detail page,
the Stage Measurements report and its Excel flattener were still reading
"samples" — so a pipe saved after the change showed no thickness at all in any
of them. Both shapes must render, and the TA 1012 symbol readings with them.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


POSITIONS_PROFILE = {
    "thickness": {"positions": {
        "1": [7.21, 7.22, 7.23],
        "5.5": [7.31, 7.32, 7.33],
        "6": [7.41, 7.42, 7.43],
    }},
    "diameter": {"samples": {"S1": [326.11, 326.12]}},
    "symbols": {"d1": 326.44, "S1": 7.55},
}

LEGACY_PROFILE = {
    "thickness": {"samples": {"S1": [float(f"9.{n:02d}") for n in range(1, 22)]}},
    "diameter": {"samples": {"S1": [325.99]}},
}


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

        u = User(username="admin", full_name="Admin", role="admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id
        self.ccm = ProductionStage.name_for_code("ccm")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe_with(self, profile, code="DR1"):
        pipe = Pipe(production_date=date(2026, 8, 22), ladle_id=code,
                    pipe_code=f"{code}-P1", no_code=f"{code}N1",
                    arrange_pipe=1, diameter=300, pipe_class="K9")
        db.session.add(pipe)
        db.session.commit()
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.ccm,
                                 dimension_profile=profile))
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class DetailPageTest(_Base):
    def test_positions_profile_shows_its_readings(self):
        pipe = self._pipe_with(POSITIONS_PROFILE)
        self._login()
        html = self.client.get(f"/stages/{pipe.id}").get_data(as_text=True)
        for token in ("7.21", "7.33", "7.43"):
            self.assertIn(token, html, f"reading {token} missing from detail")

    def test_positions_are_labelled_not_numbered_1_to_21(self):
        pipe = self._pipe_with(POSITIONS_PROFILE)
        self._login()
        html = self.client.get(f"/stages/{pipe.id}").get_data(as_text=True)
        self.assertIn("5.5", html)

    def test_legacy_profile_still_shows_its_readings(self):
        pipe = self._pipe_with(LEGACY_PROFILE, code="DR2")
        self._login()
        html = self.client.get(f"/stages/{pipe.id}").get_data(as_text=True)
        self.assertIn("9.01", html)
        self.assertIn("9.21", html)

    def test_symbol_readings_and_deviation_are_shown(self):
        pipe = self._pipe_with(POSITIONS_PROFILE)
        self._login()
        html = self.client.get(f"/stages/{pipe.id}").get_data(as_text=True)
        self.assertIn("326.44", html)
        self.assertIn("0.44", html)  # deviation from the DN300 d1 nominal 326


class StageMeasurementsReportTest(_Base):
    def test_html_shows_position_readings(self):
        self._pipe_with(POSITIONS_PROFILE)
        self._login()
        html = self.client.get(
            "/reports/stage-measurements").get_data(as_text=True)
        self.assertIn("7.21", html)
        self.assertIn("7.43", html)

    def test_html_shows_legacy_readings(self):
        self._pipe_with(LEGACY_PROFILE, code="DR2")
        self._login()
        html = self.client.get(
            "/reports/stage-measurements").get_data(as_text=True)
        self.assertIn("9.01", html)

    def test_export_flattens_positions(self):
        self._pipe_with(POSITIONS_PROFILE)
        flat = [r["dimension_profile"]
                for r in analytics_service.stage_measurements({})["export_rows"]
                if r["dimension_profile"]]
        self.assertTrue(flat, "no dimension_profile in export_rows")
        text = flat[0]
        self.assertIn("7.21", text)
        self.assertIn("5.5", text, "position labels must survive the export")
        self.assertIn("7.43", text)

    def test_export_flattens_legacy(self):
        self._pipe_with(LEGACY_PROFILE, code="DR2")
        flat = [r["dimension_profile"]
                for r in analytics_service.stage_measurements({})["export_rows"]
                if r["dimension_profile"]]
        self.assertIn("9.01", flat[0])

    def test_export_includes_symbol_readings(self):
        self._pipe_with(POSITIONS_PROFILE)
        rows = analytics_service.stage_measurements({})["export_rows"]
        text = " ".join(str(r.get("dimension_profile", "")) for r in rows)
        self.assertIn("d1", text)
        self.assertIn("326.44", text)

    def test_every_exported_cell_stays_scalar(self):
        self._pipe_with(POSITIONS_PROFILE)
        for r in analytics_service.stage_measurements({})["export_rows"]:
            for k, v in r.items():
                self.assertNotIsInstance(v, (dict, list), f"{k} is not scalar")


if __name__ == "__main__":
    unittest.main()
