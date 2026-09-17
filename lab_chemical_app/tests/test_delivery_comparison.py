"""Tests for the Delivery comparison summary.

The deliveries page (/stages/deliveries) and a new report both show the SAME
comparison, grouped three ways over delivered pipes:
  - by customer   (who received how many pipes, across how many orders/bundles)
  - by engineer   (who produced the pipes that got delivered) — NULL -> "غير محدد"
  - by sales order (per order: pipes, customer, bundles)

summarize_deliveries() takes a list of Delivery PipeStage records (already
filtered by the caller) and returns those three tables, so the panel and the
report share one implementation.
"""
from datetime import date
import unittest

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.user import User
from app.services import analytics_service
from app.models.permission import seed_default_permissions


class DeliveryComparisonTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()
        admin = User(username="boss", full_name="Boss", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        self.user_id = admin.id
        self._seed()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, engineer, dn=100):
        p = Pipe(
            production_date=date(2026, 4, 14), shift=1, shift_engineer=engineer,
            ladle_id="L1", pipe_code=no_code, no_code=no_code, arrange_pipe=1,
            diameter=dn, pipe_class="K9",
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _deliver(self, pipe, customer, sales_order, bundle, when=date(2026, 4, 20)):
        s = PipeStage(
            pipe_id=pipe.id, stage_name="Delivery", delivery_date=when,
            delivery_customer=customer, sales_order=sales_order,
            bundle_number=bundle, delivery_receipt="R-1",
        )
        db.session.add(s)
        db.session.commit()
        return s

    def _seed(self):
        # Water Co: 3 pipes across SO-100 (B1, B1) + SO-101 (B2); engineers Ali x2, None x1
        a1 = self._pipe("A-1", "Ali")
        a2 = self._pipe("A-2", "Ali")
        u1 = self._pipe("U-1", None)
        self._deliver(a1, "Water Co", "SO-100", "B1")
        self._deliver(a2, "Water Co", "SO-100", "B1")
        self._deliver(u1, "Water Co", "SO-101", "B2")
        # Gas Co: 1 pipe, engineer Sami, SO-200 B9
        s1 = self._pipe("S-1", "Sami")
        self._deliver(s1, "Gas Co", "SO-200", "B9")

    def _all_deliveries(self):
        return PipeStage.query.filter_by(stage_name="Delivery").all()

    def test_by_customer(self):
        data = analytics_service.summarize_deliveries(self._all_deliveries())
        cust = {r["customer"]: r for r in data["by_customer"]}
        self.assertEqual(cust["Water Co"]["pipes"], 3)
        self.assertEqual(cust["Water Co"]["orders"], 2)   # SO-100, SO-101
        self.assertEqual(cust["Water Co"]["bundles"], 2)  # B1, B2
        self.assertEqual(cust["Gas Co"]["pipes"], 1)

    def test_by_engineer(self):
        data = analytics_service.summarize_deliveries(self._all_deliveries())
        eng = {r["engineer"]: r for r in data["by_engineer"]}
        self.assertEqual(eng["Ali"]["pipes"], 2)
        self.assertEqual(eng["Sami"]["pipes"], 1)
        self.assertIn("غير محدد", eng)            # NULL engineer bucketed
        self.assertEqual(eng["غير محدد"]["pipes"], 1)

    def test_by_order(self):
        data = analytics_service.summarize_deliveries(self._all_deliveries())
        order = {r["sales_order"]: r for r in data["by_order"]}
        self.assertEqual(order["SO-100"]["pipes"], 2)
        self.assertEqual(order["SO-100"]["customer"], "Water Co")
        self.assertEqual(order["SO-200"]["pipes"], 1)

    def test_totals(self):
        data = analytics_service.summarize_deliveries(self._all_deliveries())
        self.assertEqual(data["totals"]["pipes"], 4)
        self.assertEqual(data["totals"]["customers"], 2)
        self.assertEqual(data["totals"]["orders"], 3)

    def test_empty_is_safe(self):
        data = analytics_service.summarize_deliveries([])
        self.assertEqual(data["by_customer"], [])
        self.assertEqual(data["totals"]["pipes"], 0)

    # ---- routes render -----------------------------------------------------

    def test_report_route_renders(self):
        self._login()
        r = self.client.get("/reports/delivery-comparison")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Water Co".encode(), r.data)
        self.assertIn("SO-100".encode(), r.data)

    def test_report_excel_export(self):
        self._login()
        r = self.client.get("/reports/export/delivery-comparison.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheet", r.headers.get("Content-Type", ""))

    def test_deliveries_panel_renders(self):
        self._login()
        r = self.client.get("/stages/deliveries")
        self.assertEqual(r.status_code, 200)
        # the comparison panel partial is embedded above the list
        self.assertIn("Water Co".encode(), r.data)
        self.assertIn("Ali".encode(), r.data)


if __name__ == "__main__":
    unittest.main()
