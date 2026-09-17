"""Rework by Stage report (/reports/rework-report).

The counting rules that matter:

A. A stage record counts as rework if it EVER carried a rework decision.
   ``PipeStage.decision`` is overwritten on re-decision, so a Rework that was
   later accepted only survives in ``PipeStageHistory``.
B. Repeated history snapshots of the same stage record count once — the report
   counts reworked stage records, not audit rows.
C. Arabic decision spellings count the same as the English ones.
D. A plain Hold is not rework (the pipe is parked, no work is repeated).
E. Every active stage gets a row, including the ones with no rework at all.
"""

import unittest
from datetime import date

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage_history import PipeStageHistory
from app.models.user import User
from app.services import analytics_service


DAY = date(2026, 8, 18)


class ReworkReportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # -- helpers ---------------------------------------------------------

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, code):
        p = Pipe(pipe_code=code, no_code=code, production_date=DAY, diameter=300)
        db.session.add(p)
        db.session.commit()
        return p

    def _stage(self, pipe, stage_name, decision):
        s = PipeStage(
            pipe_id=pipe.id, stage_name=stage_name,
            decision=decision, stage_date=DAY,
        )
        db.session.add(s)
        db.session.commit()
        return s

    def _hist(self, stage, decision, action="update"):
        h = PipeStageHistory(
            pipe_stage_id=stage.id, pipe_id=stage.pipe_id,
            stage_name=stage.stage_name, decision=decision, action=action,
        )
        db.session.add(h)
        db.session.commit()
        return h

    def _report(self):
        filters = analytics_service.parse_filters(
            MultiDict([("date_from", DAY.isoformat()), ("date_to", DAY.isoformat())])
        )
        data = analytics_service.rework_report(filters)
        return data, {r["stage"]: r for r in data["rows"]}

    def _fixture(self):
        # P1 — Zinc still open on Rework (English).
        p1 = self._pipe("RW-1")
        s1 = self._stage(p1, "Zinc", "Rework")
        self._hist(s1, "Rework", action="create")

        # P2 — Zinc reworked twice, then accepted. Live row says "Accept".
        p2 = self._pipe("RW-2")
        s2 = self._stage(p2, "Zinc", "Accept")
        self._hist(s2, "Rework", action="create")
        self._hist(s2, "Rework")
        self._hist(s2, "Accept")

        # P3 — Cutting on the Arabic Retest spelling.
        p3 = self._pipe("RW-3")
        s3 = self._stage(p3, "Cutting", "إعادة اختبار")
        self._hist(s3, "إعادة اختبار", action="create")

        # P4 — Hold and Accept: never rework.
        p4 = self._pipe("RW-4")
        self._stage(p4, "Zinc", "Hold")
        self._stage(p4, "Cutting", "Accept")

    # -- vocabulary ------------------------------------------------------

    def test_is_rework_vocabulary(self):
        for value in ("Rework", "إعادة عمل", "retest", "  Resample ",
                      "إعادة معالجة حرارية", "فحص البنية المجهرية"):
            self.assertTrue(analytics_service.is_rework(value), value)
        for value in ("Hold", "حجز", "Accept", "Reject", "", None):
            self.assertFalse(analytics_service.is_rework(value), value)

    # -- counting --------------------------------------------------------

    def test_rework_counted_after_the_stage_moved_on(self):
        """A. + B. — reworked-then-accepted counts once, not per audit row."""
        self._fixture()
        _, rows = self._report()
        zinc = rows["Zinc"]
        self.assertEqual(zinc["total"], 3)     # Rework + Accept + Hold
        self.assertEqual(zinc["rework"], 2)    # P1 (open) + P2 (since accepted)
        self.assertEqual(zinc["open"], 1)      # only P1 still sits on Rework
        self.assertEqual(zinc["pipes"], 2)
        self.assertEqual(zinc["rate"], 66.7)

    def test_arabic_decision_counts(self):
        """C. — the AR spelling is not invisible to the report."""
        self._fixture()
        _, rows = self._report()
        cutting = rows["Cutting"]
        self.assertEqual(cutting["rework"], 1)
        self.assertEqual(cutting["Retest"], 1)
        self.assertEqual(cutting["Rework"], 0)

    def test_hold_is_not_rework(self):
        """D. — a parked pipe repeats no work."""
        p = self._pipe("RW-HOLD")
        s = self._stage(p, "Zinc", "Hold")
        self._hist(s, "Hold", action="create")
        data, rows = self._report()
        self.assertEqual(rows["Zinc"]["rework"], 0)
        self.assertEqual(data["summary"]["total_rework"], 0)

    def test_every_active_stage_gets_a_row(self):
        """E. — quiet stages report zero instead of disappearing."""
        self._fixture()
        data, rows = self._report()
        from app.models.stage import ProductionStage

        for name in ProductionStage.active_names():
            self.assertIn(name, rows)
        self.assertEqual(rows["Cement"]["rework"], 0)
        self.assertEqual(rows["Cement"]["rate"], 0.0)

    def test_summary_totals(self):
        self._fixture()
        data, _ = self._report()
        s = data["summary"]
        self.assertEqual(s["total_rework"], 3)
        self.assertEqual(s["open_rework"], 2)
        self.assertEqual(s["pipes_reworked"], 3)
        self.assertEqual(s["worst_stage"], "Zinc")  # 66.7% vs Cutting's 50%

    def test_date_filter_excludes_other_days(self):
        self._fixture()
        filters = analytics_service.parse_filters(
            MultiDict([("date_from", "2026-08-19"), ("date_to", "2026-08-19")])
        )
        data = analytics_service.rework_report(filters)
        self.assertEqual(data["summary"]["total_rework"], 0)

    # -- routes ----------------------------------------------------------

    def test_page_and_excel_export_render(self):
        self._fixture()
        self._login()
        page = self.client.get("/reports/rework-report")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Zinc", page.data)

        xlsx = self.client.get("/reports/export/rework-report.xlsx")
        self.assertEqual(xlsx.status_code, 200)
        self.assertTrue(len(xlsx.data) > 0)

    def test_retired_stage_name_folds_into_current(self):
        """F. — a rework recorded under the pre-collapse "Lab" name belongs to
        Lab Approval, not to a phantom stage with no denominator."""
        p = self._pipe("RW-LAB")
        s = self._stage(p, "Lab Approval", "Accept")
        # History as it was written before collapse_lab_approval() renamed the
        # stage: pipe_stages was migrated, pipe_stage_history was not.
        db.session.add(PipeStageHistory(
            pipe_stage_id=s.id, pipe_id=p.id, stage_name="Lab",
            decision="Rework", action="create",
        ))
        db.session.commit()

        data, rows = self._report()
        self.assertNotIn("Lab", rows)
        self.assertEqual(rows["Lab Approval"]["rework"], 1)
        self.assertEqual(rows["Lab Approval"]["total"], 1)
        self.assertEqual(rows["Lab Approval"]["rate"], 100.0)
        self.assertEqual(data["summary"]["total_rework"], 1)

    def test_alias_map_covers_the_lab_rename(self):
        self.assertEqual(
            analytics_service.stage_name_aliases().get("Lab"), "Lab Approval"
        )

    def test_reports_index_links_the_report(self):
        self._login()
        index = self.client.get("/reports/")
        self.assertEqual(index.status_code, 200)
        self.assertIn(b"/reports/rework-report", index.data)


if __name__ == "__main__":
    unittest.main()
