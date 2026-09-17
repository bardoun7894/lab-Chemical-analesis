"""One pipe's measurement sheet — raw readings plus statistics."""

import unittest
from datetime import date, time

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import pipe_measurement_service as sheets


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
        self.annealing = ProductionStage.name_for_code("annealing")
        self.coating = ProductionStage.name_for_code("coating")

        self.pipe = Pipe(
            production_date=date(2026, 8, 20), ladle_id="MS1",
            pipe_code="MS1-P1", no_code="MS1N1", arrange_pipe=1,
            diameter=300, pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.ccm,
            stage_date=date(2026, 8, 20), stage_time=time(6, 30),
            dimension_profile={
                "thickness": {"positions": {
                    "1": [7.2, 7.4, 7.3],
                    "2": [7.0, 7.1, 7.2],
                }},
                "diameter": {"samples": {"S1": [326.0, 326.4]}},
            },
        ))
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.annealing,
            dimension_profile={"symbols": {"d1": 326.5, "d2": 400.0}},
            ovality_profile={"points": {"ID": {"x": 101.0, "y": 99.0,
                                               "ovality": 1.0}}},
        ))
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={"cement": [3.5, 3.6, 3.7, 3.5, 3.4, 3.6],
                               "coating": [70.0, 70.4, 69.8, 70.1, 70.0, 70.2]},
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


class SheetTest(_Base):
    def test_identity_carries_when_it_was_made(self):
        ident = sheets.pipe_measurement_sheet(self.pipe.id)["identity"]
        self.assertEqual(ident["pipe_code"], "MS1-P1")
        self.assertEqual(ident["production_date"], date(2026, 8, 20))
        self.assertEqual(ident["cast_date"], date(2026, 8, 20))
        self.assertEqual(ident["cast_time"], time(6, 30))
        self.assertEqual(ident["dn"], 300)

    def test_thickness_raw_readings_and_statistics(self):
        block = sheets.pipe_measurement_sheet(self.pipe.id)["thickness"]
        self.assertEqual(block["matrix"]["1"], [7.2, 7.4, 7.3])
        self.assertAlmostEqual(block["by_position"]["1"]["mean"], 7.3)
        self.assertEqual(block["overall"]["n"], 6)
        self.assertAlmostEqual(block["overall"]["mean"], 7.2)
        self.assertIsNotNone(block["overall"]["stdev"])

    def test_symbols_carry_deviation_and_verdict(self):
        rows = sheets.pipe_measurement_sheet(self.pipe.id)["symbols"]["rows"]
        by_symbol = {r["symbol"]: r for r in rows}
        self.assertAlmostEqual(by_symbol["d1"]["deviation"], 0.5)
        self.assertEqual(by_symbol["d1"]["status"], "in")
        # d2 nominal for DN300 is 356.5, so 400 is well outside.
        self.assertEqual(by_symbol["d2"]["status"], "out")

    def test_symbol_rows_name_the_stage_they_came_from(self):
        rows = sheets.pipe_measurement_sheet(self.pipe.id)["symbols"]["rows"]
        self.assertTrue(all(r["stage"] == self.annealing for r in rows))

    def test_lining_and_diameter_and_ovality(self):
        sheet = sheets.pipe_measurement_sheet(self.pipe.id)
        self.assertAlmostEqual(
            sheet["lining"]["layers"]["cement"]["stats"]["mean"], 3.55)
        self.assertEqual(sheet["diameter"]["overall"]["n"], 2)
        self.assertAlmostEqual(sheet["ovality"]["overall"]["mean"], 1.0)

    def test_missing_pipe(self):
        self.assertIsNone(sheets.pipe_measurement_sheet(999999))

    def test_pipe_without_measurements_has_empty_blocks(self):
        bare = Pipe(production_date=date(2026, 8, 21), ladle_id="MS2",
                    pipe_code="MS2-P1", no_code="MS2N1", arrange_pipe=1,
                    diameter=300, pipe_class="K9")
        db.session.add(bare)
        db.session.commit()
        sheet = sheets.pipe_measurement_sheet(bare.id)
        self.assertIsNone(sheet["thickness"])
        self.assertIsNone(sheet["symbols"])
        self.assertIsNone(sheet["lining"])


class ExportTest(_Base):
    def test_one_row_per_reading(self):
        rows = sheets.flatten_for_export(
            sheets.pipe_measurement_sheet(self.pipe.id))
        kinds = {r["measurement"] for r in rows}
        self.assertIn("wall_thickness", kinds)
        self.assertIn("diameter", kinds)
        self.assertIn("cement", kinds)
        self.assertIn("dim_d1", kinds)
        wall = [r for r in rows if r["measurement"] == "wall_thickness"]
        self.assertEqual(len(wall), 6)
        self.assertEqual(wall[0]["location"], "1 m")

    def test_every_cell_is_scalar(self):
        rows = sheets.flatten_for_export(
            sheets.pipe_measurement_sheet(self.pipe.id))
        for r in rows:
            for k, v in r.items():
                self.assertNotIsInstance(v, (dict, list), f"{k} not scalar")

    def test_no_sheet_exports_nothing(self):
        self.assertEqual(sheets.flatten_for_export(None), [])


class RouteTest(_Base):
    def test_report_renders_readings_and_statistics(self):
        self._login()
        html = self.client.get(
            f"/reports/pipe-measurements?pipe={self.pipe.id}"
        ).get_data(as_text=True)
        self.assertIn("MS1-P1", html)
        self.assertIn("2026-08-20", html)
        self.assertIn("7.4", html)          # a raw reading
        self.assertIn("7.3", html)          # a per-position average
        self.assertIn("Out of tol", html)   # d2 verdict

    def test_lookup_by_pipe_code(self):
        self._login()
        html = self.client.get(
            "/reports/pipe-measurements?pipe=MS1-P1").get_data(as_text=True)
        self.assertIn("MS1-P1", html)

    def test_unknown_code_says_so(self):
        self._login()
        html = self.client.get(
            "/reports/pipe-measurements?pipe=NOPE").get_data(as_text=True)
        self.assertIn("No pipe found for", html)

    def test_empty_query_prompts_instead_of_erroring(self):
        self._login()
        resp = self.client.get("/reports/pipe-measurements")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Enter a pipe code", resp.get_data(as_text=True))

    def test_picker_lists_pipes_that_have_measurements(self):
        self._login()
        html = self.client.get(
            "/reports/pipe-measurements").get_data(as_text=True)
        self.assertIn("MS1-P1", html)

    def test_report_is_linked_from_the_index(self):
        self._login()
        html = self.client.get("/reports/").get_data(as_text=True)
        self.assertIn("/reports/pipe-measurements", html)


if __name__ == "__main__":
    unittest.main()
