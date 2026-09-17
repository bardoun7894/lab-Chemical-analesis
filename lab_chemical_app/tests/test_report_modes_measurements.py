"""Tests for the report fixes (DrAlaa 2026-08-13):

1. pipe_length / pipe_metric_value: length mode reads the real Finish-stage
   measurement (popup data) with Product.length_m fallback — never a fake 6.0.
2. stage_measurements: surfaces popup-entered measurements.
3. approval_report: attributes final decisions to the approver, filterable.
"""

from datetime import date
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


def _args(d=None):
    return MultiDict(d or {})


class PipeLengthTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        ProductionStage.seed_defaults()

        self.product = Product(product_code="P100", weight_kg=120.0, length_m=5.5)
        db.session.add(self.product)
        db.session.flush()
        self.order = ProductionOrder(
            order_number="PO1",
            target_quantity=10,
            product_id=self.product.id,
            order_date=date(2026, 3, 1),
        )
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, length_m=None):
        p = Pipe(
            production_date=date(2026, 3, 1),
            shift=1,
            ladle_id="L1",
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=100,
            actual_weight=100.0,
            iso_weight=120.0,
            production_order_id=self.order.id,
            product_id=self.product.id,
        )
        db.session.add(p)
        db.session.commit()
        if length_m is not None:
            finish_name = ProductionStage.name_for_code("finish")
            db.session.add(
                PipeStage(
                    pipe_id=p.id,
                    stage_name=finish_name,
                    measurement_value=length_m,
                    measurement_type="Length",
                )
            )
            db.session.commit()
        return p

    def test_length_reads_finish_measurement(self):
        p = self._pipe("N1", length_m=5.2)
        self.assertEqual(analytics_service.pipe_length(p), 5.2)
        self.assertEqual(analytics_service.pipe_metric_value(p, "length"), 5.2)

    def test_length_falls_back_to_product_length(self):
        p = self._pipe("N1")
        self.assertEqual(analytics_service.pipe_length(p), 5.5)

    def test_length_is_none_when_no_data(self):
        p = self._pipe("N1")
        p.product_id = None
        p.production_order_id = None
        db.session.commit()
        self.assertIsNone(analytics_service.pipe_length(p))
        self.assertEqual(analytics_service.pipe_metric_value(p, "length"), 0.0)

    def test_weight_mode_unchanged(self):
        p = self._pipe("N1")
        self.assertEqual(analytics_service.pipe_metric_value(p, "weight"), 100.0)


class StageMeasurementsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        ProductionStage.seed_defaults()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code):
        p = Pipe(
            production_date=date(2026, 3, 1),
            shift=1,
            ladle_id="L1",
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=100,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_only_measured_rows_are_returned(self):
        p = self._pipe("N1")
        finish = ProductionStage.name_for_code("finish")
        ccm = ProductionStage.name_for_code("ccm")
        db.session.add(
            PipeStage(
                pipe_id=p.id,
                stage_name=finish,
                measurement_value=5.9,
                measurement_type="Length",
            )
        )
        db.session.add(
            PipeStage(
                pipe_id=p.id,
                stage_name=ccm,
                temperature=1410.0,
            )
        )
        # A stage with no measurement at all must be excluded.
        db.session.add(
            PipeStage(
                pipe_id=p.id,
                stage_name="Annealing",
                decision="Accept",
            )
        )
        db.session.commit()

        data = analytics_service.stage_measurements(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["summary"]["measured_rows"], 2)
        rows = {(r["stage"], r["measurement_type"]) for r in data["rows"]}
        self.assertIn((finish, "Length"), rows)
        self.assertIn((ccm, ""), rows)

    def test_empty_db_safe(self):
        data = analytics_service.stage_measurements(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["summary"]["measured_rows"], 0)


class ApprovalReportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        ProductionStage.seed_defaults()

        self.user_a = User(username="eng_a", full_name="Engineer A", role="supervisor")
        self.user_a.set_password("pw")
        self.user_b = User(username="eng_b", full_name="Engineer B", role="supervisor")
        self.user_b.set_password("pw")
        db.session.add_all([self.user_a, self.user_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, decision, approver=None):
        p = Pipe(
            production_date=date(2026, 3, 1),
            shift=1,
            ladle_id="L1",
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=100,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.commit()
        if approver is not None:
            lab_name = ProductionStage.name_for_code("lab")
            db.session.add(
                PipeStage(
                    pipe_id=p.id,
                    stage_name=lab_name,
                    decision="Accept",
                    approved_by_id=approver.id,
                )
            )
            db.session.commit()
        return p

    def test_approver_attributed_from_lab_stage(self):
        self._pipe("N1", "ACCEPT", approver=self.user_a)
        self._pipe("N2", "REJECT", approver=self.user_b)
        data = analytics_service.approval_report(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["summary"]["total"], 2)
        self.assertEqual(data["summary"]["accepted"], 1)
        self.assertEqual(data["summary"]["rejected"], 1)
        by_code = {r["pipe_code"]: r["approver"] for r in data["rows"]}
        self.assertEqual(by_code["N1"], "Engineer A")
        self.assertEqual(by_code["N2"], "Engineer B")

    def test_approver_filter_narrows(self):
        self._pipe("N1", "ACCEPT", approver=self.user_a)
        self._pipe("N2", "ACCEPT", approver=self.user_b)
        data = analytics_service.approval_report(
            analytics_service.parse_filters(_args({"approved_by": str(self.user_a.id)}))
        )
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["rows"][0]["pipe_code"], "N1")

    def test_approvers_list_populated(self):
        self._pipe("N1", "ACCEPT")
        data = analytics_service.approval_report(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(len(data["approvers"]), 2)


class ReportRoutesTestCase(unittest.TestCase):
    """The two new reports must render empty-safe and require login."""

    ROUTES = ["/reports/stage-measurements", "/reports/approvals"]

    def setUp(self):
        from app.models.permission import seed_default_permissions
        from app.models.user import User

        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()
        u = User(username="viewer", full_name="viewer", role="viewer", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        from flask import g

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        g.pop("_login_user", None)

    def test_routes_render_empty_safe_for_viewer(self):
        self._login()
        for r in self.ROUTES:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 200, r)

    def test_routes_require_login(self):
        for r in self.ROUTES:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 302, r)


if __name__ == "__main__":
    unittest.main()
