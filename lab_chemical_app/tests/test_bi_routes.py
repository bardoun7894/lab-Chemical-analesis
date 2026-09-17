"""Route tests for the Phase-2 BI panels: gating + empty-safe rendering."""
import unittest

from flask import g

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.user import User


class BIRoutesTestCase(unittest.TestCase):
    ROUTES = [
        "/reports/ytd-comparison",
        "/reports/period-compare",
        "/reports/stage-funnel",
        "/reports/defect-heatmap",
        "/reports/saving-matrix",
        "/reports/data-quality",
        "/reports/diagnosis",
    ]

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

    def test_all_routes_render_empty_safe_for_viewer(self):
        # reports.view is granted to every role — all panels must render.
        self._login("viewer")
        for r in self.ROUTES:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 200, r)

    def test_routes_require_login(self):
        for r in self.ROUTES:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 302, r)  # -> login

    def test_diagnosis_clean_db_shows_no_problems(self):
        self._login("admin")
        resp = self.client.get("/reports/diagnosis")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No problems detected", resp.data)

    def test_period_compare_bad_dates_fall_back(self):
        self._login("admin")
        resp = self.client.get(
            "/reports/period-compare?a_from=not-a-date&b_to=also-bad"
        )
        self.assertEqual(resp.status_code, 200)

    def test_heatmap_metric_param_validated(self):
        self._login("admin")
        resp = self.client.get("/reports/defect-heatmap?metric=bogus")
        self.assertEqual(resp.status_code, 200)

    def test_bi_dashboard_renders_empty_safe(self):
        self._login("viewer")
        resp = self.client.get("/reports/bi-dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"CCM BI", resp.data)

    def test_bi_dashboard_shows_kpis_with_data(self):
        from datetime import date as _date
        from app.models.pipe import Pipe
        db.session.add(Pipe(
            production_date=_date(2026, 3, 1), shift=1, ladle_id="L1",
            pipe_code="N1", no_code="N1", arrange_pipe=1, diameter=100,
            pipe_class="K9", actual_weight=100.0, iso_weight=120.0,
            final_decision_value="ACCEPT",
        ))
        db.session.commit()
        self._login("admin")
        resp = self.client.get("/reports/bi-dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("إجمالي الإنتاج".encode(), resp.data)
        self.assertIn(b'"produced": 1', resp.data)


if __name__ == "__main__":
    unittest.main()
