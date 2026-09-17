"""Weight pairs that cannot be right are set aside, counted, and named.

The arithmetic behind Saving was fixed on 2026-08-22, but the inputs are not
all sound: 14 of the 78 production pipes carrying both weights are linked to
the wrong product, and they move the whole-range saving from 0.9% to 25%. One
DN300 pipe carrying DN100's 144 kg standard reports -247% on its own.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.product import Product
from app.models.user import User
from app.services import analytics_service, bi_service


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

        u = User(username="admin", full_name="Admin", role="admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        # Two products: the correct DN300 standard, and DN100's, which is the
        # weight that actually appears mis-linked in production.
        self.p300 = Product(product_code="P300", weight_kg=200.0)
        self.p100 = Product(product_code="P100", weight_kg=144.0)
        db.session.add_all([self.p300, self.p100])
        db.session.commit()
        self.user_id = u.id
        self.day = date(2026, 8, 20)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, actual, product, diameter=300):
        pipe = Pipe(production_date=self.day, ladle_id="L", pipe_code=code,
                    no_code=code, arrange_pipe=1, diameter=diameter,
                    pipe_class="K9", iso_weight=0.0, actual_weight=actual,
                    product_id=product.id, lab_decision="ACCEPT")
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class GuardTest(_Base):
    def test_sane_pipes_are_usable(self):
        self._pipe("G1", 190.0, self.p300)
        usable, excluded = analytics_service.weight_pairs(Pipe.query.all())
        self.assertEqual(len(usable), 1)
        self.assertEqual(excluded, [])

    def test_wrong_product_link_is_caught_by_the_dn_median(self):
        for n in range(3):
            self._pipe(f"OK{n}", 190.0, self.p300)     # DN300 median = 200
        bad = self._pipe("BAD", 190.0, self.p100)      # DN300 carrying 144
        _usable, excluded = analytics_service.weight_pairs(Pipe.query.all())
        self.assertEqual([e["pipe"].no_code for e in excluded], ["BAD"])
        self.assertIn("المنتج المربوط", excluded[0]["reason"])
        self.assertEqual(bad.diameter, 300)

    def test_absurd_saving_is_caught_even_at_the_right_standard(self):
        self._pipe("G1", 190.0, self.p300)
        self._pipe("SEED", 19.0, self.p300)   # lorem-ipsum style record
        _usable, excluded = analytics_service.weight_pairs(Pipe.query.all())
        self.assertEqual([e["pipe"].no_code for e in excluded], ["SEED"])
        self.assertIn("%", excluded[0]["reason"])

    def test_negative_outlier_is_caught_too(self):
        self._pipe("G1", 190.0, self.p300)
        self._pipe("HEAVY", 500.0, self.p300)   # far heavier than standard
        _usable, excluded = analytics_service.weight_pairs(Pipe.query.all())
        self.assertEqual([e["pipe"].no_code for e in excluded], ["HEAVY"])

    def test_a_pipe_missing_either_weight_is_neither_usable_nor_excluded(self):
        pipe = self._pipe("G1", 190.0, self.p300)
        pipe.actual_weight = None
        db.session.commit()
        usable, excluded = analytics_service.weight_pairs(Pipe.query.all())
        self.assertEqual(usable, [])
        self.assertEqual(excluded, [],
                         "missing data is not the same as implausible data")

    def test_dn_medians_are_learned_per_diameter(self):
        self._pipe("A", 190.0, self.p300, diameter=300)
        self._pipe("B", 140.0, self.p100, diameter=100)
        medians = analytics_service.dn_standard_weights(Pipe.query.all())
        self.assertEqual(medians[300], 200.0)
        self.assertEqual(medians[100], 144.0)


class DailyReportTest(_Base):
    def test_excluded_pipes_do_not_move_the_saving_figure(self):
        for n in range(3):
            self._pipe(f"OK{n}", 190.0, self.p300)      # 5% saving each
        self._pipe("SEED", 19.0, self.p300)             # would read 90.5%
        stats = bi_service.daily_report(self.day)["today"]
        self.assertAlmostEqual(stats["sav_pct"], 5.0)
        self.assertEqual(stats["sav_n"], 3)
        self.assertEqual(len(stats["sav_excluded"]), 1)

    def test_excluded_pipes_are_named_with_a_reason(self):
        self._pipe("OK", 190.0, self.p300)
        self._pipe("SEED", 19.0, self.p300)
        excluded = bi_service.daily_report(self.day)["today"]["sav_excluded"]
        self.assertEqual(excluded[0]["pipe"], "SEED")
        self.assertEqual(excluded[0]["dn"], 300)
        self.assertTrue(excluded[0]["reason"])

    def test_the_exclusions_are_shown_on_the_page(self):
        self._pipe("OK", 190.0, self.p300)
        self._pipe("SEED", 19.0, self.p300)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.day.isoformat()}"
        ).get_data(as_text=True)
        self.assertIn("ماسورات مستبعدة من حساب التوفير", html)
        self.assertIn("SEED", html)
        self.assertIn("1 مستبعدة", html)

    def test_nothing_is_shown_when_every_pipe_is_sound(self):
        self._pipe("OK", 190.0, self.p300)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.day.isoformat()}"
        ).get_data(as_text=True)
        self.assertNotIn("ماسورات مستبعدة من حساب التوفير", html)


if __name__ == "__main__":
    unittest.main()
