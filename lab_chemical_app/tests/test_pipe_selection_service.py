"""Shared, policy-free pipe selection primitives."""

import unittest
from datetime import date

from app import create_app, db
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder


class PipeSelectionServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        self.first_order = ProductionOrder(
            order_number="PO-002", target_quantity=3, order_date=date(2026, 9, 1)
        )
        self.second_order = ProductionOrder(
            order_number="PO-001", target_quantity=3, order_date=date(2026, 9, 1)
        )
        db.session.add_all([self.first_order, self.second_order])
        db.session.flush()

        self.pipes = [
            Pipe(
                production_order_id=self.first_order.id,
                pipe_code="PIPE-Z-2",
                no_code="BATCH-Z",
                warehouse_barcode="9002",
                arrange_pipe=2,
                production_date=date(2026, 9, 2),
            ),
            Pipe(
                production_order_id=self.first_order.id,
                pipe_code="PIPE-Z-1",
                no_code="BATCH-Z",
                warehouse_barcode="9001",
                arrange_pipe=1,
                production_date=date(2026, 9, 1),
            ),
            Pipe(
                production_order_id=self.second_order.id,
                pipe_code="PIPE-A-1",
                no_code="BATCH-A",
                warehouse_barcode="8001",
                arrange_pipe=1,
                production_date=date(2026, 8, 31),
            ),
        ]
        db.session.add_all(self.pipes)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_id_parsers_preserve_first_seen_order_and_reject_invalid_values(self):
        from app.services.pipe_selection_service import dedupe_pipe_ids, parse_order_ids

        self.assertEqual(parse_order_ids([str(self.first_order.id), str(self.second_order.id), str(self.first_order.id)]),
                         [self.first_order.id, self.second_order.id])
        self.assertEqual(dedupe_pipe_ids(["3", 2, "3"]), [3, 2])
        self.assertEqual(dedupe_pipe_ids("12"), [12])
        for invalid in ([""], ["abc"], ["0"], ["-1"], [None]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                dedupe_pipe_ids(invalid)

    def test_pipes_for_orders_preserves_order_selection_then_pipe_sequence(self):
        from app.services.pipe_selection_service import pipes_for_orders

        found = pipes_for_orders([self.first_order.id, self.second_order.id, self.first_order.id])
        self.assertEqual([p.pipe_code for p in found], ["PIPE-Z-1", "PIPE-Z-2", "PIPE-A-1"])

    def test_lookup_uses_exact_barcode_then_code_then_no_code_before_partial(self):
        from app.services.pipe_selection_service import lookup_pipes

        self.assertEqual([p.pipe_code for p in lookup_pipes("9001")], ["PIPE-Z-1"])
        self.assertEqual([p.pipe_code for p in lookup_pipes("pipe-z-1")], ["PIPE-Z-1"])
        self.assertEqual([p.pipe_code for p in lookup_pipes("batch-z")],
                         ["PIPE-Z-1", "PIPE-Z-2"])
        self.assertEqual([p.pipe_code for p in lookup_pipes("pipe")],
                         ["PIPE-Z-2", "PIPE-Z-1", "PIPE-A-1"])
        self.assertEqual(lookup_pipes("  "), [])


if __name__ == "__main__":
    unittest.main()
