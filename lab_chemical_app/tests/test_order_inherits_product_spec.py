"""A production order starts as a copy of its product's Application spec.

The product owns the specification; the order snapshots it and may then be
edited for a customer who wants something slightly different. Editing the
product afterwards must leave orders already written alone.
"""

import json
import unittest

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services import application_spec_service

SPEC = {
    "standards": {"iso_8179": False, "en_598": True,
                  "iso_2531": False, "en_545": False, "awwa": True},
    "layers": {
        "thickness": {"value": 9.0, "tolerance_plus": 1.0, "tolerance_minus": 0.5,
                      "min": 8.5, "nominal": 9.0, "max": 10.0},
        "cement": {"value": 5.0, "tolerance_plus": None, "tolerance_minus": None,
                   "min": None, "nominal": 5.0, "max": None},
        "coating": {"value": 70.0, "tolerance_plus": None, "tolerance_minus": None,
                    "min": None, "nominal": 70.0, "max": None},
    },
}


class OrderInheritsProductSpecTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="root", full_name="Root", role="super_admin", is_active=True)
        u.set_password("x")
        db.session.add(u)

        product = Product(product_code="P-800-K9", description_en="DN800 K9",
                          weight_kg=100.0, length_m=6.0, is_active=True,
                          application_profile=SPEC)
        db.session.add(product)
        db.session.commit()
        self.user_id = u.id
        self.product_id = product.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_order_form_carries_the_product_spec(self):
        """The dropdown option carries the whole profile, so picking a product
        fills the block without a round trip."""
        self._login()
        html = self.client.get("/orders/add").get_data(as_text=True)
        option = html.split(f'<option value="{self.product_id}"', 1)[1].split(">", 1)[0]
        self.assertIn("data-app=", option)
        self.assertIn("en_598", option)

    def test_creating_an_order_without_the_block_snapshots_the_product(self):
        """OCR and API paths never render the Application block; they still get
        the product's spec."""
        self._login()
        resp = self.client.post("/orders/add", data={
            "order_number": "PO-1", "target_quantity": "10",
            "product_id": str(self.product_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        order = ProductionOrder.query.filter_by(order_number="PO-1").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.application_profile, SPEC)
        # An order runs to one standard, so the label is the one it picked.
        # SPEC ticks EN 598 and AWWA, a shape from when this was a
        # multi-select; the first in sheet order wins.
        self.assertEqual(order.standard_labels(), ["EN 598"])

    def test_a_posted_block_overrides_the_product(self):
        self._login()
        self.client.post("/orders/add", data={
            "order_number": "PO-2", "target_quantity": "10",
            "product_id": str(self.product_id),
            "app_block_present": "1",
            "app_group": "sewage",
            "app_std": "en_598",
            "app_thickness_value": "9",
            "app_cement_value": "7",  # this customer wants a thicker lining
        }, follow_redirects=True)

        order = ProductionOrder.query.filter_by(order_number="PO-2").first()
        self.assertEqual(order.application_profile["layers"]["cement"]["value"], 7.0)
        self.assertEqual(order.application_profile["layers"]["thickness"]["value"], 9.0)

    def test_the_order_follows_the_products_figures(self):
        """The spec is authored on the product and the order only names the
        standard, so correcting a figure on the product reaches the orders and
        the stages that run to it.

        This reverses the earlier snapshot behaviour deliberately: an order
        that kept its own stale copy showed one band on screen while the
        product said another, and nobody could tell which the floor was
        measuring against.
        """
        self._login()
        self.client.post("/orders/add", data={
            "order_number": "PO-3", "target_quantity": "10",
            "product_id": str(self.product_id),
        }, follow_redirects=True)

        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "standards": {"en_598": True},
            "specs": {"en_598": {"thickness": {"value": 12.0, "min": 11.0}}},
        }
        db.session.commit()

        order = ProductionOrder.query.filter_by(order_number="PO-3").first()
        # the stored copy is untouched — the order row is not rewritten
        self.assertEqual(
            order.application_profile["layers"]["thickness"]["value"], 9.0)
        # but what the screens and the stages read is the product's
        eff = order.effective_application()
        self.assertEqual(eff["layers"]["thickness"]["value"], 12.0)
        self.assertEqual(order.standard_labels(), ["EN 598"])

    def test_an_order_carrying_its_own_copy_still_follows_the_product(self):
        """The order form ships the product's whole set in app_specs_json so
        switching the choice needs no round trip, so nearly every order has a
        copy. That copy used to win outright and froze the order against later
        corrections — prod had two, one of them the only order with pipes.
        """
        product = db.session.get(Product, self.product_id)
        order = ProductionOrder(order_number="PO-COPY", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {
            "group": "sewage",
            "standards": {"en_598": True},
            # the snapshot taken when the order was written
            "specs": {"en_598": {"thickness": {"value": 9.0, "min": 8.5,
                                               "nominal": 9.0, "max": 10.0}}},
        }
        db.session.add(order)
        db.session.commit()

        # the product is corrected afterwards
        product.application_profile = {
            "group": "sewage",
            "standards": {"en_598": True},
            "specs": {"en_598": {"thickness": {"value": 12.0, "min": 11.0,
                                               "nominal": 12.0, "max": 13.0}}},
        }
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual((band["min"], band["nominal"], band["max"]),
                         (11.0, 12.0, 13.0))

    def test_the_orders_copy_covers_a_standard_the_product_dropped(self):
        """Only where the product has nothing. Otherwise an order would lose
        its figures the moment a table was cleared on the product."""
        product = db.session.get(Product, self.product_id)
        order = ProductionOrder(order_number="PO-GAP", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {
            "group": "sewage",
            "standards": {"awwa": True},
            "specs": {"awwa": {"thickness": {"value": 4.0, "min": 3.0,
                                             "nominal": 4.0, "max": 5.0}}},
        }
        db.session.add(order)
        product.application_profile = {
            "group": "sewage",
            "specs": {"en_598": {"thickness": {"value": 12.0, "min": 11.0,
                                               "nominal": 12.0, "max": 13.0}}},
        }
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual((band["min"], band["nominal"], band["max"]),
                         (3.0, 4.0, 5.0))

    def test_order_detail_marks_changed_rows(self):
        self._login()
        order = ProductionOrder(order_number="PO-4", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {
            "standards": dict(SPEC["standards"]),
            "layers": {
                "thickness": dict(SPEC["layers"]["thickness"]),
                "cement": dict(SPEC["layers"]["cement"], value=7.0, nominal=7.0),
                "coating": dict(SPEC["layers"]["coating"]),
            },
        }
        db.session.add(order)
        db.session.commit()

        html = self.client.get(f"/orders/{order.id}").get_data(as_text=True)
        self.assertIn("changed for this order", html)
        self.assertIn("from product", html)

    def test_stage_form_still_carries_the_order_spec(self):
        """The order -> stage hop is what actually reaches the operator; it
        reads the same profile off the order option."""
        self._login()
        order = ProductionOrder(order_number="PO-5", target_quantity=5,
                                product_id=self.product_id,
                                application_profile=SPEC, status="pending")
        db.session.add(order)
        db.session.commit()

        html = self.client.get("/stages/add").get_data(as_text=True)
        option = html.split(f'value="{order.id}"', 1)[1].split(">", 1)[0]
        self.assertIn("data-app=", option)
        self.assertIn("en_598", option)


    # --- the order picks one standard, it does not author tables ---------

    def test_the_order_offers_a_choice_not_tabs(self):
        """The product authors a table per standard; an order is built to one
        of them, so here it is a radio and the figures are read-only."""
        self._login()
        html = self.client.get("/orders/add").get_data(as_text=True)
        block = html.split('name="app_block_present"', 1)[1] \
                    .split("</table>", 1)[0]
        self.assertIn('name="app_std"', block)
        self.assertIn('type="radio"', block)
        self.assertNotIn("app-spec-tabs", block)
        # the figures come from the product and are not edited per order
        self.assertIn("readonly", block)

    def test_the_order_form_does_not_show_intended_use(self):
        """It belongs to the product. The order only needs what it decides —
        the Application group — and that is shown in the block itself."""
        self._login()
        html = self.client.get("/orders/add").get_data(as_text=True)
        self.assertNotIn('id="fieldIntent"', html)
        self.assertNotIn(">Intended Use<", html)

    def test_the_order_stores_the_standard_it_was_built_to(self):
        self._login()
        self.client.post("/orders/add", data={
            "order_number": "PO-9", "target_quantity": "5",
            "product_id": str(self.product_id),
            "app_block_present": "1",
            "app_group": "sewage",
            "app_std": "en_598",
            "app_thickness_value": "9",
            "app_thickness_min": "8.5",
            "app_thickness_max": "10",
        }, follow_redirects=True)

        order = ProductionOrder.query.filter_by(order_number="PO-9").first()
        ticked = {k for k, v in order.application_profile["standards"].items() if v}
        self.assertEqual(ticked, {"en_598"})
        self.assertEqual(order.standard_labels(), ["EN 598"])
        # what the stages read is the chosen standard's band
        self.assertEqual(order.application_profile["layers"]["thickness"]["min"], 8.5)

    def test_the_order_keeps_the_products_other_tables(self):
        """Reopening the order and switching the choice must still have
        figures to show, so the whole set travels with it."""
        self._login()
        self.client.post("/orders/add", data={
            "order_number": "PO-10", "target_quantity": "5",
            "product_id": str(self.product_id),
            "app_block_present": "1",
            "app_group": "water",
            "app_std": "en_545",
            "app_specs_json": (
                '{"iso_2531": {"thickness": {"value": 9.0}},'
                ' "en_545": {"thickness": {"value": 12.0}}}'),
        }, follow_redirects=True)

        specs = ProductionOrder.query.filter_by(
            order_number="PO-10").first().application_profile["specs"]
        self.assertEqual(specs["iso_2531"]["thickness"]["value"], 9.0)
        self.assertEqual(specs["en_545"]["thickness"]["value"], 12.0)

    # --- the figures shown come from the product --------------------------

    def test_an_order_with_no_tables_of_its_own_shows_the_products(self):
        """Order 44 in production: saved before the tables existed, so its own
        specs are empty. Picking a standard showed a blank grid."""
        product = {"group": "sewage",
                   "specs": {"iso_8179": {"thickness": {"value": 9.0}},
                             "en_598": {"thickness": {"value": 3.0}}}}
        order = {"standards": {"iso_8179": True}, "specs": {},
                 "layers": {"thickness": {"value": 1.0}}}

        view = application_spec_service.for_order(order, product)
        self.assertEqual(view["specs"]["en_598"]["thickness"]["value"], 3.0)
        self.assertEqual(view["layers"]["thickness"]["value"], 9.0)

    def test_a_choice_outside_the_products_group_is_dropped(self):
        """Order 44 still named ISO 2531 after its product moved to sewage.
        Showing a water standard on a sewage order would be a lie."""
        product = {"group": "sewage",
                   "specs": {"iso_8179": {"thickness": {"value": 9.0}}}}
        order = {"standards": {"iso_2531": True}}

        view = application_spec_service.for_order(order, product)
        self.assertEqual(view["group"], "sewage")
        chosen = {k for k, v in view["standards"].items() if v}
        self.assertEqual(chosen, {"iso_8179"})

    def test_the_group_follows_the_product(self):
        view = application_spec_service.for_order(
            {"group": "water"}, {"group": "sewage", "specs": {}})
        self.assertEqual(view["group"], "sewage")

    def test_a_valid_choice_is_kept(self):
        product = {"group": "sewage",
                   "specs": {"iso_8179": {"thickness": {"value": 9.0}},
                             "en_598": {"thickness": {"value": 3.0}}}}
        view = application_spec_service.for_order(
            {"standards": {"en_598": True}}, product)
        chosen = {k for k, v in view["standards"].items() if v}
        self.assertEqual(chosen, {"en_598"})
        self.assertEqual(view["layers"]["thickness"]["value"], 3.0)

    def test_the_order_form_shows_the_products_figures(self):
        self._login()
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "standards": {"iso_8179": True, "en_598": True, "awwa": True},
            "specs": {"iso_8179": {"thickness": {"value": 9.0}},
                      "en_598": {"thickness": {"value": 3.0}}},
        }
        order = ProductionOrder(order_number="PO-12", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"iso_8179": True}}
        db.session.add(order)
        db.session.commit()

        html = self.client.get(f"/orders/{order.id}/edit").get_data(as_text=True)
        self.assertIn("Sewage", html)           # the group is named
        self.assertIn('"en_598"', html)         # the other tables travel too
        block = html.split('name="app_block_present"', 1)[1] \
                    .split("</table>", 1)[0]
        self.assertIn('value="9.0"', block)     # the chosen standard's figure

    # --- and it has to reach the stage popups -----------------------------

    def _sewage_order(self):
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "standards": {"iso_8179": True, "en_598": True, "awwa": True},
            "specs": {
                "iso_8179": {"thickness": {"value": 9.0, "min": 8.5, "max": 10.0},
                             "cement": {"value": 5.0, "min": 4.0, "max": 6.0},
                             "coating": {"value": 70.0, "min": 65.0, "max": 75.0}},
                "en_598": {"thickness": {"value": 3.0, "min": 2.5, "max": 3.5}},
            },
        }
        order = ProductionOrder(order_number="PO-ST", target_quantity=5,
                                product_id=self.product_id, status="pending")
        order.application_profile = {"standards": {"en_598": True}}
        db.session.add(order)
        db.session.commit()
        return order

    def test_effective_application_is_the_chosen_standards_band(self):
        order = self._sewage_order()
        eff = order.effective_application()
        self.assertEqual(eff["layers"]["thickness"]["min"], 2.5)
        self.assertEqual(eff["layers"]["thickness"]["max"], 3.5)
        self.assertEqual(order.standard_labels(), ["EN 598"])

    def test_the_band_reaches_the_pipe_register_form(self):
        """The CCM and Coating popups prefill from the order option's data-app,
        so the band the readings are judged against has to be in there."""
        self._login()
        order = self._sewage_order()
        html = self.client.get("/stages/add").get_data(as_text=True)
        option = html.split(f'value="{order.id}"', 1)[1].split(">", 1)[0]
        self.assertIn("data-app=", option)
        # `layers` is what the popups read, and it must be EN 598's band.
        # The other tables ride along in `specs`, which is fine.
        payload = json.loads(html.split("data-app='", 1)[1].split("'", 1)[0])
        self.assertEqual(payload["layers"]["thickness"]["min"], 2.5)
        self.assertEqual(payload["layers"]["thickness"]["max"], 3.5)

    def test_an_order_with_no_figures_of_its_own_still_feeds_the_stages(self):
        """This is what broke on order 44: no specs of its own meant the stage
        popups got an empty band and nothing was judged."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "water",
            "specs": {"iso_2531": {"thickness": {"value": 9.0, "min": 8.0,
                                                 "max": 10.0}}},
        }
        order = ProductionOrder(order_number="PO-EMPTY", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {}, "specs": {}}
        db.session.add(order)
        db.session.commit()

        eff = order.effective_application()
        self.assertEqual(eff["layers"]["thickness"]["min"], 8.0)

    def test_a_value_and_tolerance_alone_still_produce_a_band(self):
        """Nominal/Min/Max are derived in the browser on the product form. A
        spec that arrived without them still has to reach the popups."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "water",
            "specs": {"iso_2531": {"thickness": {
                "value": 9.0, "tolerance_plus": 1.0, "tolerance_minus": 0.5}}},
        }
        order = ProductionOrder(order_number="PO-DERIVE", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"iso_2531": True}}
        db.session.add(order)
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual(band["nominal"], 9.0)
        self.assertEqual(band["min"], 8.5)
        self.assertEqual(band["max"], 10.0)

    def _intent(self, code):
        from app.models.product import ProductParameter
        param = ProductParameter(param_type="INTENT_USE", code=code,
                                 name_en=code, name_ar=code, is_active=True)
        db.session.add(param)
        db.session.commit()
        return param

    def test_intended_use_decides_the_group_not_the_saved_copy(self):
        """Intended Use is the field the operator sets. The group on the
        profile is only a cache of it, written when the block was last saved,
        and switching S to L has to move the order to the water standards
        without anyone reopening the product's Application block."""
        product = db.session.get(Product, self.product_id)
        product.intent_use_param = self._intent("S")
        product.application_profile = {
            "group": "sewage",
            "specs": {"iso_8179": {"thickness": {"value": 9.0, "min": 8.0,
                                                 "nominal": 9.0, "max": 10.0}},
                      "iso_2531": {"thickness": {"value": 4.0, "min": 3.0,
                                                 "nominal": 4.0, "max": 5.0}}},
        }
        order = ProductionOrder(order_number="PO-INTENT", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"iso_8179": True}}
        db.session.add(order)
        db.session.commit()
        self.assertEqual(order.effective_application()["group"], "sewage")
        self.assertEqual(order.standard_labels(), ["ISO 8179"])

        # Intended Use alone is changed — the stored group still says sewage.
        product.intent_use_param = self._intent("L")
        db.session.commit()

        eff = order.effective_application()
        self.assertEqual(eff["group"], "water")
        # ISO 8179 is a sewage standard, so it is dropped and the order falls
        # to the water table that actually has figures.
        self.assertEqual(order.standard_labels(), ["ISO 2531"])
        self.assertEqual(eff["layers"]["thickness"]["nominal"], 4.0)

    def test_an_intended_use_that_says_neither_leaves_the_group_alone(self):
        product = db.session.get(Product, self.product_id)
        product.intent_use_param = self._intent("ZZ")   # not S, L or E
        product.application_profile = {
            "group": "water",
            "specs": {"iso_2531": {"thickness": {"value": 4.0, "min": 3.0,
                                                 "nominal": 4.0, "max": 5.0}}},
        }
        order = ProductionOrder(order_number="PO-NEITHER", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"iso_2531": True}}
        db.session.add(order)
        db.session.commit()

        self.assertEqual(order.effective_application()["group"], "water")
        self.assertEqual(order.standard_labels(), ["ISO 2531"])

    def test_an_impossible_band_is_rebuilt_from_value_and_tolerance(self):
        """Prod's product 9 EN 598 thickness: min 2.5 with max 2.0, the 1.0 and
        2.0 left frozen from before the value was changed to 3.0. Every reading
        judged against that is out of spec."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "specs": {"en_598": {"thickness": {
                "value": 3.0, "tolerance_plus": 6.0, "tolerance_minus": 0.5,
                "min": 2.5, "nominal": 1.0, "max": 2.0}}},
        }
        order = ProductionOrder(order_number="PO-BROKEN", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"en_598": True}}
        db.session.add(order)
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual(band["nominal"], 3.0)
        self.assertEqual(band["min"], 2.5)
        self.assertEqual(band["max"], 9.0)
        self.assertLessEqual(band["min"], band["max"])

    def test_a_stale_limit_is_recomputed_not_honoured(self):
        """The three limits are derived, never authored. A stored Max that
        disagrees with Value + Tol is a limit left behind when the Value moved,
        not an override — prod carried Max 70.0 against Value 70.0 with a +Tol
        of 5.0, and Max 2.0 against Value 1.0 with a +Tol of 25.0."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "specs": {"en_598": {
                "thickness": {"value": 1.0, "tolerance_plus": 25.0,
                              "tolerance_minus": 0.5,
                              "min": 0.5, "nominal": 1.0, "max": 2.0},
                "cement": {"value": 5.0, "tolerance_plus": 5.0,
                           "tolerance_minus": 2.0,
                           "min": 3.0, "nominal": 5.0, "max": 7.0},
                "coating": {"value": 70.0, "tolerance_plus": 5.0,
                            "tolerance_minus": 5.0,
                            "min": 65.0, "nominal": 70.0, "max": 70.0}}},
        }
        order = ProductionOrder(order_number="PO-STALE", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"en_598": True}}
        db.session.add(order)
        db.session.commit()

        layers = order.effective_application()["layers"]
        self.assertEqual(
            (layers["thickness"]["min"], layers["thickness"]["nominal"],
             layers["thickness"]["max"]), (0.5, 1.0, 26.0))
        self.assertEqual(
            (layers["cement"]["min"], layers["cement"]["nominal"],
             layers["cement"]["max"]), (3.0, 5.0, 10.0))
        self.assertEqual(
            (layers["coating"]["min"], layers["coating"]["nominal"],
             layers["coating"]["max"]), (65.0, 70.0, 75.0))

    def test_one_tolerance_only_leaves_the_other_side_at_the_value(self):
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "specs": {"en_598": {"thickness": {"value": 9.0,
                                               "tolerance_plus": 1.0}}},
        }
        order = ProductionOrder(order_number="PO-ONESIDE", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"en_598": True}}
        db.session.add(order)
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual((band["min"], band["nominal"], band["max"]),
                         (9.0, 9.0, 10.0))

    def test_an_unrepairable_band_withholds_the_verdict(self):
        """With no value and no tolerance to rebuild from, the limits are
        dropped so the grid says so instead of failing every reading."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "sewage",
            "specs": {"en_598": {"thickness": {
                "min": 9.0, "nominal": 5.0, "max": 2.0}}},
        }
        order = ProductionOrder(order_number="PO-JUNK", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"en_598": True}}
        db.session.add(order)
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertIsNone(band["min"])
        self.assertIsNone(band["max"])

    def test_no_tolerance_is_not_turned_into_a_zero_width_band(self):
        """Min == Max == nominal would fail every reading that is not exactly
        nominal. An absent tolerance stays absent, and the grid says so."""
        product = db.session.get(Product, self.product_id)
        product.application_profile = {
            "group": "water",
            "specs": {"iso_2531": {"thickness": {"value": 9.0}}},
        }
        order = ProductionOrder(order_number="PO-NOTOL", target_quantity=5,
                                product_id=self.product_id)
        order.application_profile = {"standards": {"iso_2531": True}}
        db.session.add(order)
        db.session.commit()

        band = order.effective_application()["layers"]["thickness"]
        self.assertEqual(band["nominal"], 9.0)
        self.assertIsNone(band.get("min"))
        self.assertIsNone(band.get("max"))

    def test_editing_a_pipe_renders_the_band_without_waiting_for_js(self):
        """data-app only reaches the boxes on a change event. Editing a pipe
        fires none, so the band has to be server-rendered or the readings on
        an already-registered pipe are judged against nothing."""
        from datetime import date

        from app.models.pipe import Pipe
        from app.models.stage import ProductionStage

        self._login()
        order = self._sewage_order()
        pipe = Pipe(production_date=date(2026, 8, 31), ladle_id="SP1",
                    pipe_code="SP1-P1", no_code="S9001", arrange_pipe=1,
                    diameter=800, pipe_class="K9",
                    production_order_id=order.id)
        db.session.add(pipe)
        db.session.commit()

        html = self.client.get(
            "/stages/%d/edit" % pipe.id).get_data(as_text=True)
        ccm = ProductionStage.name_for_code("ccm")
        coating = ProductionStage.name_for_code("coating")

        def value_of(name):
            chunk = html.split('name="%s"' % name, 1)[1].split(">", 1)[0]
            return chunk.split('value="', 1)[1].split('"', 1)[0]

        # EN 598 is the standard the order picked: 3.0 (2.5-3.5).
        self.assertEqual(value_of("stage_%s_dim_thick_std" % ccm), "3.0")
        self.assertEqual(value_of("stage_%s_dim_thick_std_min" % ccm), "2.5")
        self.assertEqual(value_of("stage_%s_dim_thick_std_max" % ccm), "3.5")
        # EN 598 names no cement or coating, so those stay empty and say so.
        self.assertEqual(value_of("stage_%s_thick_cement_std" % coating), "")
        self.assertIn('spec-note spec-note-missing" data-spec-for=', html)

    def test_a_malformed_specs_payload_is_ignored_not_fatal(self):
        self._login()
        resp = self.client.post("/orders/add", data={
            "order_number": "PO-11", "target_quantity": "5",
            "app_block_present": "1",
            "app_group": "water",
            "app_std": "en_545",
            "app_specs_json": "not json at all",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(
            ProductionOrder.query.filter_by(order_number="PO-11").first())


if __name__ == "__main__":
    unittest.main()
