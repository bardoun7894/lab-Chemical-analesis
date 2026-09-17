"""Product barcode persistence + sticker barcode wiring (DrAlaa 2026-08-13).

The `barcode` column was lost from the Product model in a sync while the
admin routes and form kept referencing `product.barcode`. Adding a product
crashed (TypeError on the unmapped kwarg) and editing silently dropped the
barcode — "add it to any product, it won't save".

The sticker's Code128 barcode encoded the pipe code (so it looked "fixed"
and didn't scan); it should encode the product's barcode number instead.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.routes.stickers import _sticker_barcode_value


class ProductBarcodeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_barcode_column_persists_on_create(self):
        p = Product(product_code="BAR-TEST-1", barcode="2100000172400")
        db.session.add(p)
        db.session.commit()
        db.session.refresh(p)
        self.assertEqual(p.barcode, "2100000172400")

        fetched = Product.query.filter_by(product_code="BAR-TEST-1").first()
        self.assertEqual(fetched.barcode, "2100000172400")

    def test_barcode_column_persists_on_update(self):
        p = Product(product_code="BAR-TEST-2", barcode="111")
        db.session.add(p)
        db.session.commit()

        p.barcode = "2100000172400"
        db.session.commit()
        db.session.refresh(p)
        self.assertEqual(p.barcode, "2100000172400")

        fetched = Product.query.filter_by(product_code="BAR-TEST-2").first()
        self.assertEqual(fetched.barcode, "2100000172400")

    # --- Sticker barcode wiring --------------------------------------------

    def _pipe(self, no_code="P1", pipe_code=None, **kw):
        p = Pipe(
            production_date=date(2026, 8, 1), ladle_id="L1", no_code=no_code,
            pipe_code=pipe_code or no_code, arrange_pipe=1, diameter=300,
            pipe_class="K9", **kw,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _product(self, code="PROD-1", barcode=None):
        p = Product(product_code=code, barcode=barcode)
        db.session.add(p)
        db.session.commit()
        return p

    def _order(self, number, product=None):
        o = ProductionOrder(
            order_number=number, target_quantity=1,
            product_id=product.id if product else None,
        )
        db.session.add(o)
        db.session.commit()
        return o

    def test_sticker_barcode_uses_product_barcode(self):
        product = self._product(barcode="2100000172400")
        order = self._order("PO-1", product=product)
        pipe = self._pipe("P1", production_order_id=order.id)
        self.assertEqual(_sticker_barcode_value(pipe), "2100000172400")

    def test_sticker_barcode_falls_back_to_pipe_code_when_no_product_barcode(self):
        product = self._product(barcode=None)
        order = self._order("PO-2", product=product)
        pipe = self._pipe("P2", pipe_code="PIPE-CODE-2", production_order_id=order.id)
        self.assertEqual(_sticker_barcode_value(pipe), "PIPE-CODE-2")

    def test_sticker_barcode_falls_back_to_pipe_code_without_order(self):
        pipe = self._pipe("P3", pipe_code="PIPE-CODE-3")
        self.assertEqual(_sticker_barcode_value(pipe), "PIPE-CODE-3")


if __name__ == "__main__":
    unittest.main()
