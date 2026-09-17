"""The Min/Nominal/Max band is not stage activity, and not a measurement.

The band is prefilled from the order's Application spec and posts on every
save. Counting it as work done meant a stage row was written for a pipe
nobody had touched, so the pipe showed as in progress, and the popup button
went green as if readings had been taken.
"""

import re
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

        self.pipe = Pipe(production_date=date(2026, 9, 1), ladle_id="SB1",
                         pipe_code="SB1-P1", no_code="B0001", arrange_pipe=1,
                         diameter=700, pipe_class="K9")
        db.session.add(self.pipe)
        db.session.commit()
        self.coating = ProductionStage.name_for_code("coating")
        self.ccm = ProductionStage.name_for_code("ccm")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class ButtonStateTest(_Base):
    def _coating_button(self):
        """The whole <button> tag for the Thickness popup, attributes and all."""
        html = self.client.get(
            "/stages/%d/edit" % self.pipe.id).get_data(as_text=True)
        m = re.search(r"<button[^>]*id=\"coatingModalBtn\"[^>]*>", html)
        self.assertIsNotNone(m, "Thickness popup button not rendered")
        return m.group(0)

    def test_nothing_recorded_reads_as_empty(self):
        stage = PipeStage(pipe_id=self.pipe.id, stage_name=self.coating)
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_EMPTY)
        self.assertEqual(stage.dimension_popup_state(), PipeStage.POPUP_EMPTY)

    def test_a_band_on_its_own_is_not_data(self):
        stage = PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={"cement_std": 5.0, "cement_std_min": 3.0,
                               "cement_std_max": 7.0},
        )
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_STANDARD)

    def test_a_reading_makes_it_data(self):
        stage = PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={
                "cement_std": 5.0,
                "cement_points": {"socket_1": {"x": 5.0, "y": 4.0}},
            },
        )
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_DATA)

    def test_a_pre_grid_per_metre_reading_still_counts(self):
        stage = PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={"cement": [3.5, None, None, None, None, None]},
        )
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_DATA)

    def test_the_dimension_band_alone_is_not_data(self):
        stage = PipeStage(
            pipe_id=self.pipe.id, stage_name=self.ccm,
            dimension_profile={"thickness": {"standard": 10.8,
                                             "standard_min": 10.5,
                                             "standard_max": 11.1}},
        )
        self.assertEqual(stage.dimension_popup_state(),
                         PipeStage.POPUP_STANDARD)

    def test_a_dimension_reading_makes_it_data(self):
        for profile in (
            {"thickness": {"standard": 10.8, "positions": {"1": [10.6]}}},
            {"thickness": {"samples": {"S1": [10.6]}}},
            {"ovality": {"points": {"ID": {"x": 700.0, "y": 699.0}}}},
        ):
            with self.subTest(profile=profile):
                stage = PipeStage(pipe_id=self.pipe.id, stage_name=self.ccm,
                                  dimension_profile=profile)
                self.assertEqual(stage.dimension_popup_state(),
                                 PipeStage.POPUP_DATA)

    def test_only_readings_colour_the_button(self):
        """A band on its own leaves the button plain — it came from the order,
        nobody measured anything. Only readings turn it green."""
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={"cement_std": 5.0},
        ))
        db.session.commit()
        button = self._coating_button()
        self.assertIn("btn-outline-secondary", button)
        self.assertNotIn("btn-success", button)
        self.assertNotIn("btn-warning", button)
        # it still says why, without shouting in colour
        self.assertIn("Standard set, no readings yet", button)

        stage = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.coating).first()
        stage.thickness_profile = dict(
            stage.thickness_profile,
            cement_points={"socket_1": {"x": 5.0, "y": 4.0}})
        db.session.commit()
        button = self._coating_button()
        self.assertIn("btn-success", button)
        self.assertNotIn("Standard set, no readings yet", button)


