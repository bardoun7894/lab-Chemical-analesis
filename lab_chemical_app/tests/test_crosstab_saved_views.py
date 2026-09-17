"""Pinning a crosstab selection to "لوحتي".

The existing KPI store holds six hardcoded whole-database lambdas that take no
arguments, so it could not express "this pivot, under these filters" at all. A
saved crosstab stores the selection rather than the numbers and recomputes on
every view, which is what makes a pinned tile stay true as production moves.
"""

import json
import os
import shutil
import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.product import Product
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes import analytics as analytics_routes


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.user = User(username="root", full_name="Root", role="super_admin",
                         is_active=True)
        self.user.set_password("x")
        self.product = Product(product_code="P300", weight_kg=100.0)
        db.session.add_all([self.user, self.product])
        db.session.commit()

        self.day = date(2026, 8, 20)
        self.ccm = ProductionStage.name_for_code("ccm")

        # The store is a real file — keep it and restore afterwards.
        self.store = analytics_routes.KPI_STORE_PATH
        self.backup = self.store + ".testbak"
        if os.path.exists(self.store):
            shutil.copy2(self.store, self.backup)
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump([], fh)

    def tearDown(self):
        if os.path.exists(self.backup):
            shutil.move(self.backup, self.store)
        elif os.path.exists(self.store):
            os.remove(self.store)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, diameter=300, decision="Accept", shift=1):
        pipe = Pipe(production_date=self.day, ladle_id="L1", pipe_code=code,
                    no_code=code, arrange_pipe=1, diameter=diameter,
                    pipe_class="K9", shift=shift, iso_weight=0.0,
                    actual_weight=90.0, product_id=self.product.id,
                    lab_decision="ACCEPT")
        db.session.add(pipe)
        db.session.commit()
        db.session.add(PipeStage(pipe_id=pipe.id, stage_name=self.ccm,
                                 decision=decision))
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user.id)
            sess["_fresh"] = True

    def _saved(self):
        with open(self.store, encoding="utf-8") as fh:
            return json.load(fh)


class SaveTest(_Base):
    def test_saving_stores_the_selection_not_the_numbers(self):
        self._pipe("K1")
        self._login()
        resp = self.client.post(
            "/analytics/kpis/save-crosstab"
            "?xt_set=1&xt_pivot=stage&xt_rows=dn&xt_m=rej_pct&xt_mset=1&shift=1",
            data={"render": "tile", "name": "رفض حسب المرحلة"},
        )
        self.assertEqual(resp.status_code, 302)
        saved = self._saved()
        self.assertEqual(len(saved), 1)
        sel = saved[0]["selection"]
        self.assertEqual(sel["pivot"], ["stage"])
        self.assertEqual(sel["rows"], ["dn"])
        self.assertEqual(sel["measures"], ["rej_pct"])
        self.assertEqual(sel["filters"]["shift"], "1")
        self.assertNotIn("current_value", saved[0],
                         "a saved view must not freeze a number")

    def test_unknown_dimensions_are_not_persisted(self):
        self._pipe("K1")
        self._login()
        self.client.post(
            "/analytics/kpis/save-crosstab?xt_pivot=nonsense&xt_rows=dn",
            data={"render": "tile"})
        self.assertEqual(self._saved()[0]["selection"]["pivot"], [])

    def test_ui_state_is_not_persisted_as_a_filter(self):
        self._pipe("K1")
        self._login()
        self.client.post(
            "/analytics/kpis/save-crosstab?xt_rows=dn&_panel=crosstab&xt_limit=5",
            data={"render": "tile"})
        self.assertNotIn("_panel", self._saved()[0]["selection"]["filters"])

    def test_render_kind_is_recorded(self):
        self._pipe("K1")
        self._login()
        self.client.post("/analytics/kpis/save-crosstab?xt_rows=dn",
                         data={"render": "table"})
        self.assertEqual(self._saved()[0]["render"], "table")

    def test_a_name_is_never_blank(self):
        self._pipe("K1")
        self._login()
        self.client.post("/analytics/kpis/save-crosstab?xt_rows=dn",
                         data={"render": "tile", "name": "   "})
        self.assertTrue(self._saved()[0]["name"].strip())


