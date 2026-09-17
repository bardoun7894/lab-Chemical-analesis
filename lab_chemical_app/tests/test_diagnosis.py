"""Diagnosis engine — every stopped node must say what to do about it."""

import unittest
from datetime import date

from flask import g

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services import diagnosis_service as dx
from app.services import traceability_service as ts


class DiagnosisTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.admin = User(username="admin", full_name="Admin",
                          role="super_admin", is_active=True)
        self.admin.set_password("x")
        db.session.add(self.admin)
        self.order = ProductionOrder(order_number="PO-1", target_quantity=6)
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _dx(self, kind, id):
        """diagnose() builds real links, so url_for needs a request context.
        Scoped to the call — pushing one for the whole test would make the
        client's own requests resolve current_user against it."""
        with self.app.test_request_context():
            return dx.diagnose(kind, id)

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True
        g.pop("_login_user", None)

    def _ladle(self, decision, **kw):
        ladle = ChemicalAnalysis(test_date=date(2026, 8, 29), ladle_no=1,
                                 ladle_id="111082026", decision=decision,
                                 production_order_id=self.order.id, **kw)
        db.session.add(ladle)
        db.session.commit()
        return ladle

    def _pipe(self, code="P1", lab=None, role=None, ladle_id=None, cascade=None):
        p = Pipe(production_date=date(2026, 8, 29), ladle_id=ladle_id,
                 pipe_code=code, no_code=code, arrange_pipe=1, diameter=300,
                 pipe_class="K9", production_order_id=self.order.id,
                 lab_decision=lab, mechanical_test_role=role,
                 cascade_from=cascade)
        db.session.add(p)
        db.session.commit()
        return p

    # --- every diagnosis must be actionable ------------------------------

    def _assert_actionable(self, d):
        self.assertIsNotNone(d)
        self.assertTrue(d["what_en"].strip())
        self.assertTrue(d["what_ar"].strip())
        self.assertTrue(d["actions"], "a diagnosis with no way out is useless")
        self.assertTrue(any(a["primary"] for a in d["actions"]),
                        "one action must be the recommended one")
        for a in d["actions"]:
            self.assertTrue(a["url"].startswith("/"), a)

    def test_rejected_heat_says_it_blocks_everything_and_links_to_it(self):
        ladle = self._ladle("تالف", carbon=1.0, magnesium=0.001)
        d = self._dx("ladle", ladle.id)
        self._assert_actionable(d)
        self.assertEqual(d["severity"], "fail")
        self.assertIn("blocked", d["what_en"])
        self.assertTrue(d["why"], "must name the elements that failed")
        self.assertTrue(any("/chemical/" in a["url"] for a in d["actions"]))

    def test_mechanical_fail_explains_the_hold_rule_and_offers_a_retest(self):
        ladle = self._ladle("فحص أخيرة فقط")
        t = MechanicalTest(test_date=date(2026, 8, 29), ladle_id=ladle.ladle_id,
                           pipe_code="P1", decision="REJECT", status="ACTIVE",
                           tensile_mpa=310.0)
        db.session.add(t)
        db.session.commit()

        d = self._dx("mechanical", t.id)
        self._assert_actionable(d)
        self.assertIn("never scraps", d["what_en"])
        self.assertTrue(any("/mechanical/add" in a["url"] for a in d["actions"]))
        self.assertTrue(any("310" in w for w in d["why"]))

    def test_undecided_mechanical_test_is_flagged_as_blocking(self):
        ladle = self._ladle("فحص أخيرة فقط")
        t = MechanicalTest(test_date=date(2026, 8, 29), ladle_id=ladle.ladle_id,
                           pipe_code="P1", decision=None, status="ACTIVE")
        db.session.add(t)
        db.session.commit()
        d = self._dx("mechanical", t.id)
        self._assert_actionable(d)
        self.assertIn("no verdict", d["what_en"])

    def test_rejected_stage_links_into_the_console_at_that_stage(self):
        p = self._pipe(lab="ACCEPT")
        st = PipeStage(pipe_id=p.id, stage_name="Coating", decision="Reject",
                       defect_reason="thin coating")
        db.session.add(st)
        db.session.commit()

        d = self._dx("stage", st.id)
        self._assert_actionable(d)
        self.assertIn("Coating", d["what_en"])
        console = [a for a in d["actions"] if "/stages/console" in a["url"]]
        self.assertTrue(console)
        # the link must land on this pipe at this stage, not the top of the list
        self.assertIn("q=P1", console[0]["url"])
        self.assertIn("stage=Coating", console[0]["url"])
        self.assertTrue(any("thin coating" in w for w in d["why"]))

    def test_out_of_band_readings_are_quoted_as_the_evidence(self):
        p = self._pipe(lab="ACCEPT")
        st = PipeStage(pipe_id=p.id, stage_name="Coating", decision="Reject",
                       thickness_profile={
                           "cement": [2.0, None, None, None, None, None],
                           "coating": [None] * 6,
                           "cement_std": 5.0, "cement_std_min": 4.0,
                           "cement_std_max": 6.0})
        db.session.add(st)
        db.session.commit()
        d = self._dx("stage", st.id)
        self.assertTrue(any("cement m1" in w for w in d["why"]), d["why"])

    def test_rework_stage_says_it_re_runs(self):
        p = self._pipe(lab="ACCEPT")
        st = PipeStage(pipe_id=p.id, stage_name="Annealing", decision="Rework")
        db.session.add(st)
        db.session.commit()
        d = self._dx("stage", st.id)
        self._assert_actionable(d)
        self.assertIn("re-runs", d["what_en"])
        self.assertEqual(d["severity"], "hold")

    def test_blocked_pipe_points_at_the_heat_that_caused_it(self):
        ladle = self._ladle("تالف")
        p = self._pipe(lab="BLOCKED", ladle_id=ladle.ladle_id,
                       cascade="Ladle Rejected")
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertEqual(d["severity"], "blocked")
        self.assertIn("terminal", d["what_en"])
        self.assertTrue(any(str(ladle.id) in a["url"] and "/chemical/" in a["url"]
                            for a in d["actions"]))

    def test_cascaded_hold_explains_the_retest_route(self):
        ladle = self._ladle("فحص أخيرة فقط")
        p = self._pipe(lab="HOLD", ladle_id=ladle.ladle_id,
                       cascade="cascade from Last(Fail)")
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("retest", d["what_en"])
        primary = [a for a in d["actions"] if a["primary"]][0]
        self.assertIn("/mechanical/add", primary["url"])

    def test_waiting_sample_pipe_asks_for_its_test(self):
        ladle = self._ladle("فحص أخيرة فقط")
        p = self._pipe(lab="WAITING", role="LAST", ladle_id=ladle.ladle_id)
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("LAST sample", d["what_en"])

    def test_waiting_non_sample_pipe_points_at_the_heat(self):
        ladle = self._ladle("فحص أخيرة فقط")
        p = self._pipe(lab="WAITING", role="ANY", ladle_id=ladle.ladle_id)
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("sample result", d["what_en"])

    def test_pipe_with_no_heat_says_so(self):
        p = self._pipe(lab="WAITING")
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("No heat recorded", d["what_en"])

    def test_lab_accepted_but_a_stage_turned_it(self):
        """The hardest case to read on the floor: the lab line says ACCEPT and
        the pipe is REJECT overall. Found on production data (X11-4), where it
        produced no advice at all."""
        ladle = self._ladle("فحص الشحنة 100%")
        p = self._pipe(lab="ACCEPT", role="ALL", ladle_id=ladle.ladle_id,
                       cascade="individual")
        p.final_decision_value = "REJECT"
        db.session.add(PipeStage(pipe_id=p.id, stage_name="Hydrotest",
                                 decision="Reject", defect_reason="leak"))
        db.session.commit()

        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("Hydrotest", d["what_en"])
        self.assertIn("worst step", d["what_en"])
        self.assertTrue(any("leak" in w for w in d["why"]))
        console = [a for a in d["actions"] if "/stages/console" in a["url"]]
        self.assertIn("stage=Hydrotest", console[0]["url"])

    def test_the_worst_stage_wins_when_several_turned_it(self):
        p = self._pipe(lab="ACCEPT")
        p.final_decision_value = "REJECT"
        db.session.add(PipeStage(pipe_id=p.id, stage_name="Annealing",
                                 decision="Hold"))
        db.session.add(PipeStage(pipe_id=p.id, stage_name="Zinc",
                                 decision="Reject"))
        db.session.commit()
        d = self._dx("pipe", p.id)
        self.assertIn("Zinc", d["what_en"])

    def test_a_contradictory_record_says_so_instead_of_going_quiet(self):
        p = self._pipe(lab="ACCEPT")
        p.final_decision_value = "REJECT"   # nothing on any stage explains it
        db.session.commit()
        d = self._dx("pipe", p.id)
        self._assert_actionable(d)
        self.assertIn("inconsistent", d["what_en"])

    def test_healthy_things_produce_no_diagnosis(self):
        ladle = self._ladle("فحص أخيرة فقط")
        p = self._pipe(lab="ACCEPT", ladle_id=ladle.ladle_id)
        st = PipeStage(pipe_id=p.id, stage_name="CCM", decision="Accept")
        db.session.add(st)
        db.session.commit()
        self.assertIsNone(self._dx("ladle", ladle.id))
        self.assertIsNone(self._dx("pipe", p.id))
        self.assertIsNone(self._dx("stage", st.id))

    def test_unknown_ids_do_not_raise(self):
        for kind in ("ladle", "pipe", "stage", "mechanical", "order", "bogus"):
            self.assertIsNone(dx.diagnose(kind, 999999), kind)

    # --- it reaches the screen -------------------------------------------

    def test_inspector_renders_the_fix_button(self):
        self._login()
        p = self._pipe(lab="ACCEPT")
        st = PipeStage(pipe_id=p.id, stage_name="Coating", decision="Reject",
                       defect_reason="thin coating")
        db.session.add(st)
        db.session.commit()

        html = self.client.get(
            "/reports/traceability-tree/node/stage/%d" % st.id
        ).get_data(as_text=True)
        self.assertIn("trace-diag", html)
        self.assertIn("Rejected at Coating", html)
        self.assertIn("/stages/console", html)
        self.assertIn("thin coating", html)

    def test_node_detail_carries_the_diagnosis(self):
        ladle = self._ladle("تالف")
        with self.app.test_request_context():
            d = ts.node_detail("ladle", ladle.id)
        self.assertIsNotNone(d["diagnosis"])
        self.assertTrue(d["diagnosis"]["actions"])


if __name__ == "__main__":
    unittest.main()
