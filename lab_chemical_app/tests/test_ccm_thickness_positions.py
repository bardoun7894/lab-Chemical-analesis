"""CCM wall thickness entered as 7 positions x 3 readings.

The operator's sheet measures the wall at 1, 2, 3, 4, 5, 5.5 and 6 m along the
pipe, three readings at each position. The app used to store those 21 numbers
as an unnamed flat row per sample, so nothing downstream could tell a position
from a repeat reading. Posts in the old shape must still be accepted and
folded into positions rather than dropped.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes.stages import _parse_dimension_profile
from app.services import measurement_stats_service as stats


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.ccm = ProductionStage.name_for_code("ccm")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _post(self, mapping):
        return _parse_dimension_profile(mapping.items(), self.ccm)

    def test_positions_are_named_from_the_operator_sheet(self):
        profile, has_any = self._post({
            f"stage_{self.ccm}_dim_thick_p1_1": "5.0",
            f"stage_{self.ccm}_dim_thick_p1_2": "5.1",
            f"stage_{self.ccm}_dim_thick_p1_3": "5.2",
            f"stage_{self.ccm}_dim_thick_p6_1": "4.8",
        })
        self.assertTrue(has_any)
        positions = profile["thickness"]["positions"]
        self.assertEqual(positions["1"], [5.0, 5.1, 5.2])
        self.assertEqual(positions["5.5"], [4.8, None, None])

    def test_all_seven_positions_round_trip(self):
        mapping = {}
        for pos_idx in range(1, 8):
            for reading in range(1, 4):
                mapping[f"stage_{self.ccm}_dim_thick_p{pos_idx}_{reading}"] = (
                    f"{pos_idx}.{reading}")
        profile, _ = self._post(mapping)
        positions = profile["thickness"]["positions"]
        self.assertEqual(list(positions.keys()), list(stats.THICKNESS_POSITIONS))
        self.assertEqual(positions["6"], [7.1, 7.2, 7.3])
        self.assertEqual(sum(len(v) for v in positions.values()), 21)

    def test_position_index_out_of_range_is_ignored(self):
        profile, has_any = self._post({
            f"stage_{self.ccm}_dim_thick_p8_1": "9.9",
            f"stage_{self.ccm}_dim_thick_p1_4": "9.9",
        })
        self.assertFalse(has_any)
        self.assertIsNone(profile)

    def test_legacy_flat_row_is_folded_not_dropped(self):
        mapping = {
            f"stage_{self.ccm}_dim_thick_S1_{n}": str(float(n))
            for n in range(1, 22)
        }
        profile, has_any = self._post(mapping)
        self.assertTrue(has_any)
        positions = profile["thickness"]["positions"]
        self.assertEqual(positions["1"], [1.0, 2.0, 3.0])
        self.assertEqual(positions["6"], [19.0, 20.0, 21.0])

    def test_new_shape_wins_over_a_legacy_row_in_the_same_post(self):
        profile, _ = self._post({
            f"stage_{self.ccm}_dim_thick_p1_1": "5.0",
            f"stage_{self.ccm}_dim_thick_S1_1": "99.0",
        })
        self.assertEqual(profile["thickness"]["positions"]["1"][0], 5.0)

    def test_diameter_keeps_its_sample_rows(self):
        profile, _ = self._post({
            f"stage_{self.ccm}_dim_dia_S1_1": "326.0",
            f"stage_{self.ccm}_dim_dia_S2_15": "325.4",
        })
        samples = profile["diameter"]["samples"]
        self.assertEqual(samples["S1"][0], 326.0)
        self.assertEqual(samples["S2"][14], 325.4)

    def test_other_stages_are_untouched(self):
        profile, has_any = _parse_dimension_profile(
            {f"stage_{self.ccm}_dim_thick_p1_1": "5.0"}.items(), "Coating")
        self.assertIsNone(profile)
        self.assertFalse(has_any)

    def test_empty_post(self):
        self.assertEqual(self._post({}), (None, False))


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

        self.pipe = Pipe(
            production_date=date(2026, 8, 22), ladle_id="TH1",
            pipe_code="TH1-P1", no_code="T0001", arrange_pipe=1,
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

    def test_edit_form_renders_the_position_grid(self):
        self._login()
        html = self.client.get(
            f"/stages/{self.pipe.id}/edit").get_data(as_text=True)
        ccm = ProductionStage.name_for_code("ccm")
        self.assertIn(f"stage_{ccm}_dim_thick_p1_1", html)
        self.assertIn(f"stage_{ccm}_dim_thick_p7_3", html)
        self.assertNotIn(f"stage_{ccm}_dim_thick_S1_1", html)
        # The 5.5 m position must be a visible column header.
        self.assertIn(">5.5<", html)

    def test_legacy_profile_still_shows_its_numbers(self):
        ccm = ProductionStage.name_for_code("ccm")
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=ccm,
            dimension_profile={"thickness": {"samples": {
                "S1": [float(n) for n in range(1, 22)]}}},
        ))
        db.session.commit()
        self._login()
        html = self.client.get(
            f"/stages/{self.pipe.id}/edit").get_data(as_text=True)
        self.assertIn('value="1.0"', html)
        self.assertIn('value="21.0"', html)

    def test_a_pipe_with_no_order_says_it_has_no_wall_standard(self):
        # The wall band used to come from TA 1012, keyed by DN. It now comes
        # from the product's Application table for the standard the order was
        # built to, so a pipe with no order has nothing to judge against — and
        # has to say so rather than render an empty grid that looks passed.
        self._login()
        html = self.client.get(
            f"/stages/{self.pipe.id}/edit").get_data(as_text=True)
        self.assertIn("No standard on file for", html)
        # Rendered without d-none, i.e. actually visible.
        self.assertIn('spec-note spec-note-missing" data-spec-for=', html)


if __name__ == "__main__":
    unittest.main()
