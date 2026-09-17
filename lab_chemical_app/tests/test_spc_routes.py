"""Route tests for the SPC / capability reports: permission gating and
empty-safe rendering. Follows the test_permissions auth pattern.
"""
import unittest

from flask import g

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.user import User


class SPCRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.users = {}
        for role in User.ROLES:
            u = User(username=role, full_name=role, role=role, is_active=True)
            u.set_password("x")
            db.session.add(u)
            self.users[role] = u
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, role):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.users[role].id)
            sess["_fresh"] = True
        g.pop("_login_user", None)

    def test_admin_gets_200(self):
        self._login("admin")
        resp = self.client.get("/reports/spc")
        self.assertEqual(resp.status_code, 200)

    def test_ungranted_role_is_denied(self):
        # The matrix decorator fails closed with a redirect + flash (app-wide
        # convention), never by rendering the report.
        self._login("viewer")
        resp = self.client.get("/reports/spc")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(b"SPC Control Charts", resp.data)

    def test_operator_ungranted_by_default(self):
        # reports.spc defaults to admin/super_admin only (fail-closed).
        self._login("operator")
        resp = self.client.get("/reports/spc")
        self.assertEqual(resp.status_code, 302)

    def test_empty_db_renders_insufficient_data(self):
        self._login("admin")
        resp = self.client.get("/reports/spc?characteristic=tensile_mpa&chart=imr")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Insufficient data", resp.data)

    def test_all_chart_modes_render_empty_safe(self):
        self._login("admin")
        for chart in ("imr", "xbar", "p", "c"):
            resp = self.client.get(f"/reports/spc?chart={chart}")
            self.assertEqual(resp.status_code, 200, chart)

    def test_unknown_characteristic_and_chart_fall_back(self):
        self._login("admin")
        resp = self.client.get("/reports/spc?characteristic=bogus&chart=bogus")
        self.assertEqual(resp.status_code, 200)

    def test_export_requires_export_permission(self):
        self._login("viewer")
        resp = self.client.get("/reports/spc.xlsx")
        self.assertEqual(resp.status_code, 302)
        self._login("admin")
        resp = self.client.get("/reports/spc.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.content_type)

    def test_export_also_requires_spc_grant(self):
        # Review finding: supervisor holds reports.export but NOT reports.spc —
        # the export must not bypass the screen's own gate.
        self._login("supervisor")
        resp = self.client.get("/reports/spc.xlsx")
        self.assertEqual(resp.status_code, 302)

    def test_capability_route_gated(self):
        self._login("viewer")
        self.assertEqual(self.client.get("/reports/capability").status_code, 302)
        self._login("admin")
        self.assertEqual(self.client.get("/reports/capability").status_code, 200)

    def test_capability_no_limits_shows_unavailable(self):
        self._login("admin")
        # microstructure_40 is the one lab characteristic with no acceptance
        # criterion; nodularity now resolves one from mechanical_rules.
        resp = self.client.get(
            "/reports/capability?characteristic=microstructure_40")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No spec limits configured", resp.data)
        self.assertIn(b"never fabricated", resp.data)


    def test_lab_trends_renders_with_partial_data(self):
        # Only tensile data exists — the other five panels must render their
        # empty state without breaking the page or each other.
        from datetime import date as _date
        from app.models.mechanical import MechanicalTest
        db.session.add(MechanicalTest(
            test_date=_date(2026, 3, 1), tensile_mpa=420.0, status="ACTIVE",
            ladle_id="L1", code="T1",
        ))
        db.session.commit()

        self._login("admin")
        resp = self.client.get("/reports/lab-trends")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"trend_tensile_mpa", resp.data)   # populated panel
        self.assertIn(b"other panels are unaffected", resp.data)  # empty states
        self.assertIn(b"Microstructure %", resp.data)    # honest label

    def test_lab_trends_gated_for_viewer_ok(self):
        # lab-trends uses the shared reports.view grant (all roles have it).
        self._login("viewer")
        self.assertEqual(self.client.get("/reports/lab-trends").status_code, 200)

    def test_stage_cycle_time_renders_empty_safe(self):
        self._login("viewer")  # reports.view grant suffices
        resp = self.client.get("/reports/stage-cycle-time")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Coverage:", resp.data)

    def test_stage_audit_lists_history_newest_first(self):
        from app.models.pipe import Pipe, PipeStage
        from app.models.stage_history import PipeStageHistory
        from datetime import date as _date
        p = Pipe(production_date=_date(2026, 3, 1), shift=1, ladle_id="L1",
                 pipe_code="N1", no_code="N1", arrange_pipe=1, diameter=100)
        db.session.add(p)
        db.session.flush()
        st = PipeStage(pipe_id=p.id, stage_name="Zinc", decision="HOLD",
                       reason="waiting lab")
        db.session.add(st)
        db.session.flush()
        db.session.add(PipeStageHistory.create_from_stage(
            st, action="update", user_id=self.users["admin"].id))
        db.session.commit()

        self._login("admin")
        resp = self.client.get("/reports/stage-audit")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"N1", resp.data)
        self.assertIn(b"waiting lab", resp.data)
        # Filter by stage excludes it
        resp = self.client.get("/reports/stage-audit?stage=CCM")
        self.assertNotIn(b"waiting lab", resp.data)

    def test_stage_audit_export_cols_subset(self):
        self._login("admin")
        resp = self.client.get("/reports/stage-audit.xlsx?cols=stage,action")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.content_type)


if __name__ == "__main__":
    unittest.main()
