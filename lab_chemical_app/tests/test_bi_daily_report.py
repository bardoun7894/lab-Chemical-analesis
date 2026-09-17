"""Tests for the v52-style daily report (bi_service.daily_report + routes).

Fixtures hand-counted; denominators are DECIDED pipes only.
"""
from datetime import date
import unittest

from flask import g

from app import create_app, db
from app.models.chemical import Machine
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User
from app.services import bi_service

TODAY = date(2026, 8, 17)
YESTERDAY = date(2026, 8, 15)  # previous *production* date (gap on the 16th)
WEEK = [date(2026, 8, d) for d in (10, 11, 12, 13, 14, 15)]


def _pipe(no_code, day, decision="ACCEPT", dn=100, cls="K9",
          actual=100.0, iso=120.0, engineer="Eng1", shift=1,
          machine=None, mold=None, reason=None, lab=None):
    p = Pipe(
        production_date=day, shift=shift, ladle_id="L1", pipe_code=no_code,
        no_code=no_code, arrange_pipe=1, diameter=dn, pipe_class=cls,
        actual_weight=actual, iso_weight=iso, mold_number=mold,
        final_decision_value=decision, final_decision_reason=reason,
        lab_decision=lab, shift_engineer=engineer,
    )
    db.session.add(p)
    db.session.flush()
    if machine is not None:
        db.session.add(PipeStage(
            pipe_id=p.id, stage_name="CCM", decision="ACCEPT",
            machine_id=machine.id, stage_date=day,
        ))
    db.session.commit()
    return p


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.m10 = Machine(machine_code="M10", is_active=True)
        self.m11 = Machine(machine_code="M11", is_active=True)
        db.session.add_all([self.m10, self.m11])
        db.session.commit()

        # The report's alert thresholds are the ones an admin configured, and
        # fall back to the built-in 7%/4% only where nothing is set. That
        # setting is a real file on disk shared with the running app, so these
        # tests pin it to "nothing configured" and put it back afterwards —
        # otherwise which alerts fire depends on the machine the suite runs on.
        from app.routes.admin import APP_SETTINGS_PATH, load_app_settings, \
            save_app_settings
        from app.services import kpi_target_service

        self._settings_path = APP_SETTINGS_PATH
        try:
            with open(self._settings_path, "rb") as f:
                self._settings_backup = f.read()
        except FileNotFoundError:
            self._settings_backup = None
        settings = load_app_settings()
        settings[kpi_target_service.SETTINGS_KEY] = {}
        save_app_settings(settings)

    def tearDown(self):
        import os

        if self._settings_backup is None:
            try:
                os.remove(self._settings_path)
            except FileNotFoundError:
                pass
        else:
            with open(self._settings_path, "wb") as f:
                f.write(self._settings_backup)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()


