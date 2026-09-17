"""TA 1012 symbol readings on the CCM and Annealing stages.

The operator measures the standard's dimension symbols (d1, d2, S1, C, t1 ...)
at CCM and again at Annealing, and needs the deviation from the DN's nominal
shown as they type. Readings are stored under dimension_profile["symbols"].
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes.stages import _parse_dimension_profile, _merge_dimension_profile


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.ccm = ProductionStage.name_for_code("ccm")
        self.annealing = ProductionStage.name_for_code("annealing")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_annealing_stores_symbol_readings(self):
        profile, has_any = _parse_dimension_profile(
            {f"stage_{self.annealing}_std_d1": "326.4",
             f"stage_{self.annealing}_std_S1": "7.3"}.items(),
            self.annealing)
        self.assertTrue(has_any)
        self.assertEqual(profile["symbols"], {"d1": 326.4, "S1": 7.3})
        # Annealing carries no thickness/diameter grid.
        self.assertNotIn("thickness", profile)

    def test_ccm_stores_symbols_alongside_the_grids(self):
        profile, _ = _parse_dimension_profile(
            {f"stage_{self.ccm}_dim_thick_p1_1": "7.2",
             f"stage_{self.ccm}_std_d1": "326.4"}.items(),
            self.ccm)
        self.assertEqual(profile["symbols"], {"d1": 326.4})
        self.assertEqual(profile["thickness"]["positions"]["1"][0], 7.2)

    def test_unknown_symbol_is_rejected(self):
        profile, has_any = _parse_dimension_profile(
            {f"stage_{self.annealing}_std_nope": "1.0"}.items(),
            self.annealing)
        self.assertFalse(has_any)
        self.assertIsNone(profile)

    def test_non_numeric_reading_is_skipped(self):
        profile, has_any = _parse_dimension_profile(
            {f"stage_{self.annealing}_std_d1": "abc"}.items(), self.annealing)
        self.assertFalse(has_any)

    def test_other_stages_ignore_the_symbol_grid(self):
        profile, has_any = _parse_dimension_profile(
            {"stage_Coating_std_d1": "326.4"}.items(), "Coating")
        self.assertIsNone(profile)
        self.assertFalse(has_any)


class MergeTest(unittest.TestCase):
    def test_symbols_only_post_keeps_the_saved_thickness_grid(self):
        existing = {"thickness": {"positions": {"1": [7.2, 7.3, 7.1]}},
                    "diameter": {"samples": {"S1": [326.0]}}}
        merged = _merge_dimension_profile(existing, {"symbols": {"d1": 326.4}})
        self.assertEqual(merged["thickness"]["positions"]["1"], [7.2, 7.3, 7.1])
        self.assertEqual(merged["symbols"], {"d1": 326.4})

    def test_grid_post_keeps_saved_symbols(self):
        existing = {"symbols": {"d1": 326.4}}
        merged = _merge_dimension_profile(
            existing, {"thickness": {"positions": {"1": [7.2]}},
                       "diameter": {"samples": {}}})
        self.assertEqual(merged["symbols"], {"d1": 326.4})
        self.assertIn("thickness", merged)

    def test_a_section_is_replaced_wholesale_so_it_can_be_cleared(self):
        existing = {"symbols": {"d1": 326.4, "d2": 356.0}}
        merged = _merge_dimension_profile(existing, {"symbols": {"d1": 327.0}})
        self.assertEqual(merged["symbols"], {"d1": 327.0})

    def test_no_prior_profile(self):
        self.assertEqual(_merge_dimension_profile(None, {"symbols": {"d1": 1}}),
                         {"symbols": {"d1": 1}})


class RenderTest(unittest.TestCase):
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
        self.annealing = ProductionStage.name_for_code("annealing")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, diameter=300):
        pipe = Pipe(production_date=date(2026, 8, 22), ladle_id="SD1",
                    pipe_code=f"SD1-P{diameter}", no_code=f"S{diameter:04d}",
                    arrange_pipe=1, diameter=diameter, pipe_class="K9")
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_grid_shows_nominal_and_tolerance_for_the_dn(self):
        pipe = self._pipe(300)
        self._login()
        html = self.client.get(f"/stages/{pipe.id}/edit").get_data(as_text=True)
        self.assertIn("Dimensions &amp; ovality", html)
        # The reading is taken in the merged grid now: X/Y per symbol, with the
        # nominal and tolerance in the same table rather than a second one.
        self.assertIn(f"stage_{self.annealing}_ov_x_d1", html)
        self.assertIn(f"stage_{self.annealing}_ov_y_d1", html)
        self.assertNotIn(f"stage_{self.annealing}_std_d1", html)
        self.assertIn("326", html)   # d1 nominal for DN300
        self.assertIn("-3.3", html)  # its lower tolerance

    def test_saved_reading_renders_its_deviation_without_js(self):
        pipe = self._pipe(300)
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.annealing,
            dimension_profile={"symbols": {"d1": 326.5}},
        ))
        db.session.commit()
        self._login()
        html = self.client.get(f"/stages/{pipe.id}/edit").get_data(as_text=True)
        self.assertIn('value="326.5"', html)
        self.assertIn("0.5", html)

    def test_out_of_tolerance_reading_is_flagged_server_side(self):
        pipe = self._pipe(300)
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.annealing,
            dimension_profile={"symbols": {"d1": 330.0}},  # usl is 327.0
        ))
        db.session.commit()
        self._login()
        html = self.client.get(f"/stages/{pipe.id}/edit").get_data(as_text=True)
        self.assertIn("std-dim-diff text-danger", html)

    def test_dn_without_a_standard_explains_itself(self):
        pipe = self._pipe(9999)
        self._login()
        html = self.client.get(f"/stages/{pipe.id}/edit").get_data(as_text=True)
        self.assertIn("No standard on file for", html)
        self.assertNotIn(f"stage_{self.annealing}_std_d1", html)

    def test_reading_saves_through_the_edit_form(self):
        pipe = self._pipe(300)
        self._login()
        resp = self.client.post(f"/stages/{pipe.id}/edit", data={
            "no_code": "S0300", "production_date": "2026-08-22",
            "shift": "1", "diameter": "300", "pipe_class": "K9",
            "arrange_pipe": "1",
            f"stage_{self.annealing}_std_d1": "326.6",
        })
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True))
        stage = PipeStage.query.filter_by(
            pipe_id=pipe.id, stage_name=self.annealing).one()
        self.assertEqual(stage.dimension_profile["symbols"]["d1"], 326.6)


if __name__ == "__main__":
    unittest.main()
