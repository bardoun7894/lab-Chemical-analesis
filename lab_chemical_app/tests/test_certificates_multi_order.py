"""One certificate can cover pipes from several production orders.

R1 of the 2026-09-05 client feedback: the client ships one delivery that draws
on more than one production order, and wants one certificate for it rather than
one per order. `production_order_id` stays the primary order (everything else
downstream still reads it), but the actual coverage is derived from whichever
pipes got ticked — spanning however many orders those pipes come from.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.certificate import Certificate
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder
from app.models.user import User


class CertificateMultiOrderTestCase(unittest.TestCase):
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

        self.order1 = ProductionOrder(
            order_number="PO-20260905-001",
            customer_name="Cairo Water",
            target_quantity=10,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 1),
        )
        self.order2 = ProductionOrder(
            order_number="PO-20260905-002",
            customer_name="Alexandria Utilities",
            target_quantity=10,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 2),
        )
        db.session.add_all([self.order1, self.order2])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, order, no_code, decision="ACCEPT", ladle="L1"):
        p = Pipe(
            production_date=date(2026, 9, 1), ladle_id=ladle,
            pipe_code=no_code, no_code=no_code, arrange_pipe=1,
            diameter=800, pipe_class="K9",
            production_order_id=order.id,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.commit()
        return p

    # ---- multi-order coverage -----------------------------------------

    def test_pipes_from_two_orders_saved_on_one_certificate(self):
        a = self._pipe(self.order1, "A0001")
        b = self._pipe(self.order2, "B0001")
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=warranty"
            f"&order_ids={self.order1.id}&order_ids={self.order2.id}",
            data={
                "pipe_ids": [str(a.id), str(b.id)],
                "warranty_years": "3",
            },
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual(2, cert.quantity)

    def test_production_order_id_ends_up_as_one_of_the_ticked_orders(self):
        a = self._pipe(self.order1, "A0002")
        b = self._pipe(self.order2, "B0002")
        self._login()

        self.client.post(
            f"/certificates/new?type=warranty"
            f"&order_ids={self.order1.id}&order_ids={self.order2.id}",
            data={
                "pipe_ids": [str(a.id), str(b.id)],
                "warranty_years": "3",
            },
            follow_redirects=True)

        cert = Certificate.query.one()
        self.assertIn(cert.production_order_id, {self.order1.id, self.order2.id})
        self.assertIsNotNone(cert.production_order_id)

    def test_cert_orders_contains_both_ticked_orders(self):
        a = self._pipe(self.order1, "A0003")
        b = self._pipe(self.order2, "B0003")
        self._login()

        self.client.post(
            f"/certificates/new?type=warranty"
            f"&order_ids={self.order1.id}&order_ids={self.order2.id}",
            data={
                "pipe_ids": [str(a.id), str(b.id)],
                "warranty_years": "3",
            },
            follow_redirects=True)

        cert = Certificate.query.one()
        self.assertEqual({self.order1.id, self.order2.id},
                         {o.id for o in cert.orders})

    def test_legacy_single_order_id_url_still_works(self):
        a = self._pipe(self.order1, "A0004")
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=warranty&order_id={self.order1.id}",
            data={"pipe_ids": [str(a.id)], "warranty_years": "3"},
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual(1, cert.quantity)
        self.assertEqual(self.order1.id, cert.production_order_id)
        self.assertEqual([self.order1.id], [o.id for o in cert.orders])

    def test_certificate_with_no_link_rows_falls_back_to_production_order(self):
        # Certificates issued before this feature have no certificate_orders
        # rows at all — created straight on the model, the way old data sits.
        cert = Certificate(
            certificate_no="WC-2026-900", cert_type=Certificate.WARRANTY,
            production_order_id=self.order1.id,
            issue_date=date(2026, 9, 2), warranty_years=3)
        db.session.add(cert)
        db.session.commit()

        self.assertEqual([cert.production_order], cert.orders)

    def test_reject_pipes_are_visible_but_not_selectable_across_orders(self):
        self._pipe(self.order1, "A0005")
        self._pipe(self.order2, "B0005", decision="REJECT")
        self._login()

        resp = self.client.get(
            f"/certificates/new?order_ids={self.order1.id}"
            f"&order_ids={self.order2.id}")
        self.assertEqual(200, resp.status_code)
        body = resp.get_data(as_text=True)
        self.assertIn("A0005", body)
        self.assertIn("B0005", body)
        self.assertIn("REJECT", body)
        self.assertRegex(body, r'value="\d+"[^>]*disabled')

    def test_crossing_customers_flashes_a_warning_but_still_saves(self):
        a = self._pipe(self.order1, "A0006")
        b = self._pipe(self.order2, "B0006")
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=warranty"
            f"&order_ids={self.order1.id}&order_ids={self.order2.id}",
            data={
                "pipe_ids": [str(a.id), str(b.id)],
                "warranty_years": "3",
            },
            follow_redirects=False)
        self.assertEqual(302, resp.status_code)
        self.assertEqual(1, Certificate.query.count())

        # The redirect lands on the print page, which does not render flashes
        # (no toolbar for them) — the warning is still queued, so the next
        # normal page shows it.
        index_body = self.client.get("/certificates/").get_data(as_text=True)
        self.assertIn("more than one customer", index_body.lower())


if __name__ == "__main__":
    unittest.main()