class DailyReportStatsTestCase(_Base):
    def _seed(self):
        # Today: 4 decided (2 reject), 1 pending — pending excluded everywhere
        _pipe("T1", TODAY, "ACCEPT", machine=self.m10)
        _pipe("T2", TODAY, "REJECT", machine=self.m10, reason="شعرية",
              mold="M-77")
        _pipe("T3", TODAY, "REJECT", machine=self.m11, reason="شعرية",
              mold="M-77", engineer="Eng2", shift=2)
        _pipe("T4", TODAY, "accept", machine=self.m11, reason="nan")
        _pipe("T5", TODAY, None, machine=self.m10)  # undecided
        # Yesterday: 2 decided, 0 rejected
        _pipe("Y1", YESTERDAY, "ACCEPT", machine=self.m10)
        _pipe("Y2", YESTERDAY, "ACCEPT", machine=self.m10)
        # Week filler (before yesterday)
        for i, d in enumerate(WEEK[:-1]):
            _pipe(f"W{i}", d, "ACCEPT", machine=self.m10)

    def test_stats_decided_only_denominators(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        t = r["today"]
        self.assertEqual(t["n"], 4)       # T5 pending excluded
        self.assertEqual(t["rejects"], 2)
        self.assertAlmostEqual(t["rej_pct"], 50.0)
        # saving = (iso - act) / iso over decided: (480-400)/480
        self.assertAlmostEqual(t["sav_pct"], 80 / 480 * 100, places=3)

    def test_yesterday_is_previous_production_date(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        self.assertEqual(r["yesterday"], "2026-08-15")  # skipped the 16th
        self.assertEqual(r["yest"]["n"], 2)
        self.assertEqual(r["yest"]["rejects"], 0)

    def test_week_average_pooled(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        # window = WEEK (6 days): 5 filler + yesterday = 7 pipes, 0 rejects
        self.assertEqual(r["week_avg"]["rej_pct"], 0.0)
        self.assertEqual(r["week_avg"]["n_per_day"], 1)  # round(7/6)

    def test_auto_pick_latest_date(self):
        self._seed()
        r = bi_service.daily_report(None)
        self.assertEqual(r["date"], TODAY.isoformat())

    def test_unknown_date_falls_back_to_latest(self):
        self._seed()
        r = bi_service.daily_report(date(2020, 1, 1))
        self.assertEqual(r["date"], TODAY.isoformat())

    def test_machine_rows_with_yesterday_pct(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        by_key = {row["key"]: row for row in r["machine_rows"]}
        self.assertEqual(by_key["M10"]["total"], 2)
        self.assertEqual(by_key["M10"]["rejects"], 1)
        self.assertEqual(by_key["M10"]["y_pct"], 0.0)
        self.assertEqual(by_key["M11"]["y_pct"], None)  # no M11 yesterday
        # worst-first ordering: M11 (1/2) vs M10 (1/2) tie — both 50%
        self.assertEqual(len(r["machine_rows"]), 2)

    def test_reasons_filter_nan_and_blank(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        reasons = [x["reason"] for x in r["top_reasons"]]
        self.assertEqual(reasons, ["شعرية"])
        self.assertEqual(r["top_reasons"][0]["count"], 2)
        self.assertEqual(r["top_reasons"][0]["pct"], 100.0)

    def test_top_molds(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        self.assertEqual(r["top_molds"], [{"mold": "M-77", "count": 2}])

    def test_status_thresholds(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        self.assertEqual(r["status"]["color"], "#dc2626")  # 50% > 7%

    def test_alerts_triggers(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        joined = " | ".join(r["alerts"])
        self.assertIn("تدخل فوري", joined)          # machine >= 7%
        self.assertIn("تجاوز الحد المسموح", joined)  # total >= 7%
        self.assertNotIn("تكرر", joined)            # top reason 2 < 5
        self.assertNotIn("Saving سالب", joined)     # saving positive

    def test_shift_detail_groups_and_approvers(self):
        self._seed()
        u = User(username="qc1", full_name="QC One", role="admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        t2 = Pipe.query.filter_by(no_code="T2").one()
        ccm = PipeStage.query.filter_by(pipe_id=t2.id, stage_name="CCM").one()
        ccm.approved_by_id = u.id
        db.session.commit()
        r = bi_service.daily_report(TODAY)
        self.assertTrue(r["today"]["has_approver"])
        row = [x for x in r["shift_detail_rows"]
               if x["shift"] == 1 and x["dn"] == 100 and x["cls"] == "K9"][0]
        self.assertEqual(row["total"], 3)  # T1, T2, T4 (T5 pending excluded)
        self.assertEqual(row["rejects"], 1)
        self.assertEqual(row["approver"], "QC One")

    def test_empty_db_returns_none(self):
        self.assertIsNone(bi_service.daily_report(TODAY))

    # --- lab_decision fallback + produced-vs-decided -----------------------
    #
    # Production reality: final_decision_value is filled on ~18% of pipes
    # while lab_decision is filled on ~98%, so a decided-only report on
    # final_decision_value alone hid most output and inflated reject %.

    def test_lab_decision_used_when_final_missing(self):
        self._seed()
        _pipe("L1", TODAY, "", machine=self.m10, lab="ACCEPT")
        _pipe("L2", TODAY, None, machine=self.m10, lab="REJECT")
        t = bi_service.daily_report(TODAY)["today"]
        self.assertEqual(t["n"], 6)        # 4 final-decided + 2 lab-decided
        self.assertEqual(t["rejects"], 3)  # 2 final REJECT + 1 lab REJECT

    def test_final_decision_wins_over_lab(self):
        self._seed()
        _pipe("L3", TODAY, "ACCEPT", machine=self.m10, lab="REJECT")
        t = bi_service.daily_report(TODAY)["today"]
        self.assertEqual(t["n"], 5)
        self.assertEqual(t["rejects"], 2)  # unchanged — final ACCEPT wins

    def test_lab_pending_states_stay_undecided(self):
        """WAITING / HOLD / BLOCKED are not outcomes — they must not land in
        the reject-% denominator, but they are still produced pipes."""
        self._seed()
        for i, state in enumerate(("WAITING", "HOLD", "BLOCKED")):
            _pipe(f"P{i}", TODAY, "", machine=self.m10, lab=state)
        t = bi_service.daily_report(TODAY)["today"]
        self.assertEqual(t["n"], 4)         # denominator unchanged
        self.assertEqual(t["produced"], 8)  # 5 seeded + 3 pending

    def test_produced_counts_every_pipe_on_the_date(self):
        self._seed()
        t = bi_service.daily_report(TODAY)["today"]
        self.assertEqual(t["produced"], 5)  # T1..T5, including undecided T5
        self.assertEqual(t["n"], 4)

    def test_produced_reported_when_nothing_is_decided(self):
        """Regression: a day with output but no decisions rendered as
        '0 production, 0% reject, status good' — production was invisible."""
        _pipe("U1", TODAY, "", machine=self.m10, lab="WAITING")
        _pipe("W1", YESTERDAY, "ACCEPT", machine=self.m10)
        t = bi_service.daily_report(TODAY)["today"]
        self.assertEqual(t["produced"], 1)
        self.assertEqual(t["n"], 0)
        self.assertEqual(t["rejects"], 0)
        self.assertAlmostEqual(t["rej_pct"], 0.0)

    def test_mixed_null_and_numbered_shifts_no_crash(self):
        """NULL shift ('؟') + numbered shifts must not TypeError on sort.

        Regression: prod had all-NULL shifts; one real pipe with shift=1
        crashed daily_report() with int < str comparison (bi_service).
        """
        self._seed()
        _pipe("M1", TODAY, "ACCEPT", machine=self.m10, shift=2)
        _pipe("M2", TODAY, "ACCEPT", machine=self.m10, shift=None)  # NULL
        r = bi_service.daily_report(TODAY)  # must not raise
        keys = [row["key"] for row in r["shift_rows"]]
        # numeric shifts first ascending, '؟' last
        self.assertEqual(keys[-1], "؟")
        self.assertEqual(
            [k for k in keys if k != "؟"], sorted(k for k in keys if k != "؟"))
        detail_shifts = [row["shift"] for row in r["shift_detail_rows"]]
        self.assertEqual(detail_shifts[-1], "؟")

    def test_summary_contains_core_numbers(self):
        self._seed()
        r = bi_service.daily_report(TODAY)
        # 5 cast today, 4 of them decided — the summary states both so the
        # reject % is never read against the wrong denominator.
        self.assertIn("5 ماسورة", r["summary"])
        self.assertIn("تم البت في 4", r["summary"])
        self.assertIn("50.00%", r["summary"])


class DailyReportRoutesTestCase(_Base):
    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        self.users = {}
        for role in User.ROLES:
            u = User(username=role, full_name=role, role=role, is_active=True)
            u.set_password("x")
            db.session.add(u)
            self.users[role] = u
        db.session.commit()

    def _login(self, role):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.users[role].id)
            sess["_fresh"] = True
        g.pop("_login_user", None)

    def _seed_one(self):
        _pipe("T1", TODAY, "ACCEPT", machine=self.m10)

    def test_bi_dashboard_renders_daily_section(self):
        self._seed_one()
        self._login("viewer")
        resp = self.client.get("/reports/bi-dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("التقرير اليومي للإنتاج والجودة".encode(), resp.data)
        self.assertIn(TODAY.isoformat().encode(), resp.data)

    def test_bi_dashboard_rpt_date_param(self):
        self._seed_one()
        self._login("viewer")
        resp = self.client.get(
            f"/reports/bi-dashboard?rpt_date={TODAY.isoformat()}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("التقرير اليومي".encode(), resp.data)

    def test_bi_dashboard_empty_db_safe(self):
        self._login("viewer")
        resp = self.client.get("/reports/bi-dashboard")
        self.assertEqual(resp.status_code, 200)

    def test_pdf_export_returns_pdf(self):
        self._seed_one()
        self._login("admin")
        resp = self.client.get(
            f"/reports/export/daily-report-pdf?date={TODAY.isoformat()}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF"))

    def test_pdf_export_requires_login(self):
        resp = self.client.get("/reports/export/daily-report-pdf")
        self.assertEqual(resp.status_code, 302)

    def test_pdf_export_denied_without_export_permission(self):
        self._seed_one()
        self._login("viewer")  # viewer lacks reports.export
        resp = self.client.get("/reports/export/daily-report-pdf")
        self.assertIn(resp.status_code, (302, 403))

    def test_pdf_export_bad_date_falls_back(self):
        self._seed_one()
        self._login("admin")
        resp = self.client.get("/reports/export/daily-report-pdf?date=junk")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
