"""The daily report must survive a zero baseline.

dr_arrow divides by yesterday's value to get a day-over-day percentage. A day
following one with zero rejects made that a division by zero, which surfaced as
a 500 on the whole CCM dashboard rather than a missing arrow.
"""
import unittest
from datetime import date

from app import create_app, db
from app.models.pipe import Pipe
from app.models.user import User


class DailyReportZeroBaselineTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.user = User(username='zerobase', email='zerobase@test.local',
                         full_name='Zero Baseline', role='super_admin')
        self.user.set_password('x')
        db.session.add(self.user)

        # Yesterday: one pipe, accepted — zero rejects, the denominator that
        # used to blow up. Today: one pipe, rejected.
        db.session.add(Pipe(
            no_code='ZB1', production_date=date(2026, 3, 10), shift='1',
            diameter='300', pipe_class='K9', lab_decision='ACCEPT'))
        db.session.add(Pipe(
            no_code='ZB2', production_date=date(2026, 3, 11), shift='1',
            diameter='300', pipe_class='K9', lab_decision='REJECT'))
        db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user.id)
            sess['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_day_after_zero_rejects_renders(self):
        resp = self.client.get(
            '/reports/bi-dashboard?_panel=daily&rpt_date=2026-03-11')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Direction is still reported, just without an undefined percentage.
        self.assertIn('من صفر', html)

    def test_every_production_date_renders(self):
        for day in (date(2026, 3, 10), date(2026, 3, 11)):
            resp = self.client.get(
                '/reports/bi-dashboard?_panel=daily&rpt_date=%s' % day)
            self.assertEqual(resp.status_code, 200, 'failed on %s' % day)


if __name__ == '__main__':
    unittest.main()
