"""Approver Comparison report.

The Approval Register lists pipes and names who signed each one off, which
answers "who approved this" but not "how do the approvers compare". This report
is the second half: one row per approver, plus the coverage disclosure — a
comparison built on a third of the pipes is a different claim from one built
on all of them.
"""
from datetime import date
import unittest

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


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

        self.boss = self._user("boss", "Boss", role="admin")
        self.hossam = self._user("hossam", "Hossam Reda")
        self.mona = self._user("mona", "Mona Adel")

        self.lab = ProductionStage.name_for_code("lab")
        self.ccm = ProductionStage.name_for_code("ccm")
        self.day = date(2026, 8, 20)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, username, full_name, role="lab"):
        u = User(username=username, full_name=full_name, role=role,
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        return u

    def _pipe(self, code, decision="ACCEPT", dn=300, approver=None,
              defect=False, when=None):
        p = Pipe(production_date=when or self.day, ladle_id="L1",
                 pipe_code=code, no_code=code, arrange_pipe=1, diameter=dn,
                 pipe_class="K9", lab_decision=decision)
        db.session.add(p)
        db.session.commit()
        if approver is not None:
            db.session.add(PipeStage(pipe_id=p.id, stage_name=self.lab,
                                     decision="Accept",
                                     approved_by_id=approver.id))
        if defect:
            db.session.add(PipeStage(pipe_id=p.id, stage_name=self.ccm,
                                     decision="Accept", has_defect=True))
        db.session.commit()
        return p

    def _login(self, user=None):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str((user or self.boss).id)
            sess["_fresh"] = True


class GroupingTest(_Base):
    def test_one_row_per_approver(self):
        self._pipe("P1", approver=self.hossam)
        self._pipe("P2", approver=self.hossam)
        self._pipe("P3", approver=self.mona)
        rows = analytics_service.approver_comparison({})["approver_rows"]
        self.assertEqual({r["approver"]: r["total"] for r in rows},
                         {"Hossam Reda": 2, "Mona Adel": 1})

    def test_rows_are_sorted_by_volume(self):
        self._pipe("P1", approver=self.mona)
        for code in ("P2", "P3"):
            self._pipe(code, approver=self.hossam)
        rows = analytics_service.approver_comparison({})["approver_rows"]
        self.assertEqual(rows[0]["approver"], "Hossam Reda")

    def test_unattributed_pipes_are_grouped_not_dropped(self):
        self._pipe("P1", approver=self.hossam)
        self._pipe("P2")
        data = analytics_service.approver_comparison({})
        names = {r["approver"] for r in data["approver_rows"]}
        self.assertIn(analytics_service._UNSPECIFIED_APPROVER, names)
        self.assertEqual(sum(r["total"] for r in data["approver_rows"]), 2)

    def test_the_unspecified_bucket_is_not_counted_as_an_approver(self):
        self._pipe("P1", approver=self.hossam)
        self._pipe("P2")
        data = analytics_service.approver_comparison({})
        self.assertEqual(data["summary"]["approvers"], 1)

    def test_modified_by_is_the_fallback_attribution(self):
        p = self._pipe("P1")
        p.modified_by_id = self.mona.id
        db.session.commit()
        rows = analytics_service.approver_comparison({})["approver_rows"]
        self.assertEqual([r["approver"] for r in rows], ["Mona Adel"])

    def test_the_lab_approver_beats_the_modified_by_fallback(self):
        p = self._pipe("P1", approver=self.hossam)
        p.modified_by_id = self.mona.id
        db.session.commit()
        rows = analytics_service.approver_comparison({})["approver_rows"]
        self.assertEqual([r["approver"] for r in rows], ["Hossam Reda"])


class OutcomeTest(_Base):
    def test_accept_and_reject_are_split(self):
        self._pipe("P1", decision="ACCEPT", approver=self.hossam)
        self._pipe("P2", decision="REJECT", approver=self.hossam)
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual((row["accepted"], row["rejected"]), (1, 1))
        self.assertEqual(row["reject_rate"], 50.0)

    def test_the_final_decision_wins_over_the_lab_decision(self):
        p = self._pipe("P1", decision="ACCEPT", approver=self.hossam)
        p.final_decision_value = "REJECT"
        db.session.commit()
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual(row["rejected"], 1)

    def test_rates_are_over_decided_pipes_not_all_pipes(self):
        self._pipe("P1", decision="ACCEPT", approver=self.hossam)
        self._pipe("P2", decision="REJECT", approver=self.hossam)
        self._pipe("P3", decision="WAITING", approver=self.hossam)
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual(row["pending"], 1)
        self.assertEqual(row["decided"], 2)
        self.assertEqual(row["reject_rate"], 50.0,
                         "a large pending queue must not flatter the rate")

    def test_rates_are_none_when_nothing_was_decided(self):
        self._pipe("P1", decision="WAITING", approver=self.hossam)
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertIsNone(row["reject_rate"], "no decisions is not 0% reject")
        self.assertIsNone(row["accept_rate"])

    def test_hold_counts_as_pending_not_reject(self):
        self._pipe("P1", decision="HOLD", approver=self.hossam)
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual((row["rejected"], row["pending"]), (0, 1))

    def test_defect_pipes_are_counted_once_per_pipe(self):
        self._pipe("P1", approver=self.hossam, defect=True)
        self._pipe("P2", approver=self.hossam)
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual(row["defect_pipes"], 1)
        self.assertEqual(row["defect_rate"], 50.0)

    def test_date_range_is_recorded_per_approver(self):
        self._pipe("P1", approver=self.hossam, when=date(2026, 8, 1))
        self._pipe("P2", approver=self.hossam, when=date(2026, 8, 9))
        row = analytics_service.approver_comparison({})["approver_rows"][0]
        self.assertEqual((row["first_date"], row["last_date"]),
                         ("2026-08-01", "2026-08-09"))


class HighlightTest(_Base):
    def test_strictest_needs_more_than_one_approver(self):
        self._pipe("P1", decision="REJECT", approver=self.hossam)
        data = analytics_service.approver_comparison({})
        self.assertIsNone(data["summary"]["strictest"],
                          "worst of one is not a comparison")

    def test_strictest_is_the_highest_reject_rate(self):
        self._pipe("P1", decision="REJECT", approver=self.hossam)
        self._pipe("P2", decision="ACCEPT", approver=self.mona)
        data = analytics_service.approver_comparison({})
        self.assertEqual(data["summary"]["strictest"], "Hossam Reda")
        self.assertEqual(data["summary"]["strictest_rate"], 100.0)

    def test_nobody_is_crowned_when_nothing_was_rejected(self):
        self._pipe("P1", decision="ACCEPT", approver=self.hossam)
        self._pipe("P2", decision="ACCEPT", approver=self.mona)
        data = analytics_service.approver_comparison({})
        self.assertIsNone(data["summary"]["strictest"])


class CoverageAndBreakdownTest(_Base):
    def test_coverage_reports_the_attributed_share(self):
        self._pipe("P1", approver=self.hossam)
        self._pipe("P2")
        self._pipe("P3")
        cov = analytics_service.approver_comparison({})["coverage"]
        self.assertEqual((cov["with_approver"], cov["total_pipes"]), (1, 3))
        self.assertAlmostEqual(cov["pct"], 33.3, places=1)

    def test_coverage_is_zero_not_a_crash_on_an_empty_set(self):
        data = analytics_service.approver_comparison({})
        self.assertEqual(data["coverage"]["pct"], 0)
        self.assertEqual(data["approver_rows"], [])

    def test_dn_breakdown_axis_covers_every_row(self):
        self._pipe("P1", dn=300, approver=self.hossam)
        self._pipe("P2", dn=800, approver=self.hossam)
        self._pipe("P3", dn=300, approver=self.mona)
        data = analytics_service.approver_comparison({})
        self.assertEqual(data["dn_codes"], ["DN300", "DN800"])
        hossam = next(r for r in data["approver_rows"]
                      if r["approver"] == "Hossam Reda")
        self.assertEqual(hossam["by_dn"], {"DN300": 1, "DN800": 1})

    def test_date_filters_narrow_the_population(self):
        self._pipe("P1", approver=self.hossam, when=date(2026, 8, 1))
        self._pipe("P2", approver=self.hossam, when=date(2026, 8, 20))
        data = analytics_service.approver_comparison(
            {"date_from": date(2026, 8, 10)})
        self.assertEqual(data["approver_rows"][0]["total"], 1)


class RouteTest(_Base):
    def test_the_page_renders(self):
        self._pipe("P1", decision="REJECT", approver=self.hossam)
        self._pipe("P2", decision="ACCEPT", approver=self.mona)
        self._login()
        resp = self.client.get("/reports/approver-comparison")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Hossam Reda", resp.get_data(as_text=True))

    def test_the_page_renders_with_no_data(self):
        self._login()
        self.assertEqual(
            self.client.get("/reports/approver-comparison").status_code, 200)

    def test_the_excel_export_is_wired(self):
        self._pipe("P1", approver=self.hossam)
        self._login()
        resp = self.client.get("/reports/export/approver-comparison.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Unknown report", resp.data)

    def test_the_screen_has_its_own_permission_key(self):
        from app.models.permission import MODULES
        self.assertIn("approver_comparison", MODULES["reports"])


if __name__ == "__main__":
    unittest.main()