class RegisterFormButtonTest(_Base):
    """/stages/add has no stage rows, so the button colour is decided in the
    browser. That rule has to match the server's: the band is prefilled from
    the order the moment one is picked, and it must not turn the button green.
    """

    def _script(self):
        self._login()
        html = self.client.get("/stages/add").get_data(as_text=True)
        for m in re.finditer(r"<script>(.*?)</script>", html, re.S):
            if "refreshModalButtons" in m.group(1):
                return m.group(1)
        self.fail("refreshModalButtons script not rendered on /stages/add")

    def test_the_band_inputs_are_excluded_from_the_green_check(self):
        script = self._script()
        # the rule itself, not a paraphrase of it
        self.assertIn("_std(_min|_max)?$", script)
        self.assertIn("!isBand(inp)", script)

    def test_every_reading_cell_would_still_count(self):
        """The exclusion is anchored to the end of the name, so a reading whose
        name merely contains 'std' is not swallowed by it."""
        import re as _re
        band = _re.compile(r"_std(_min|_max)?$")
        for name in ("stage_Coating_thick_cement_std",
                     "stage_Coating_thick_cement_std_min",
                     "stage_Coating_thick_cement_std_max",
                     "stage_CCM_dim_thick_std"):
            self.assertTrue(band.search(name), name)
        for name in ("stage_Coating_thick_cement_pt_socket_1_x",
                     "stage_CCM_dim_thick_p1_1",
                     "stage_Annealing_ov_x_ID"):
            self.assertFalse(band.search(name), name)


class ActivityTest(_Base):
    def test_a_band_alone_does_not_create_a_stage_row(self):
        """Saving a pipe whose Coating band came from the order must not make
        the stage look started."""
        self._login()
        resp = self.client.post(
            "/stages/%d/stage/%s" % (self.pipe.id, self.coating),
            json={"stage_%s_thick_cement_std" % self.coating: "5",
                  "stage_%s_thick_cement_std_min" % self.coating: "3",
                  "stage_%s_thick_cement_std_max" % self.coating: "7"})
        self.assertTrue(resp.get_json()["success"],
                        resp.get_data(as_text=True))
        stage = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.coating).first()
        # update_stage works on a row it owns, so the band is stored...
        self.assertEqual(stage.thickness_profile["cement_std"], 5.0)
        # ...but nothing about it says a measurement happened
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_STANDARD)

    def test_the_band_still_saves_when_readings_arrive(self):
        self._login()
        self.client.post(
            "/stages/%d/stage/%s" % (self.pipe.id, self.coating),
            json={"stage_%s_thick_cement_std" % self.coating: "5",
                  lps.field_name(self.coating, "cement", "socket_1", "x"): "5.2"})
        stage = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.coating).first()
        self.assertEqual(stage.thickness_profile["cement_std"], 5.0)
        self.assertEqual(
            stage.thickness_profile["cement_points"]["socket_1"]["x"], 5.2)
        self.assertEqual(stage.lining_popup_state(), PipeStage.POPUP_DATA)

    def test_a_blank_band_still_clears(self):
        self._login()
        db.session.add(PipeStage(
            pipe_id=self.pipe.id, stage_name=self.coating,
            thickness_profile={"cement_std": 5.0}))
        db.session.commit()
        self.client.post(
            "/stages/%d/stage/%s" % (self.pipe.id, self.coating),
            json={"stage_%s_thick_cement_std" % self.coating: ""})
        stage = PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=self.coating).first()
        self.assertIsNone(stage.thickness_profile["cement_std"])


class StageIsNotStartedTest(_Base):
    """Registering a pipe must not stamp a date and In Progress onto stages
    nobody has worked. Three things arrive on their own and used to count:
    the Coating band, the CCM band, and the Finish visual checklist, whose
    boxes each post a _hidden companion whether ticked or not."""

    NO_CODE = "B0009"

    def _register(self, **extra):
        self._login()
        data = {
            "no_code": self.NO_CODE,
            "production_date": "2026-09-01",
            "shift": "1",
            "ladle_id": "SB1",
            "arrange_pipe": "1",
            "diameter": "700",
            "pipe_class": "K9",
        }
        data.update(extra)
        return self.client.post("/stages/add", data=data,
                                follow_redirects=True)

    def _stages(self):
        pipe = Pipe.query.filter_by(no_code=self.NO_CODE).first()
        self.assertIsNotNone(pipe, "pipe was not registered")
        return {s.stage_name for s in PipeStage.query.filter_by(
            pipe_id=pipe.id).all()}

    def test_the_ccm_band_alone_does_not_start_the_stage(self):
        self._register(**{
            "stage_%s_dim_thick_std_min" % self.ccm: "0.5",
            "stage_%s_dim_thick_std" % self.ccm: "1",
            "stage_%s_dim_thick_std_max" % self.ccm: "3",
        })
        self.assertNotIn(self.ccm, self._stages())

    def test_a_ccm_reading_does_start_it(self):
        self._register(**{
            "stage_%s_dim_thick_std" % self.ccm: "1",
            "stage_%s_dim_thick_p1_1" % self.ccm: "10.6",
        })
        self.assertIn(self.ccm, self._stages())

    def test_an_unticked_visual_checklist_does_not_start_finish(self):
        finish = ProductionStage.name_for_code("finish")
        self._register(**{
            "stage_%s_visual_marking_hidden" % finish: "0",
            "stage_%s_visual_ovality_hidden" % finish: "0",
            "stage_%s_visual_straightness_hidden" % finish: "0",
        })
        self.assertNotIn(finish, self._stages())

    def test_one_ticked_box_does_start_finish(self):
        finish = ProductionStage.name_for_code("finish")
        self._register(**{
            "stage_%s_visual_marking_hidden" % finish: "0",
            "stage_%s_visual_marking" % finish: "1",
        })
        self.assertIn(finish, self._stages())

    def test_a_bare_registration_starts_nothing(self):
        self._register()
        self.assertEqual(self._stages(), set())


