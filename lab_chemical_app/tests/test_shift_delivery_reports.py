"""Tests for the Shift Engineer report and the Delivery Overview report.

These reports surface inside the Reports section and must show data across
ANY date (no narrow default window) — the previous /stages versions only
showed the current clock-shift / a single day and looked permanently empty.
"""
from datetime import date
import unittest

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.user import User
from app.services import analytics_service
from app.models.permission import seed_default_permissions


class ShiftDeliveryReportsTestCase(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, no_code, ladle, shift, engineer, final, dn=100, weight=50.0):
        p = Pipe(
            production_date=date(2026, 4, 14),
            shift=shift,
            shift_engineer=engineer,
            ladle_id=ladle,
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=dn,
            pipe_class="K9",
            actual_weight=weight,
            lab_decision="ACCEPT" if final == "ACCEPT" else "WAITING",
            final_decision_value=final,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _deliver(self, pipe, customer, sales_order, bundle, when=date(2026, 4, 20)):
        s = PipeStage(
            pipe_id=pipe.id,
            stage_name="Delivery",
            delivery_date=when,
            delivery_customer=customer,
            sales_order=sales_order,
            bundle_number=bundle,
            delivery_receipt="R-1",
        )
        db.session.add(s)
        db.session.commit()
        return s

    def _seed(self):
        # Heat H1: 3 pipes (2 accept, 1 reject); 2 delivered
        p1 = self._pipe("H1-1", "H1", 1, "Ali", "ACCEPT")
        p2 = self._pipe("H1-2", "H1", 1, "Ali", "ACCEPT")
        self._pipe("H1-3", "H1", 1, "Ali", "REJECT")
        self._deliver(p1, "Water Co", "SO-100", "B1")
        self._deliver(p2, "Water Co", "SO-100", "B1")
        # Heat H2: 1 pipe (pending) shift 2, different engineer; not delivered
        self._pipe("H2-1", "H2", 2, "Sami", "")

    # ---- Shift Engineer report --------------------------------------------

    def test_shift_engineer_all_shifts(self):
        data = analytics_service.shift_engineer_report(
            {"date_from": None, "date_to": None, "shift": None, "shift_engineer": None}
        )
        self.assertEqual(data["stats"]["total"], 4)
        self.assertEqual(data["stats"]["accepted"], 2)
        self.assertEqual(data["stats"]["rejected"], 1)
        self.assertEqual(data["stats"]["pending"], 1)
        self.assertIn("Ali", data["engineers"])
        self.assertIn("Sami", data["engineers"])

    def test_shift_engineer_filtered_by_shift(self):
        data = analytics_service.shift_engineer_report(
            {"date_from": None, "date_to": None, "shift": 2, "shift_engineer": None}
        )
        self.assertEqual(data["stats"]["total"], 1)
        self.assertEqual(data["stats"]["pending"], 1)

    def test_shift_engineer_filtered_by_engineer(self):
        data = analytics_service.shift_engineer_report(
            {"date_from": None, "date_to": None, "shift": None, "shift_engineer": "Ali"}
        )
        self.assertEqual(data["stats"]["total"], 3)

    # ---- Delivery Overview report -----------------------------------------

    def test_delivery_overview_per_heat(self):
        data = analytics_service.delivery_overview(
            {"date_from": None, "date_to": None, "group_by": "heat", "customer": None}
        )
        groups = {g["key"]: g for g in data["groups"]}
        self.assertEqual(groups["H1"]["produced"], 3)
        self.assertEqual(groups["H1"]["accepted"], 2)
        self.assertEqual(groups["H1"]["rejected"], 1)
        self.assertEqual(groups["H1"]["delivered"], 2)
        self.assertEqual(groups["H2"]["produced"], 1)
        self.assertEqual(groups["H2"]["delivered"], 0)

    def test_delivery_overview_per_batch(self):
        data = analytics_service.delivery_overview(
            {"date_from": None, "date_to": None, "group_by": "batch", "customer": None}
        )
        groups = {g["key"]: g for g in data["groups"]}
        self.assertIn("B1", groups)
        self.assertEqual(groups["B1"]["pipes"], 2)

    def test_delivery_overview_per_order(self):
        data = analytics_service.delivery_overview(
            {"date_from": None, "date_to": None, "group_by": "order", "customer": None}
        )
        groups = {g["key"]: g for g in data["groups"]}
        self.assertIn("SO-100", groups)
        self.assertEqual(groups["SO-100"]["pipes"], 2)

    # ---- Routes render with data (not just empty 200) ---------------------

    def test_shift_route_renders_rows(self):
        self._login()
        r = self.client.get("/reports/shift-engineer")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"H1-1", r.data)

    def test_delivery_route_renders_groups(self):
        self._login()
        r = self.client.get("/reports/delivery-overview?group_by=heat")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"H1", r.data)


if __name__ == "__main__":
    unittest.main()
