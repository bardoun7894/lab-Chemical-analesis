"""Daily report: filters, date range, per-stage, defects, lab and sign-off.

The daily panel was the one report on the BI dashboard that ignored the page
filters entirely, covered exactly one day, read reject reasons from a column
that is empty on most production pipes, and ended in three bare captions
pretending to be a sign-off block.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import bi_service


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

        self.user = User(username="qc", full_name="Hossam Reda", role="admin",
                         is_active=True)
        self.user.set_password("x")
        db.session.add(self.user)
        db.session.commit()
        self.user_id = self.user.id

        self.d1 = date(2026, 8, 18)
        self.d2 = date(2026, 8, 19)
        self.ccm = ProductionStage.name_for_code("ccm")
        self.zinc = ProductionStage.name_for_code("zinc")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, day, shift=1, diameter=300, decision="ACCEPT"):
        pipe = Pipe(production_date=day, ladle_id=f"L{code}", pipe_code=code,
                    no_code=code, arrange_pipe=1, diameter=diameter,
                    pipe_class="K9", shift=shift, lab_decision=decision)
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class FilterTest(_Base):
    def test_shift_filter_narrows_the_report(self):
        self._pipe("A1", self.d1, shift=1)
        self._pipe("A2", self.d1, shift=2)
        both = bi_service.daily_report(self.d1)
        one = bi_service.daily_report(self.d1, {"shift": 1})
        self.assertEqual(both["today"]["produced"], 2)
        self.assertEqual(one["today"]["produced"], 1)

    def test_diameter_filter_applies(self):
        self._pipe("A1", self.d1, diameter=300)
        self._pipe("A2", self.d1, diameter=800)
        report = bi_service.daily_report(self.d1, {"diameter": 800})
        self.assertEqual(report["today"]["produced"], 1)

    def test_a_filter_that_matches_nothing_returns_none_not_a_crash(self):
        self._pipe("A1", self.d1, shift=1)
        self.assertIsNone(bi_service.daily_report(self.d1, {"shift": 3}))

    def test_filters_are_echoed_back_for_the_form(self):
        self._pipe("A1", self.d1, shift=2)
        report = bi_service.daily_report(self.d1, {"shift": 2})
        self.assertEqual(report["filters"]["shift"], 2)


class RangeTest(_Base):
    def test_range_aggregates_every_day(self):
        self._pipe("A1", self.d1)
        self._pipe("A2", self.d2)
        report = bi_service.daily_report(self.d1, None, self.d2)
        self.assertTrue(report["is_range"])
        self.assertEqual(report["today"]["produced"], 2)
        self.assertEqual(len(report["per_day"]), 2)

    def test_per_day_rows_keep_each_day_separate(self):
        self._pipe("A1", self.d1)
        self._pipe("A2", self.d2)
        self._pipe("A3", self.d2)
        rows = {r["date"]: r for r in
                bi_service.daily_report(self.d1, None, self.d2)["per_day"]}
        self.assertEqual(rows[self.d1]["produced"], 1)
        self.assertEqual(rows[self.d2]["produced"], 2)

    def test_range_drops_the_yesterday_comparison(self):
        self._pipe("A1", self.d1)
        self._pipe("A2", self.d2)
        report = bi_service.daily_report(self.d1, None, self.d2)
        self.assertIsNone(report["yest"],
                          "yesterday has no meaning against a span")
        self.assertIsNone(report["week_avg"])

    def test_single_day_is_unchanged_when_no_end_date(self):
        self._pipe("A1", self.d1)
        self._pipe("A2", self.d2)
        report = bi_service.daily_report(self.d1)
        self.assertFalse(report["is_range"])
        self.assertEqual(report["today"]["produced"], 1)
        self.assertEqual(report["per_day"], [])

    def test_end_date_before_start_is_not_a_range(self):
        self._pipe("A1", self.d2)
        report = bi_service.daily_report(self.d2, None, self.d1)
        self.assertFalse(report["is_range"])


class StageAndDefectTest(_Base):
    def test_per_stage_rows(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.ccm,
                                 decision="Accept"))
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.zinc,
                                 decision="Reject"))
        db.session.commit()
        rows = {r["stage"]: r for r in
                bi_service.daily_report(self.d1)["stage_rows"]}
        self.assertEqual(rows[self.ccm]["acc"], 1)
        self.assertEqual(rows[self.zinc]["rej"], 1)
        self.assertEqual(rows[self.zinc]["rej_pct"], 100.0)

    def test_pending_is_not_counted_as_good(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.ccm,
                                 decision=None))
        db.session.commit()
        row = bi_service.daily_report(self.d1)["stage_rows"][0]
        self.assertEqual(row["pending"], 1)
        self.assertEqual(row["rej_pct"], 0.0)
        self.assertEqual(row["acc"], 0)

    def test_defects_come_from_the_stage_rows(self):
        pipe = self._pipe("A1", self.d1, diameter=300)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.zinc,
                                 has_defect=True, defect_reason="قطع طوق"))
        db.session.commit()
        defects = bi_service.daily_report(self.d1)["defects"]
        self.assertEqual(defects["total"], 1)
        self.assertEqual(defects["rows"][0]["label"], "قطع طوق")

    def test_worst_defect_per_diameter(self):
        # One stage row per pipe: (pipe_id, stage_name) is unique.
        for code in ("A1", "A1b"):
            big = self._pipe(code, self.d1, diameter=800)
            db.session.add(PipeStage(pipe_id=big.id, stage_name=self.zinc,
                                     has_defect=True, defect_type="مناوله"))
        small = self._pipe("A2", self.d1, diameter=300)
        db.session.add(PipeStage(pipe_id=small.id, stage_name=self.zinc,
                                 has_defect=True, defect_type="زهر رمادى"))
        db.session.commit()
        by_dn = bi_service.daily_report(self.d1)["defects"]["by_dn"]
        self.assertEqual(by_dn[0]["dn"], 800)
        self.assertEqual(by_dn[0]["worst"], "مناوله")

    def test_defect_falls_back_to_type_then_to_unknown(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.zinc,
                                 has_defect=True))
        db.session.commit()
        rows = bi_service.daily_report(self.d1)["defects"]["rows"]
        self.assertEqual(rows[0]["label"], "غير محدد")


class LabAndSignoffTest(_Base):
    def test_lab_counts_are_reported(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(ChemicalAnalysis(ladle_id=pipe.ladle_id, ladle_no=pipe.ladle_id,
                                        test_date=self.d1, decision="مقبول"))
        db.session.add(MechanicalTest(test_date=self.d1, decision="PASS",
                                      status="ACTIVE"))
        db.session.commit()
        lab = bi_service.daily_report(self.d1)["lab"]
        self.assertEqual(lab["chem_count"], 1)
        self.assertEqual(lab["mech_count"], 1)
        self.assertEqual(lab["chem_decisions"]["مقبول"], 1)
        self.assertEqual(lab["mech_results"]["PASS"], 1)

    def test_superseded_mechanical_tests_are_excluded(self):
        self._pipe("A1", self.d1)
        db.session.add(MechanicalTest(test_date=self.d1, decision="PASS",
                                      status="SUPERSEDED"))
        db.session.commit()
        self.assertIsNone(bi_service.daily_report(self.d1)["lab"])

    def test_no_lab_data_is_none_not_an_empty_shell(self):
        self._pipe("A1", self.d1)
        self.assertIsNone(bi_service.daily_report(self.d1)["lab"])

    def test_signoff_names_the_days_approver(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.user_id))
        db.session.commit()
        rows = bi_service.daily_report(self.d1)["signoff"]
        self.assertEqual(len(rows), 3)
        self.assertIn("Hossam Reda", [r["name"] for r in rows])

    def test_signoff_leaves_a_blank_line_when_nobody_approved(self):
        self._pipe("A1", self.d1)
        rows = bi_service.daily_report(self.d1)["signoff"]
        self.assertEqual([r["name"] for r in rows], ["", "", ""])
        self.assertEqual([r["role"] for r in rows],
                         ["مسؤول الإنتاج", "مراقب الجودة", "مدير التشغيل"])


class RenderTest(_Base):
    def test_all_new_sections_render(self):
        pipe = self._pipe("A1", self.d1)
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.zinc,
                                 decision="Reject", has_defect=True,
                                 defect_reason="قطع طوق",
                                 approved_by_id=self.user_id))
        db.session.add(ChemicalAnalysis(ladle_id=pipe.ladle_id, ladle_no=pipe.ladle_id,
                                        test_date=self.d1, decision="مقبول"))
        db.session.commit()
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.d1.isoformat()}"
        ).get_data(as_text=True)
        for token in ("تحليل بالمرحلة", "العيوب الأكثر تكراراً",
                      "العيوب حسب القطر", "نتائج المعمل", "الاعتماد",
                      "قطع طوق", "Hossam Reda"):
            self.assertIn(token, html, f"missing section/value: {token}")

    def test_range_form_and_per_day_table_render(self):
        self._pipe("A1", self.d1)
        self._pipe("A2", self.d2)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.d1.isoformat()}"
            f"&rpt_to={self.d2.isoformat()}"
        ).get_data(as_text=True)
        self.assertIn("الأداء اليومي خلال الفترة", html)
        self.assertIn('name="rpt_to"', html)
        self.assertIn("2 يوم إنتاج", html)

    def test_shift_select_reflects_the_active_filter(self):
        self._pipe("A1", self.d1, shift=2)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.d1.isoformat()}&shift=2"
        ).get_data(as_text=True)
        self.assertIn('<option value="2" selected>وردية 2</option>', html)
        self.assertIn("وردية 2 فقط", html)

    def test_bad_date_does_not_500(self):
        self._pipe("A1", self.d1)
        self._login()
        resp = self.client.get("/reports/bi-dashboard?rpt_date=not-a-date")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class AiShapeTest(_Base):
    """The AI prompt must carry the report's own aggregates, never raw rows."""

    def test_prompt_carries_the_computed_numbers(self):
        from app.services.ai_service import build_report_prompt

        prompt = build_report_prompt("daily", {
            "period": "2026-08-18",
            "produced": 64, "decided": 60, "rejects": 3, "rej_pct": 5.0,
            "saving": {"pct": 6.39, "n": 44, "of": 60},
            "stage_rows": [{"stage": "CCM", "total": 60, "acc": 57,
                            "rej": 3, "rej_pct": 5.0}],
            "top_reasons": [{"reason": "قطع طوق", "count": 2, "pct": 66.7}],
            "defects": [{"label": "مناوله", "count": 2}],
        })
        for token in ("2026-08-18", "64", "5.00%", "CCM", "قطع طوق",
                      "مناوله", "44", "60"):
            self.assertIn(token, prompt, f"missing {token}")

    def test_missing_saving_coverage_is_stated_not_shown_as_zero(self):
        from app.services.ai_service import build_report_prompt

        prompt = build_report_prompt("daily", {
            "period": "2026-08-18", "produced": 1, "decided": 1,
            "rejects": 0, "rej_pct": 0.0,
            "saving": {"pct": 0.0, "n": 0, "of": 1},
            "stage_rows": [], "top_reasons": [], "defects": [],
        })
        self.assertIn("غير متاحة", prompt)

    def test_endpoint_returns_404_when_the_selection_has_no_data(self):
        self._login()
        resp = self.client.get(
            "/reports/api/ai-summary/daily?rpt_date=2026-08-18")
        self.assertEqual(resp.status_code, 404)

    def test_ai_button_renders_with_the_current_selection(self):
        self._pipe("A1", self.d1, shift=2)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.d1.isoformat()}&shift=2"
        ).get_data(as_text=True)
        self.assertIn("توليد التحليل", html)
        self.assertIn("ai-summary/daily", html)
