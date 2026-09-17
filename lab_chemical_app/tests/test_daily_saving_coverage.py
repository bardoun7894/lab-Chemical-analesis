"""Daily report saving is computed from a weight that actually exists.

`_daily_stats` read `Pipe.iso_weight` directly. That column is 0 for every pipe
in production — there is no input for it on the pipe form, and both save paths
write `float(request.form.get("iso_weight") or 0)`, so even a backfill is wiped
on the next edit. The result: Saving pinned at 0.00%, tonnage negative, and the
negative-saving alert that watches it could never fire.

Saving now goes through `analytics_service.planned_weight()`, which falls back
to the product's standard weight and reports which source it used, and the
report discloses how many pipes the figure actually covers.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.product import Product
from app.models.user import User
from app.services import bi_service


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
        db.session.commit()
        self.user_id = u.id

        self.product = Product(product_code="P300K9", weight_kg=100.0)
        db.session.add(self.product)
        db.session.commit()
        self.day = date(2026, 8, 20)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, actual=None, with_product=True, decision="ACCEPT"):
        pipe = Pipe(
            production_date=self.day, ladle_id="SAV", pipe_code=code,
            no_code=code, arrange_pipe=1, diameter=300, pipe_class="K9",
            iso_weight=0.0,               # production reality
            actual_weight=actual,
            product_id=self.product.id if with_product else None,
            lab_decision=decision,
        )
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True


class SavingTest(_Base):
    def test_saving_is_real_when_iso_weight_is_zero(self):
        self._pipe("S1", actual=95.0)
        self._pipe("S2", actual=97.0)
        stats = bi_service.daily_report(self.day)["today"]
        # planned 200, actual 192 -> 4%
        self.assertAlmostEqual(stats["sav_pct"], 4.0)
        self.assertAlmostEqual(stats["sav_kg"], 8.0)

    def test_coverage_is_reported(self):
        self._pipe("S1", actual=95.0)
        self._pipe("S2", actual=None)              # no actual weight
        self._pipe("S3", actual=95.0, with_product=False)  # no standard weight
        stats = bi_service.daily_report(self.day)["today"]
        self.assertEqual(stats["sav_of"], 3)
        # Only S1 has BOTH weights. S2 has a standard but no actual, which used
        # to be counted with actual=0 and so read as 100% saving; S3 has an
        # actual but no standard to compare it against.
        self.assertEqual(stats["sav_n"], 1)

    def test_pipe_without_any_weight_source_does_not_poison_the_ratio(self):
        self._pipe("S1", actual=95.0)
        self._pipe("S2", actual=95.0, with_product=False)
        stats = bi_service.daily_report(self.day)["today"]
        # Only S1 counts: planned 100, actual 95 -> 5%, not a negative number
        self.assertAlmostEqual(stats["sav_pct"], 5.0)
        self.assertGreater(stats["sav_kg"], 0)

    def test_no_weighed_pipes_reports_zero_coverage_not_a_fake_zero(self):
        self._pipe("S1", actual=95.0, with_product=False)
        stats = bi_service.daily_report(self.day)["today"]
        self.assertEqual(stats["sav_n"], 0)
        self.assertEqual(stats["sav_pct"], 0.0)

    def test_iso_weight_is_preferred_when_it_is_ever_populated(self):
        # 110 rather than the product's 100, so the source is distinguishable —
        # and within the plausibility bound, since a 50%+ gap would (rightly)
        # be set aside as a data error whatever its source.
        pipe = self._pipe("S1", actual=95.0)
        pipe.iso_weight = 110.0
        db.session.commit()
        stats = bi_service.daily_report(self.day)["today"]
        self.assertAlmostEqual(stats["sav_pct"], 100 * 15 / 110)  # (110-95)/110

    def test_negative_saving_alert_can_now_fire(self):
        # Actual heavier than standard — the alert at bi_service:1116 was
        # unreachable while sav_pct was pinned at exactly 0.0.
        self._pipe("S1", actual=110.0)
        report = bi_service.daily_report(self.day)
        self.assertLess(report["today"]["sav_pct"], 0)
        self.assertTrue(any("Saving" in a for a in report["alerts"]),
                        report["alerts"])


class RenderTest(_Base):
    def test_kpi_shows_the_coverage(self):
        self._pipe("S1", actual=95.0)
        self._pipe("S2", actual=95.0, with_product=False)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.day.isoformat()}"
        ).get_data(as_text=True)
        self.assertIn("محسوب من 1 من 2 ماسورة", html)

    def test_no_coverage_says_so_instead_of_showing_zero_percent(self):
        self._pipe("S1", actual=95.0, with_product=False)
        self._login()
        html = self.client.get(
            f"/reports/bi-dashboard?rpt_date={self.day.isoformat()}"
        ).get_data(as_text=True)
        self.assertIn("لا توجد ماسورة لها وزن معياري وفعلي معاً", html)


if __name__ == "__main__":
    unittest.main()
