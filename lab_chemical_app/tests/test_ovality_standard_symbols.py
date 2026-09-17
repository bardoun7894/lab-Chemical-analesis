"""Ovality grid keyed by the TA 1012 symbols, not an invented D1-D15 run.

DrAlaa 2026-08-23, looking at the Ovality popup next to the standard sheet:
"مش D1 وD2 ... على حسب ما موجودة في الجدول" — the columns must be the symbols
the standard actually names (d1, d2, ... S1, S2, C, t1 ... r3) for the pipe's
DN, plus ID for the internal diameter the sheet has no symbol for.

Two further rules from the same review:
  * Ovality = (X-Y)/(X+Y) only means something on a diameter. It is computed
    for ID and d1 and left blank everywhere else, instead of being printed
    under every column as if a wall thickness could be oval.
  * The standard (nominal + tolerance) belongs in the same grid as X and Y, at
    both stations that take the reading — the Register/Edit form and the Stage
    Console — so the operator sees the target next to what they measured.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes.stages import ovality_points_for


class _OvalityBase(unittest.TestCase):
    DN = 300

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
            production_date=date(2026, 8, 23), ladle_id="OVS1",
            pipe_code="OVS1-P1", no_code="O0001", arrange_pipe=1,
            diameter=self.DN, pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

        self.annealing = ProductionStage.name_for_code("annealing")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _console_html(self):
        return self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)

    def _form_html(self):
        """The Edit form, not /stages/add.

        /stages/add registers a new pipe, so its DN is only chosen in the same
        submission and there is no standard to draw columns from yet. The two
        stations that take an Annealing reading on a pipe that already exists
        are the Edit form and the Stage Console.
        """
        return self.client.get(
            f"/stages/{self.pipe.id}/edit"
        ).get_data(as_text=True)

    def _stage(self):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.annealing
        ).first()


class OvalityPointsTestCase(_OvalityBase):
    def test_points_are_the_dn_standard_symbols_plus_id(self):
        points = ovality_points_for(self.DN)
        self.assertEqual(points[0], "ID")
        # Exactly the symbols DN300 has a nominal for, in standard-sheet order.
        self.assertEqual(
            points[1:],
            ["d1", "d2", "d3", "d4", "d5", "d7", "S1", "S2", "C",
             "t1", "t2", "t3", "t4", "t5", "t6", "r2"],
        )

    def test_invented_d_run_is_gone(self):
        points = ovality_points_for(self.DN)
        self.assertNotIn("D1", points)
        self.assertNotIn("D15", points)

    def test_dn_without_a_standard_falls_back_to_id_only(self):
        self.assertEqual(ovality_points_for(9999), ["ID"])
        self.assertEqual(ovality_points_for(None), ["ID"])


class OvalityGridMarkupTestCase(_OvalityBase):
    """Both stations render one merged grid: standard + X/Y in the same table."""

    def _assert_merged_grid(self, html, where):
        self.assertIn(f'name="stage_{self.annealing}_ov_x_d1"', html,
                      f"{where}: no d1 column")
        self.assertIn(f'name="stage_{self.annealing}_ov_y_S1"', html,
                      f"{where}: no S1 column")
        self.assertIn(f'name="stage_{self.annealing}_ov_x_ID"', html,
                      f"{where}: ID column dropped")
        self.assertNotIn(f'name="stage_{self.annealing}_ov_x_D5"', html,
                         f"{where}: invented D5 column still rendered")
        # The DN300 nominal for d1 sits in the same table as the X/Y inputs.
        self.assertIn("326", html, f"{where}: d1 nominal missing")

    def test_console_renders_merged_grid(self):
        self._login()
        self._assert_merged_grid(self._console_html(), "console")

    def test_register_form_renders_merged_grid(self):
        self._login()
        self._assert_merged_grid(self._form_html(), "form")


class OvalityScopeTestCase(_OvalityBase):
    def test_ovality_only_on_the_diameters_id_and_d1(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={
                "decision": "Accept",
                f"stage_{self.annealing}_ov_x_ID": "21",
                f"stage_{self.annealing}_ov_y_ID": "20",
                f"stage_{self.annealing}_ov_x_d1": "327",
                f"stage_{self.annealing}_ov_y_d1": "325",
                f"stage_{self.annealing}_ov_x_S1": "7.4",
                f"stage_{self.annealing}_ov_y_S1": "7.0",
            },
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        points = self._stage().ovality_profile["points"]

        # (21-20)/(21+20)*100
        self.assertAlmostEqual(points["ID"]["ovality"], 2.44, places=2)
        # (327-325)/(327+325)*100
        self.assertAlmostEqual(points["d1"]["ovality"], 0.31, places=2)

        # S1 is a wall thickness: both readings kept, no ovality invented.
        self.assertEqual(points["S1"]["x"], 7.4)
        self.assertEqual(points["S1"]["y"], 7.0)
        self.assertNotIn("ovality", points["S1"])

    def test_x_alone_is_kept_for_a_single_reading_symbol(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={"decision": "Accept",
                  f"stage_{self.annealing}_ov_x_t1": "110"},
        )
        points = self._stage().ovality_profile["points"]
        self.assertEqual(points["t1"]["x"], 110)
        self.assertIsNone(points["t1"].get("y"))
        self.assertNotIn("ovality", points["t1"])


class OvalityFeedsStandardComparisonTestCase(_OvalityBase):
    """The merged grid is the only input, so it must still feed SPC/capability.

    spc_service and pipe_measurement_service both read
    ``dimension_profile["symbols"]``. The X/Y grid derives that value instead
    of asking the operator to type the same reading twice.
    """

    def test_symbols_are_derived_from_the_xy_mean(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={
                "decision": "Accept",
                f"stage_{self.annealing}_ov_x_d1": "327",
                f"stage_{self.annealing}_ov_y_d1": "325",
                f"stage_{self.annealing}_ov_x_t1": "110",
            },
        )
        symbols = self._stage().dimension_profile["symbols"]
        self.assertAlmostEqual(symbols["d1"], 326.0, places=2)
        # Only X filled -> that reading is the actual, not a mean with zero.
        self.assertAlmostEqual(symbols["t1"], 110.0, places=2)

    def test_id_never_leaks_into_the_standard_symbols(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={"decision": "Accept",
                  f"stage_{self.annealing}_ov_x_ID": "21",
                  f"stage_{self.annealing}_ov_y_ID": "20"},
        )
        symbols = (self._stage().dimension_profile or {}).get("symbols") or {}
        self.assertNotIn("ID", symbols)


class LegacyOvalityDataTestCase(_OvalityBase):
    """Readings saved under the old D1-D15 keys must still be displayed.

    The columns changed; the measurements already in the database did not.
    Dropping them from the render would look like data loss on every pipe
    annealed before this change.
    """

    def test_saved_legacy_points_still_render(self):
        self._login()
        stage = PipeStage(
            pipe_id=self.pipe.id, stage_name=self.annealing,
            decision="Accept",
            ovality_profile={"points": {"D7": {"x": 30.0, "y": 29.0,
                                               "ovality": 1.69}}},
        )
        db.session.add(stage)
        db.session.commit()

        html = self._console_html()
        self.assertIn("D7", html)
        self.assertIn("1.69", html)


if __name__ == "__main__":
    unittest.main()