class RecordsWorkTest(_Base):
    """Rows written before the parser was fixed are still on prod. They must
    not keep reading as In Progress just because the row exists."""

    def _row(self, **kw):
        return PipeStage(pipe_id=self.pipe.id, stage_name=self.coating, **kw)

    def test_an_empty_row_records_nothing(self):
        self.assertFalse(self._row().records_work())

    def test_a_band_records_nothing(self):
        self.assertFalse(self._row(
            thickness_profile={"cement_std": 5.0,
                               "cement_std_min": 3.0}).records_work())

    def test_a_date_alone_records_nothing(self):
        """The register form prefills it, so it is not evidence."""
        self.assertFalse(self._row(stage_date=date(2026, 9, 1)).records_work())

    def test_an_unticked_checklist_records_nothing(self):
        self.assertFalse(self._row(visual_profile={
            "marking": False, "ovality": False,
            "straightness": False}).records_work())

    def test_real_work_is_recognised(self):
        for kw in (
            {"decision": "Accept"},
            {"notes": "checked by hand"},
            {"visual_profile": {"marking": True, "ovality": False}},
            {"thickness_profile": {
                "cement_points": {"socket_1": {"x": 5.0}}}},
            {"zinc_profile": {"mass": 130.0}},
            {"ring_profile": {"deflection": 3.0}},
        ):
            with self.subTest(kw=kw):
                self.assertTrue(self._row(**kw).records_work())

    def test_the_detail_page_does_not_call_it_in_progress(self):
        self._login()
        db.session.add(self._row(stage_date=date(2026, 9, 1),
                                 thickness_profile={"cement_std": 5.0}))
        db.session.commit()
        html = self.client.get(
            "/stages/%d" % self.pipe.id).get_data(as_text=True)
        self.assertNotIn("In Progress", html)

    def test_the_detail_page_still_says_it_for_real_work(self):
        self._login()
        db.session.add(self._row(notes="operator was here"))
        db.session.commit()
        html = self.client.get(
            "/stages/%d" % self.pipe.id).get_data(as_text=True)
        self.assertIn("In Progress", html)


class ProductCardTest(_Base):
    def test_the_product_card_reads_the_orders_product(self):
        """Pipe.product had no relationship at all, so the card rendered blank
        on every pipe. Most pipes carry no product_id either — the product is
        chosen on the order."""
        from app.models.product import Product, ProductParameter
        from app.models.production_order import ProductionOrder

        zinc = ProductParameter(param_type="ZINC_TYPE", code="Z1",
                                name_en="Zn 130", name_ar="Zn 130",
                                is_active=True)
        internal = ProductParameter(param_type="INTERNAL_FINISH", code="C",
                                    name_en="Cement SRC", name_ar="اسمنت",
                                    is_active=True)
        db.session.add_all([zinc, internal])
        db.session.commit()

        product = Product(product_code="P-700", description_en="DN700 K9",
                          is_active=True, zinc_type_param=zinc,
                          internal_finish_param=internal)
        db.session.add(product)
        db.session.commit()
        order = ProductionOrder(order_number="PO-CARD", target_quantity=5,
                                product_id=product.id)
        db.session.add(order)
        db.session.commit()

        self.pipe.production_order_id = order.id
        db.session.commit()

        self.assertEqual(self.pipe.effective_product().id, product.id)

        self._login()
        html = self.client.get(
            "/stages/%d/edit" % self.pipe.id).get_data(as_text=True)

        def field(fid):
            m = re.search(r'id="%s"[^>]*value="([^"]*)"' % fid, html)
            return m.group(1) if m else None

        self.assertEqual(field("pipeFieldZinc"), "Zn 130")
        self.assertEqual(field("pipeFieldInternal"), "Cement SRC")


if __name__ == "__main__":
    unittest.main()
