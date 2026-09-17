"""Application spec on the Product.

The product owns the specification: which standard it is built to (sewage →
ISO 8179 / EN 598, water → ISO 2531 / EN 545, AWWA either way) and the wall,
cement-lining and coating thicknesses as Value ± Tolerance. Which standards are
offered follows the Intended Use parameter's group, configured in admin.
"""

import unittest

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.product import Product, ProductParameter
from app.models.user import User
from app.services import application_spec_service


class ProductApplicationSpecTestCase(unittest.TestCase):
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

        self.sewage = ProductParameter(
            param_type="INTENT_USE", code="S", name_en="Sewage",
            is_active=True, application_group="sewage",
        )
        self.water = ProductParameter(
            param_type="INTENT_USE", code="W", name_en="Water",
            is_active=True, application_group="water",
        )
        # generate_product_code() runs on every save and the column is NOT
        # NULL, so a product used in an edit test needs a parameter to build a
        # code from.
        self.dn = ProductParameter(param_type="DN", code="P80",
                                   name_en="DN800", is_active=True)
        db.session.add_all([self.sewage, self.water, self.dn])
        db.session.commit()
        self.user_id = u.id
        self.sewage_id = self.sewage.id
        self.water_id = self.water.id
        self.dn_id = self.dn.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    @staticmethod
    def _visible_tabs(html):
        """The standards whose tab is on screen, in order."""
        import re
        tabs = html.split("app-spec-tabs", 1)[1].split("tab-content", 1)[0]
        out = []
        for item in tabs.split('<li class="nav-item"')[1:]:
            head = item.split(">", 1)[0]
            if "hidden" in head:
                continue
            m = re.search(r'data-std="([^"]+)"', item)
            if m:
                out.append(m.group(1))
        return out

    @staticmethod
    def _post(**over):
        form = {
            "app_block_present": "1",
            "app_group": "sewage",
            "app_thickness_value": "9",
            "app_thickness_tolerance_plus": "1",
            "app_thickness_tolerance_minus": "0.5",
            "app_thickness_min": "8.5",
            "app_thickness_nominal": "9",
            "app_thickness_max": "10",
            "app_cement_value": "5",
            "app_coating_value": "70",
            "is_active": "on",
        }
        form.update(over)
        return form

    def test_add_product_stores_the_spec(self):
        self._login()
        resp = self.client.post(
            "/admin/products/add",
            data=self._post(intent_use_param_id=str(self.sewage_id)),
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        product = Product.query.first()
        self.assertIsNotNone(product)
        self.assertEqual(product.application_profile["standards"]["en_598"], True)
        self.assertEqual(product.application_profile["standards"]["en_545"], False)
        layers = product.application_profile["layers"]
        self.assertEqual(layers["thickness"]["value"], 9.0)
        self.assertEqual(layers["thickness"]["min"], 8.5)
        self.assertEqual(layers["thickness"]["max"], 10.0)
        self.assertEqual(layers["cement"]["value"], 5.0)
        self.assertEqual(layers["coating"]["value"], 70.0)
        # the whole sewage group is part of the spec, not one of them
        self.assertEqual(product.standard_labels(),
                         ["ISO 8179", "EN 598", "AWWA"])

    def test_edit_product_updates_the_spec(self):
        self._login()
        product = Product(product_code="P1", dn_param_id=self.dn_id,
                          application_profile={
                              "standards": {"en_598": True},
                              "layers": {"thickness": {"value": 9.0}},
                          })
        db.session.add(product)
        db.session.commit()

        self.client.post(
            f"/admin/products/{product.id}/edit",
            data=self._post(app_group="water",
                            app_thickness_value="12",
                            dn_param_id=str(self.dn_id)),
            follow_redirects=True,
        )
        product = Product.query.get(product.id)
        self.assertEqual(product.standard_labels(),
                         ["ISO 2531", "EN 545", "AWWA"])
        self.assertEqual(product.application_profile["group"], "water")

    def test_a_form_without_the_block_keeps_the_saved_spec(self):
        """Same blank-keeps-prior rule as the order form: the checkboxes only
        post when ticked, so only the marker distinguishes 'not on screen' from
        'operator cleared everything'."""
        self._login()
        saved = {"standards": {"en_598": True}, "layers": {"thickness": {"value": 9.0}}}
        product = Product(product_code="P1", dn_param_id=self.dn_id,
                          application_profile=saved)
        db.session.add(product)
        db.session.commit()

        self.client.post(
            f"/admin/products/{product.id}/edit",
            data={"is_active": "on", "dn_param_id": str(self.dn_id)},  # no marker
            follow_redirects=True,
        )
        self.assertEqual(Product.query.get(product.id).application_profile, saved)

    def test_form_offers_only_the_intended_use_group(self):
        self._login()
        product = Product(product_code="P1", intent_use_param_id=self.sewage_id)
        db.session.add(product)
        db.session.commit()

        html = self.client.get(
            f"/admin/products/{product.id}/edit"
        ).get_data(as_text=True)

        # Every standard is in the page so switching Intended Use can reveal
        # the other group; the ones that do not apply are hidden.
        self.assertEqual(self._visible_tabs(html), ["iso_8179", "en_598", "awwa"])

    def test_untagged_intended_use_offers_everything(self):
        self._login()
        param = ProductParameter(param_type="INTENT_USE", code="X",
                                 name_en="Unspecified", is_active=True)
        db.session.add(param)
        db.session.commit()
        product = Product(product_code="P1", intent_use_param_id=param.id)
        db.session.add(product)
        db.session.commit()

        html = self.client.get(
            f"/admin/products/{product.id}/edit"
        ).get_data(as_text=True)
        self.assertEqual(
            self._visible_tabs(html),
            ["iso_8179", "en_598", "iso_2531", "en_545", "awwa"])

    def test_a_product_carries_every_standard_in_its_group(self):
        """The group is the only single choice. Every standard inside it —
        AWWA included — is part of the product's spec and gets its own data."""
        self._login()
        self.client.post(
            "/admin/products/add",
            data=self._post(app_group="water"),
            follow_redirects=True,
        )
        product = Product.query.first()
        ticked = {k for k, v in product.application_profile["standards"].items() if v}
        self.assertEqual(ticked, {"iso_2531", "en_545", "awwa"})
        self.assertEqual(product.application_profile["group"], "water")

    def test_a_sewage_product_carries_its_own_three(self):
        self._login()
        self.client.post(
            "/admin/products/add",
            data=self._post(app_group="sewage"),
            follow_redirects=True,
        )
        ticked = {k for k, v in
                  Product.query.first().application_profile["standards"].items() if v}
        self.assertEqual(ticked, {"iso_8179", "en_598", "awwa"})

    def test_the_form_renders_radios_not_checkboxes(self):
        self._login()
        html = self.client.get("/admin/products/add").get_data(as_text=True)
        # Intended Use decides the group, so nothing is picked here at all —
        # no checkboxes, no radios, just the tabs
        block = html.split('name="app_block_present"', 1)[1] \
                    .split("app-spec-tabs", 1)[0]
        self.assertNotIn('type="checkbox"', block)
        self.assertNotIn('type="radio"', block)
        self.assertIn('name="app_group"', block)

    def test_awwa_belongs_to_both_groups(self):
        """AWWA applies to sewage and water alike, so it is part of whichever
        group the product is in."""
        self.assertIn("awwa",
                      application_spec_service.standards_for_group("sewage"))
        self.assertIn("awwa",
                      application_spec_service.standards_for_group("water"))

    def test_awwa_gets_a_tab_in_either_group(self):
        self._login()
        html = self.client.get("/admin/products/add").get_data(as_text=True)
        self.assertIn('id="appLayerTable_pane_awwa"', html)
        self.assertIn('name="app_spec-awwa-thickness-value"', html)

    def test_a_legacy_multi_select_record_still_reads(self):
        """Records written while this was a multi-select must still read."""
        legacy = {"standards": {"en_598": True, "awwa": True},
                  "layers": {"thickness": {"value": 9.0}}}
        self.assertEqual(application_spec_service.selected_group(legacy),
                         "sewage")

    def test_an_order_picking_a_standard_stores_only_that_one(self):
        """The order is where one standard is chosen; the product is not."""
        profile, _ok = application_spec_service.read_application_profile({
            "app_block_present": "1",
            "app_group": "water",
            "app_std": "en_545",
        })
        ticked = {k for k, v in profile["standards"].items() if v}
        self.assertEqual(ticked, {"en_545"})

    # --- intended use decides the group ----------------------------------

    def test_the_intent_code_decides_the_group(self):
        """S is sewage, L and E are water. Read off the code so the gate works
        on the parameters already in the database, with no re-entry."""
        for code, expected in (("S", "sewage"), ("L", "water"), ("E", "water"),
                               ("s", "sewage"), ("Z", "both")):
            param = ProductParameter(param_type="INTENT_USE", code=code,
                                     name_en=code)
            self.assertEqual(
                application_spec_service.group_for_intent(param), expected, code)

    def test_the_admin_tag_overrides_the_code(self):
        param = ProductParameter(param_type="INTENT_USE", code="S",
                                 name_en="Odd", application_group="water")
        self.assertEqual(application_spec_service.group_for_intent(param),
                         "water")

    def test_a_both_tag_is_not_an_answer_and_the_code_still_decides(self):
        """"both" is the admin select's default, so any Intended Use saved for
        an unrelated reason comes back tagged with it. Prod had all three
        tagged that way; the block then rendered with no group, posted an empty
        app_group, and the next product save cleared the Application."""
        for code, expected in (("S", "sewage"), ("L", "water"), ("E", "water")):
            param = ProductParameter(param_type="INTENT_USE", code=code,
                                     name_en=code, application_group="both")
            self.assertEqual(
                application_spec_service.group_for_intent(param), expected, code)

    def test_an_ungated_post_never_clears_a_saved_application(self):
        """A form that renders the block but resolves to no group and no tick
        is the gate failing, not the operator clearing the spec. It must read
        as "nothing authored" so the caller keeps what the product had."""
        profile, has_any = application_spec_service.read_application_profile({
            "app_block_present": "1",
            "app_group": "",
        })
        self.assertIsNone(profile)
        self.assertFalse(has_any)

    def test_editing_only_the_weight_leaves_the_application_alone(self):
        """The prod incident: the weight was corrected on two products and both
        lost their group and every standard tick."""
        self._login()
        water = ProductParameter(param_type="INTENT_USE", code="L",
                                 name_en="Water line", is_active=True,
                                 application_group="both")
        db.session.add(water)
        db.session.commit()
        product = Product(product_code="P1", intent_use_param_id=water.id,
                          weight_kg=1399.0)
        product.application_profile = {
            "group": "water",
            "standards": {"iso_8179": False, "en_598": False,
                          "iso_2531": True, "en_545": True, "awwa": True},
            "specs": {}, "layers": {},
        }
        db.session.add(product)
        db.session.commit()
        product_id = product.id

        self.client.post(f"/admin/products/{product_id}/edit", data={
            "product_code": "P1",
            "weight_kg": "1394",
            "intent_use_param_id": str(water.id),
            "app_block_present": "1",
            "app_group": "",
        }, follow_redirects=True)

        saved = db.session.get(Product, product_id)
        self.assertEqual(1394.0, saved.weight_kg)
        self.assertEqual("water", saved.application_profile["group"])
        self.assertTrue(saved.application_profile["standards"]["iso_2531"])
        self.assertTrue(saved.application_profile["standards"]["en_545"])

    def test_a_water_product_never_offers_sewage(self):
        self._login()
        water = ProductParameter(param_type="INTENT_USE", code="L",
                                 name_en="Water line", is_active=True)
        db.session.add(water)
        db.session.commit()
        product = Product(product_code="P1", intent_use_param_id=water.id)
        db.session.add(product)
        db.session.commit()

        html = self.client.get(
            f"/admin/products/{product.id}/edit").get_data(as_text=True)
        self.assertEqual(self._visible_tabs(html), ["iso_2531", "en_545", "awwa"])

    def test_the_other_group_is_in_the_page_ready_to_be_revealed(self):
        """Switching Intended Use in the browser can only show a tab that was
        rendered. Rendering just the current group left a sewage switch with
        AWWA alone on screen, because the sewage tabs did not exist."""
        self._login()
        water = ProductParameter(param_type="INTENT_USE", code="L",
                                 name_en="Water line", is_active=True)
        db.session.add(water)
        db.session.commit()
        product = Product(product_code="P1", intent_use_param_id=water.id)
        db.session.add(product)
        db.session.commit()

        html = self.client.get(
            f"/admin/products/{product.id}/edit").get_data(as_text=True)
        tabs = html.split("app-spec-tabs", 1)[1].split("tab-content", 1)[0]
        for key in ("iso_8179", "en_598"):
            self.assertIn('data-std="%s"' % key, tabs)
        self.assertNotIn("iso_8179", "".join(self._visible_tabs(html)))

    # --- every standard keeps its own table ------------------------------

    def test_each_standard_stores_its_own_thicknesses(self):
        """A product can be specified under two standards at once; only one is
        active, but both sets of numbers are kept."""
        self._login()
        self.client.post("/admin/products/add", data={
            "app_block_present": "1",
            "app_group": "water",
            "app_std": "iso_2531",
            "app_spec-iso_2531-thickness-value": "9",
            "app_spec-iso_2531-cement-value": "5",
            "app_spec-en_545-thickness-value": "12",
            "is_active": "on",
        }, follow_redirects=True)

        specs = Product.query.first().application_profile["specs"]
        self.assertEqual(specs["iso_2531"]["thickness"]["value"], 9.0)
        self.assertEqual(specs["iso_2531"]["cement"]["value"], 5.0)
        self.assertEqual(specs["en_545"]["thickness"]["value"], 12.0)

    def test_layers_mirrors_the_active_standard(self):
        """Everything downstream reads `layers`, so it has to be the active
        standard's table or the order and the stages get the wrong numbers."""
        self._login()
        self.client.post("/admin/products/add", data={
            "app_block_present": "1",
            "app_group": "water",
            "app_std": "en_545",
            "app_spec-iso_2531-thickness-value": "9",
            "app_spec-en_545-thickness-value": "12",
            "is_active": "on",
        }, follow_redirects=True)

        profile = Product.query.first().application_profile
        self.assertEqual(profile["layers"]["thickness"]["value"], 12.0)

    def test_the_form_renders_one_tab_per_standard(self):
        self._login()
        html = self.client.get("/admin/products/add").get_data(as_text=True)
        self.assertIn('id="appLayerTable_pane_iso_2531"', html)
        self.assertIn('id="appLayerTable_pane_en_545"', html)
        self.assertIn('name="app_spec-en_545-thickness-value"', html)

    def test_a_record_without_tabs_still_reads(self):
        legacy = {"standards": {"en_598": True},
                  "layers": {"thickness": {"value": 9.0}}}
        rows = application_spec_service.layers_for(legacy, "en_598")
        self.assertEqual(rows["thickness"]["value"], 9.0)

    def test_products_list_shows_the_spec(self):
        self._login()
        db.session.add(Product(product_code="P1", application_profile={
            "standards": {"en_545": True},
            "layers": {"thickness": {"nominal": 9.0}},
        }))
        db.session.commit()

        html = self.client.get("/admin/products").get_data(as_text=True)
        self.assertIn("EN 545", html)
        self.assertIn("9.0", html)

    def test_parameter_form_saves_the_standards_group(self):
        self._login()
        self.client.post("/admin/product-parameters/add", data={
            "param_type": "INTENT_USE", "code": "C", "name_en": "Combined",
            "application_group": "water", "is_active": "on",
        }, follow_redirects=True)
        param = ProductParameter.query.filter_by(code="C").first()
        self.assertEqual(param.application_group, "water")

    def test_group_is_only_stored_on_intended_use(self):
        """A stray application_group on some other parameter type must not gate
        the product's standards."""
        self._login()
        self.client.post("/admin/product-parameters/add", data={
            "param_type": "CLASS", "code": "K9", "name_en": "K9",
            "application_group": "water", "is_active": "on",
        }, follow_redirects=True)
        self.assertIsNone(ProductParameter.query.filter_by(code="K9").first().application_group)


class ApplicationSpecServiceTestCase(unittest.TestCase):
    def test_groups_follow_the_parameter_tag(self):
        class P:
            application_group = "sewage"
        self.assertEqual(
            application_spec_service.groups_for_intent(P()),
            {"sewage", "other"},
        )

    def test_no_parameter_offers_everything(self):
        self.assertEqual(
            application_spec_service.groups_for_intent(None),
            {"sewage", "water", "other"},
        )

    def test_legacy_single_tolerance_is_read_into_both_sides(self):
        """Records written before the +Tol / -Tol split carry one key; they
        rendered empty tolerance cells until normalise() read it back."""
        view = application_spec_service.normalise({
            "standards": {"en_598": True},
            "layers": {"thickness": {"value": 9.0, "tolerance": 0.5}},
        })
        row = view["layers"]["thickness"]
        self.assertEqual(row["tolerance_plus"], 0.5)
        self.assertEqual(row["tolerance_minus"], 0.5)
        self.assertNotIn("tolerance", row)

    def test_current_tolerances_win_over_the_legacy_key(self):
        view = application_spec_service.normalise({
            "layers": {"thickness": {"tolerance": 0.5, "tolerance_plus": 1.0}},
        })
        row = view["layers"]["thickness"]
        self.assertEqual(row["tolerance_plus"], 1.0)
        self.assertEqual(row["tolerance_minus"], 0.5)


if __name__ == "__main__":
    unittest.main()
