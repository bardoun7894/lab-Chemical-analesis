"""Order detail page: pipes table shows the Class column and the page
offers a Print button (window.print + print stylesheet)."""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder
from app.models.user import User


class OrderDetailPrintAndClassTestCase(unittest.TestCase):
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

        self.order = ProductionOrder(
            order_number="PO-1", customer_name="Alex Water",
            order_date=date(2026, 7, 1), target_quantity=7, status="pending",
        )
        db.session.add(self.order)
        db.session.commit()

        p = Pipe(
            production_date=date(2026, 7, 20), ladle_id="G1",
            pipe_code="T1", no_code="T1", arrange_pipe=1,
            diameter=800, pipe_class="K9",
            production_order_id=self.order.id,
        )
        db.session.add(p)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_pipes_table_has_class_column(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        html = self.client.get(f"/orders/{self.order.id}").get_data(as_text=True)
        self.assertIn(">Class<", html)
        self.assertIn(">K9<", html)

    def test_print_button_and_print_css_present(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        html = self.client.get(f"/orders/{self.order.id}").get_data(as_text=True)
        self.assertIn("window.print()", html)
        self.assertIn("@media print", html)


if __name__ == "__main__":
    unittest.main()