class RecomputeTest(_Base):
    def _save(self, query, render="tile"):
        self._login()
        self.client.post(f"/analytics/kpis/save-crosstab?{query}",
                         data={"render": render})
        return self._saved()[0]

    def test_value_reflects_data_added_after_saving(self):
        self._pipe("K1", decision="Accept")
        kpi = self._save("xt_set=1&xt_rows=dn&xt_m=rej&xt_mset=1")
        self.assertEqual(analytics_routes._kpi_value(kpi), 0)

        self._pipe("K2", decision="Reject")
        self.assertEqual(analytics_routes._kpi_value(kpi), 1,
                         "a pinned view must recompute, not replay")

    def test_saved_filters_still_narrow_the_result(self):
        self._pipe("K1", shift=1, decision="Reject")
        self._pipe("K2", shift=2, decision="Reject")
        kpi = self._save("xt_set=1&xt_rows=dn&xt_m=rej&xt_mset=1&shift=1")
        self.assertEqual(analytics_routes._kpi_value(kpi), 1)

    def test_a_broken_selection_is_unavailable_not_an_exception(self):
        kpi = {"kind": "crosstab", "render": "tile",
               "selection": {"rows": ["dn"], "measure": "nope",
                             "filters": {"date_from": "not-a-date"}}}
        self.assertIsNone(analytics_routes._kpi_value(kpi))

    def test_table_kind_carries_its_whole_crosstab(self):
        self._pipe("K1")
        kpi = self._save("xt_set=1&xt_pivot=stage&xt_rows=dn", render="table")
        value, ct = analytics_routes._compute_crosstab_kpi(kpi)
        self.assertIsNotNone(ct)
        self.assertTrue(ct["rows"])


class RenderTest(_Base):
    def test_saved_tile_and_table_render_on_the_kpi_page(self):
        self._pipe("K1")
        self._login()
        self.client.post(
            "/analytics/kpis/save-crosstab?xt_set=1&xt_rows=dn&xt_m=rej&xt_mset=1",
            data={"render": "tile", "name": "مؤشري"})
        self.client.post(
            "/analytics/kpis/save-crosstab?xt_set=1&xt_pivot=stage&xt_rows=dn",
            data={"render": "table", "name": "جدولي"})
        html = self.client.get("/analytics/kpis").get_data(as_text=True)
        self.assertIn("مؤشري", html)
        self.assertIn("جدولي", html)
        self.assertIn("الجداول المحفوظة", html)

    def test_dashboard_page_renders_a_saved_crosstab_tile(self):
        self._pipe("K1")
        self._login()
        self.client.post(
            "/analytics/kpis/save-crosstab?xt_set=1&xt_rows=dn&xt_m=rej&xt_mset=1",
            data={"render": "tile", "name": "مؤشري"})
        resp = self.client.get("/analytics/dashboards")
        self.assertEqual(resp.status_code, 200)

    def test_save_buttons_appear_on_the_crosstab_panel(self):
        self._pipe("K1")
        self._login()
        html = self.client.get("/reports/bi-dashboard").get_data(as_text=True)
        self.assertIn("حفظ كـ مؤشر", html)
        self.assertIn("حفظ كـ جدول", html)
        self.assertIn("save-crosstab", html)


if __name__ == "__main__":
    unittest.main()


class ChartKindTest(_Base):
    def _save_chart(self, query="xt_set=1&xt_pivot=stage&xt_rows=dn&xt_chart_type=line"):
        self._login()
        self.client.post(f"/analytics/kpis/save-crosstab?{query}",
                         data={"render": "chart", "name": "رسمي"})
        return self._saved()[0]

    def test_chart_kind_is_stored_with_its_type(self):
        self._pipe("C1")
        kpi = self._save_chart()
        self.assertEqual(kpi["render"], "chart")
        self.assertEqual(kpi["selection"]["chart_type"], "line")

    def test_an_unknown_chart_type_falls_back_to_bar(self):
        self._pipe("C1")
        kpi = self._save_chart("xt_set=1&xt_rows=dn&xt_chart_type=nonsense")
        self.assertEqual(kpi["selection"]["chart_type"], "bar")

    def test_a_saved_chart_carries_its_crosstab(self):
        self._pipe("C1")
        kpi = self._save_chart()
        _value, ct = analytics_routes._compute_crosstab_kpi(kpi)
        self.assertTrue(ct["rows"])

    def test_saved_chart_renders_a_canvas(self):
        self._pipe("C1")
        self._save_chart()
        html = self.client.get("/analytics/kpis").get_data(as_text=True)
        self.assertIn("الرسوم المحفوظة", html)
        self.assertIn("رسمي", html)
        self.assertIn("data-chart", html)
        self.assertIn("chart.js", html)

    def test_save_as_chart_button_is_offered(self):
        self._pipe("C1")
        self._login()
        html = self.client.get("/reports/bi-dashboard").get_data(as_text=True)
        self.assertIn("حفظ كـ رسم", html)
        self.assertIn('name="xt_chart_type"', html)
