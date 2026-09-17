"""Cement lining thickness taken X/Y at the socket and the spigot.

Six per-metre readings did not match the floor: the lining is measured at the
two ends, twice at each, and each measurement is a perpendicular pair. What is
wanted is the spread of each pair, the average of each position, and one
overall average — the same shape as the ovality grid.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import lining_points_service as lps


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

        u = User(username="admin", full_name="Admin", role="super_admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.pipe = Pipe(production_date=date(2026, 9, 1), ladle_id="CM1",
                         pipe_code="CM1-P1", no_code="C0001", arrange_pipe=1,
                         diameter=700, pipe_class="K9")
        db.session.add(self.pipe)
        db.session.commit()
        self.coating = ProductionStage.name_for_code("coating")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _post(self, **extra):
        self._login()
        data = {"decision": "Accept"}
        data.update(extra)
        return self.client.post(
            "/stages/%d/stage/%s" % (self.pipe.id, self.coating), json=data)

    def _profile(self):
        st = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.coating).first()
        return (st.thickness_profile or {}) if st else {}


class ServiceTest(unittest.TestCase):
    def test_the_four_positions_are_two_per_end(self):
        ends = [end for _k, end, _n in lps.CEMENT_POINTS]
        self.assertEqual(ends.count("socket"), 2)
        self.assertEqual(ends.count("spigot"), 2)
        self.assertEqual([n for _k, _e, n in lps.CEMENT_POINTS],
                         ["1", "2", "3", "4"])

    def test_diff_is_the_spread_and_avg_the_mean(self):
        self.assertEqual(lps.summarise_point(5.0, 4.0), (1.0, 4.5))
        self.assertEqual(lps.summarise_point(4.0, 5.0), (-1.0, 4.5))

    def test_one_reading_has_an_average_but_no_spread(self):
        # Nothing to differ from, so no difference is invented.
        self.assertEqual(lps.summarise_point(5.0, None), (None, 5.0))
        self.assertEqual(lps.summarise_point(None, None), (None, None))

    def test_the_overall_average_ignores_positions_never_measured(self):
        points = {"socket_1": {"avg": 4.0}, "spigot_3": {"avg": 6.0}}
        self.assertEqual(lps.overall_average(points), 5.0)
        self.assertIsNone(lps.overall_average({}))


class SaveTest(_Base):
    def _cells(self, **vals):
        out = {}
        for key, val in vals.items():
            point, axis = key.rsplit("_", 1)
            out[lps.field_name(self.coating, point, axis)] = str(val)
        return out

    def test_the_grid_saves_its_readings_and_its_derived_rows(self):
        resp = self._post(**self._cells(
            socket_1_x=5.0, socket_1_y=4.0,
            socket_2_x=5.5, socket_2_y=5.5,
            spigot_3_x=6.0, spigot_3_y=5.0,
            spigot_4_x=4.0, spigot_4_y=4.0,
        ))
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        profile = self._profile()
        pts = profile["cement_points"]
        self.assertEqual(pts["socket_1"]["diff"], 1.0)
        self.assertEqual(pts["socket_1"]["avg"], 4.5)
        self.assertEqual(pts["socket_2"]["diff"], 0.0)
        self.assertEqual(pts["spigot_3"]["avg"], 5.5)
        # (4.5 + 5.5 + 5.5 + 4.0) / 4
        self.assertEqual(profile["cement_avg"], 4.88)

    def test_the_band_still_saves_alongside_the_readings(self):
        self._post(**dict(
            self._cells(socket_1_x=5.0, socket_1_y=4.0),
            **{"stage_%s_thick_cement_std_min" % self.coating: "3",
               "stage_%s_thick_cement_std" % self.coating: "5",
               "stage_%s_thick_cement_std_max" % self.coating: "7"}))
        profile = self._profile()
        self.assertEqual(profile["cement_std_min"], 3.0)
        self.assertEqual(profile["cement_std"], 5.0)
        self.assertEqual(profile["cement_std_max"], 7.0)

    def test_a_save_without_the_grid_leaves_the_readings_alone(self):
        """The band and the grid are on the same screen, but a post that
        carries neither must not blank what is stored."""
        self._post(**self._cells(socket_1_x=5.0, socket_1_y=4.0))
        self._post(**{"stage_%s_thick_coating_1" % self.coating: "70"})
        profile = self._profile()
        self.assertEqual(profile["cement_points"]["socket_1"]["x"], 5.0)
        self.assertEqual(profile["coating"][0], 70.0)

    def test_a_partial_save_keeps_the_positions_it_did_not_name(self):
        """The console posts the whole grid, but a partial save must not drop
        the spigot readings just because it only carried the socket."""
        self._post(**self._cells(
            socket_1_x=5.0, socket_1_y=4.0, spigot_3_x=6.0, spigot_3_y=6.0))
        self._post(**self._cells(socket_1_x=5.5))
        points = self._profile()["cement_points"]
        self.assertEqual(points["socket_1"]["x"], 5.5)
        self.assertEqual(points["spigot_3"]["x"], 6.0)
        # and the overall average is recomputed over what actually remains
        self.assertEqual(self._profile()["cement_avg"], 5.75)

    def test_coating_is_still_measured_per_metre(self):
        self._post(**{"stage_%s_thick_coating_%d" % (self.coating, m): str(60 + m)
                      for m in range(1, 7)})
        self.assertEqual(self._profile()["coating"],
                         [61.0, 62.0, 63.0, 64.0, 65.0, 66.0])


class RenderTest(_Base):
    def test_the_popup_shows_socket_and_spigot_not_six_metres(self):
        self._login()
        html = self.client.get(
            "/stages/%d/edit" % self.pipe.id).get_data(as_text=True)
        self.assertIn("Socket", html)
        self.assertIn("Spigot", html)
        for key, _e, _n in lps.CEMENT_POINTS:
            for axis in ("x", "y"):
                self.assertIn(lps.field_name(self.coating, key, axis), html)
        # the per-metre cement cells are gone; coating keeps its own
        self.assertNotIn("stage_%s_thick_cement_1" % self.coating, html)
        self.assertIn("stage_%s_thick_coating_1" % self.coating, html)

    def test_saved_readings_come_back_with_their_derived_rows(self):
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={
                "cement_points": {"socket_1": {"x": 5.0, "y": 4.0,
                                               "diff": 1.0, "avg": 4.5}},
                "cement_avg": 4.5,
            },
        ))
        db.session.commit()
        self._login()
        html = self.client.get(
            "/stages/%d/edit" % self.pipe.id).get_data(as_text=True)
        self.assertIn('value="5.0"', html)
        self.assertIn('cem-grand', html)

    def test_the_console_renders_the_same_grid(self):
        self._login()
        html = self.client.get(
            "/stages/console/pipe/%d" % self.pipe.id).get_data(as_text=True)
        self.assertIn(lps.field_name(self.coating, "socket_1", "x"), html)
        self.assertIn(lps.field_name(self.coating, "spigot_4", "y"), html)

    def test_the_console_page_carries_the_grid_script(self):
        """The pane is injected with innerHTML, which never runs a script
        inside it, so the script has to be on the console page itself."""
        self._login()
        html = self.client.get("/stages/console").get_data(as_text=True)
        self.assertIn("__cemBound", html)
        self.assertIn("refreshCementGrid", html)


if __name__ == "__main__":
    unittest.main()
