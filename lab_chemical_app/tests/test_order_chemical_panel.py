"""Order progress 'Chemical' panel must show analyses linked by pipe ladle,
not only those with production_order_id set (which add() never set -> always empty)."""

from datetime import date
import unittest

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.models.permission import seed_default_permissions


class OrderChemicalPanelTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()
        u = User(username="sup", full_name="Sup", role="admin")
        if hasattr(u, "set_password"):
            u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_panel_shows_analysis_linked_only_by_ladle(self):
        """Reproduces the bug: analysis exists for a pipe's ladle but has no
        production_order_id. Panel must still list it after the fix."""
        order = ProductionOrder(order_number="PO-TEST-001",
                                target_quantity=1, order_date=date(2026, 4, 13))
        db.session.add(order)
        db.session.commit()

        # Analysis linked ONLY by ladle (production_order_id intentionally unset)
        ca = ChemicalAnalysis(ladle_id="113042026", test_date=date(2026, 4, 13),
                              ladle_no=1, day=13, month=4, year=2026,
                              decision="فحص أخيرة فقط")
        db.session.add(ca)
        db.session.commit()

        pipe = Pipe(production_order_id=order.id, ladle_id="113042026",
                    pipe_code="y0001", no_code="y0001", production_date=date(2026, 4, 13))
        db.session.add(pipe)
        db.session.commit()

        self.assertIsNone(ca.production_order_id, "precondition: not directly linked")

        self._login()
        resp = self.client.get(f"/orders/{order.id}/progress")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("113042026", body, "ladle analysis should appear in Chemical panel")
        self.assertNotIn("No chemical data linked to this order", body)


if __name__ == "__main__":
    unittest.main()
