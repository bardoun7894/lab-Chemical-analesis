"""Tests for the weight-saving fallback: iso_weight=0 must fall back to
Product.weight_kg; pipes with neither are excluded and counted.
"""
from datetime import date
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.pipe import Pipe
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.services import analytics_service


def _args(d=None):
    return MultiDict(d or {})


class WeightSavingFallbackTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        self.product = Product(product_code="P100", weight_kg=120.0)
        db.session.add(self.product)
        db.session.flush()
        self.order = ProductionOrder(
            order_number="PO1", target_quantity=10,
            product_id=self.product.id, order_date=date(2026, 3, 1),
        )
        db.session.add(self.order)
        # The pipes below are poured from ladle L1, and pipes.ladle_id is a
        # foreign key to chemical_analyses.ladle_id. SQLite ignored it, so this
        # ladle was never needed; Postgres enforces it.
        db.session.add(
            ChemicalAnalysis(test_date=date(2026, 3, 1), ladle_no=1, ladle_id="L1")
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, iso_weight, actual_weight, order=None, product=None):
        p = Pipe(
            production_date=date(2026, 3, 1), shift=1, ladle_id="L1",
            pipe_code=no_code, no_code=no_code, arrange_pipe=1, diameter=100,
            iso_weight=iso_weight, actual_weight=actual_weight,
            production_order_id=order.id if order else None,
            product_id=product.id if product else None,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_iso_weight_used_when_positive(self):
        self._pipe("N1", iso_weight=200.0, actual_weight=180.0)
        data = analytics_service.weight_saving(
            analytics_service.parse_filters(_args())
        )
        row = data["rows"][0]
        self.assertEqual(row["iso_weight"], 200.0)
        self.assertEqual(row["saving"], 20.0)
        self.assertEqual(data["summary"]["excluded_no_weight"], 0)
        self.assertEqual(data["summary"]["via_product_standard"], 0)

    def test_falls_back_to_product_weight_via_order(self):
        self._pipe("N1", iso_weight=0, actual_weight=105.0, order=self.order)
        data = analytics_service.weight_saving(
            analytics_service.parse_filters(_args())
        )
        row = data["rows"][0]
        self.assertEqual(row["iso_weight"], 120.0)   # product standard
        self.assertEqual(row["saving"], 15.0)
        self.assertEqual(data["summary"]["via_product_standard"], 1)
        self.assertEqual(data["summary"]["excluded_no_weight"], 0)

    def test_falls_back_to_product_weight_via_product_id(self):
        self._pipe("N1", iso_weight=None, actual_weight=100.0,
                   product=self.product)
        data = analytics_service.weight_saving(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["rows"][0]["iso_weight"], 120.0)
        self.assertEqual(data["summary"]["via_product_standard"], 1)

    def test_both_missing_excluded_and_counted(self):
        self._pipe("N1", iso_weight=0, actual_weight=100.0)
        self._pipe("N2", iso_weight=200.0, actual_weight=190.0)
        data = analytics_service.weight_saving(
            analytics_service.parse_filters(_args())
        )
        # N1 excluded from aggregates but counted in the coverage note
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["count"], 1)
        self.assertEqual(data["summary"]["excluded_no_weight"], 1)

    def test_iso_metric_value_falls_back(self):
        p = self._pipe("N1", iso_weight=0, actual_weight=100.0, order=self.order)
        self.assertEqual(analytics_service.iso_metric_value(p, "weight"), 120.0)
        p2 = self._pipe("N2", iso_weight=50.0, actual_weight=40.0)
        self.assertEqual(analytics_service.iso_metric_value(p2, "weight"), 50.0)


if __name__ == "__main__":
    unittest.main()
