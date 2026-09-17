"""The certificate's Pipe Length column is a SUM, not one pipe's own length.

A ticked warranty covering two pipes printed "Pipe Length 6" — the length of
just one of them — because ``group_pipes`` grouped by (dn, class, length) and
copied a single pipe's length onto the row. It now groups by (dn, class) only
and the printed length is the total of every pipe's actual length in that
group, falling back per pipe to that pipe's own order when Finish never
measured it (a certificate can now cover pipes from more than one order).
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.models.permission import seed_default_permissions
from app.services.certificate_service import group_pipes, total_metres


class CertificateLengthsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

        self.order = ProductionOrder(
            order_number="PO-20260905-001",
            customer_name="Cairo Water",
            target_quantity=10,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 1),
        )
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, order=None, length=None, diameter=800,
              pipe_class="K9", decision="ACCEPT", ladle="L1"):
        order = order or self.order
        p = Pipe(
            production_date=date(2026, 9, 1), ladle_id=ladle,
            pipe_code=no_code, no_code=no_code, arrange_pipe=1,
            diameter=diameter, pipe_class=pipe_class,
            production_order_id=order.id,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.commit()
        if length is not None:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name="Finish",
                measurement_value=length, decision="Accept",
            ))
            db.session.commit()
        return p

    def test_two_measured_pipes_sum_into_one_row(self):
        a = self._pipe("N0001", length=5.5)
        b = self._pipe("N0002", length=6.0)

        rows = group_pipes([a, b], self.order)

        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["quantity"])
        self.assertEqual(11.5, rows[0]["length"])

    def test_unmeasured_pipe_falls_back_per_pipe_to_its_own_order(self):
        other = ProductionOrder(
            order_number="PO-20260905-002",
            customer_name="Cairo Water",
            target_quantity=5,
            diameter=800,
            pipe_class="K9",
            product_length=5.0,
            order_date=date(2026, 9, 1),
        )
        db.session.add(other)
        db.session.commit()

        measured = self._pipe("N0010", length=6.0)
        unmeasured_a = self._pipe("N0011")  # falls back to self.order (6.0)
        unmeasured_b = self._pipe("N0012", order=other)  # falls back to other (5.0)

        rows = group_pipes([measured, unmeasured_a, unmeasured_b], self.order)

        self.assertEqual(1, len(rows))
        self.assertEqual(3, rows[0]["quantity"])
        # 6.0 (measured) + 6.0 (self.order fallback) + 5.0 (other order fallback)
        self.assertEqual(17.0, rows[0]["length"])

    def test_pipes_from_two_orders_each_use_their_own_fallback(self):
        other = ProductionOrder(
            order_number="PO-20260905-003",
            customer_name="Cairo Water",
            target_quantity=5,
            diameter=800,
            pipe_class="K9",
            product_length=4.5,
            order_date=date(2026, 9, 1),
        )
        db.session.add(other)
        db.session.commit()

        a = self._pipe("N0020")  # self.order, unmeasured -> 6.0
        b = self._pipe("N0021", order=other)  # other, unmeasured -> 4.5

        # No single `order` argument covers both, so pass None: the fallback
        # must come from each pipe's own order, not a passed-in one.
        rows = group_pipes([a, b], order=None)

        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["quantity"])
        self.assertEqual(10.5, rows[0]["length"])

    def test_two_different_dns_still_produce_two_rows(self):
        a = self._pipe("N0030", length=6.0, diameter=800)
        b = self._pipe("N0031", length=6.0, diameter=300)

        rows = group_pipes([a, b], self.order)

        self.assertEqual(2, len(rows))
        dns = {r["dn"] for r in rows}
        self.assertEqual({800, 300}, dns)

    def test_no_length_recorded_anywhere_leaves_length_none(self):
        order = ProductionOrder(
            order_number="PO-20260905-004",
            customer_name="Cairo Water",
            target_quantity=5,
            diameter=800,
            pipe_class="K9",
            product_length=None,
            order_date=date(2026, 9, 1),
        )
        db.session.add(order)
        db.session.commit()

        p = self._pipe("N0040", order=order)

        rows = group_pipes([p], order)

        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0]["length"])

    def test_total_metres_sums_rows_without_multiplying_by_quantity(self):
        rows = [
            {"dn": 800, "pipe_class": "K9", "length": 11.5, "quantity": 2},
            {"dn": 300, "pipe_class": "K9", "length": 4.5, "quantity": 3},
        ]
        self.assertEqual(16.0, total_metres(rows))

    def test_total_metres_skips_rows_with_no_length(self):
        rows = [
            {"dn": 800, "pipe_class": "K9", "length": 11.5, "quantity": 2},
            {"dn": 300, "pipe_class": "K9", "length": None, "quantity": 3},
        ]
        self.assertEqual(11.5, total_metres(rows))


if __name__ == "__main__":
    unittest.main()
