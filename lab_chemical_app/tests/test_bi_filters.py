"""The BI filter bar: inputs for filters that only worked by hand-typed URL.

parse_filters understood machine_id, mold_number, shift, customer and
approved_by, and apply_pipe_filters honoured most of them — but the dashboard
rendered no input for any of them, so they were unreachable. approved_by went
further: it was parsed, had a UI nowhere, and was applied by nothing except
approval_report.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import Machine
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import analytics_service


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

        self.alice = User(username="alice", full_name="Alice Approver",
                          role="admin", is_active=True)
        self.bob = User(username="bob", full_name="Bob Approver",
                        role="admin", is_active=True)
        for u in (self.alice, self.bob):
            u.set_password("x")
        self.machine = Machine(machine_code="M10", machine_name="M10")
        self.order = ProductionOrder(order_number="PO-1",
                                     customer_name="Acme Water",
                                     target_quantity=10)
        db.session.add_all([self.alice, self.bob, self.machine, self.order])
        db.session.commit()

        self.day = date(2026, 8, 20)
        self.ccm = ProductionStage.name_for_code("ccm")
        self.zinc = ProductionStage.name_for_code("zinc")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, **kw):
        pipe = Pipe(production_date=self.day, ladle_id="L", pipe_code=code,
                    no_code=code, arrange_pipe=1, diameter=kw.pop("dn", 300),
                    pipe_class=kw.pop("cls", "K9"), lab_decision="ACCEPT", **kw)
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.alice.id)
            sess["_fresh"] = True


class ApproverFilterTest(_Base):
    def test_filtering_by_approver_narrows_the_pipes(self):
        a = self._pipe("A1")
        b = self._pipe("B1")
        db.session.add(PipeStage(pipe_id=a.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.alice.id))
        db.session.add(PipeStage(pipe_id=b.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.bob.id))
        db.session.commit()
        q = analytics_service.apply_pipe_filters(
            Pipe.query, {"approved_by": self.alice.id})
        self.assertEqual([p.no_code for p in q.all()], ["A1"])

    def test_any_approved_stage_counts_not_just_the_first(self):
        p = self._pipe("A1")
        db.session.add(PipeStage(pipe_id=p.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.bob.id))
        db.session.add(PipeStage(pipe_id=p.id, stage_name=self.zinc,
                                 decision="Accept",
                                 approved_by_id=self.alice.id))
        db.session.commit()
        q = analytics_service.apply_pipe_filters(
            Pipe.query, {"approved_by": self.alice.id})
        self.assertEqual(len(q.all()), 1)

    def test_no_approver_filter_returns_everything(self):
        self._pipe("A1")
        self._pipe("B1")
        q = analytics_service.apply_pipe_filters(Pipe.query, {})
        self.assertEqual(len(q.all()), 2)


class OtherFilterTest(_Base):
    def test_customer_filter_goes_through_the_order(self):
        self._pipe("A1", production_order_id=self.order.id)
        self._pipe("B1")
        q = analytics_service.apply_pipe_filters(
            Pipe.query, {"customer": "Acme Water"})
        self.assertEqual([p.no_code for p in q.all()], ["A1"])

    def test_shift_engineer_filter(self):
        self._pipe("A1", shift_engineer="Sami")
        self._pipe("B1", shift_engineer="Omar")
        q = analytics_service.apply_pipe_filters(
            Pipe.query, {"shift_engineer": "Sami"})
        self.assertEqual([p.no_code for p in q.all()], ["A1"])

    def test_stage_filter_narrows_to_pipes_that_reached_it(self):
        a = self._pipe("A1")
        self._pipe("B1")
        db.session.add(PipeStage(pipe_id=a.id, stage_name=self.zinc))
        db.session.commit()
        q = analytics_service.apply_pipe_filters(Pipe.query,
                                                 {"stage": self.zinc})
        self.assertEqual([p.no_code for p in q.all()], ["A1"])

    def test_parse_filters_reads_the_new_keys(self):
        with self.app.test_request_context(
                "/?approved_by=7&shift_engineer=Sami&stage=Zinc"):
            from flask import request
            f = analytics_service.parse_filters(request.args)
        self.assertEqual(f["approved_by"], 7)
        self.assertEqual(f["shift_engineer"], "Sami")
        self.assertEqual(f["stage"], "Zinc")


class FilterBarRenderTest(_Base):
    def test_every_new_filter_has_an_input(self):
        p = self._pipe("A1", production_order_id=self.order.id,
                       shift_engineer="Sami", mold_number="MD-1", shift=2)
        db.session.add(PipeStage(pipe_id=p.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.alice.id))
        db.session.commit()
        self._login()
        html = self.client.get("/reports/bi-dashboard").get_data(as_text=True)
        for field in ("shift", "stage", "machine_id", "mold_number",
                      "customer", "shift_engineer", "approved_by"):
            self.assertIn('name="%s"' % field, html, "no input for %s" % field)

    def test_options_come_from_the_data(self):
        p = self._pipe("A1", production_order_id=self.order.id,
                       shift_engineer="Sami", mold_number="MD-1")
        db.session.add(PipeStage(pipe_id=p.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.alice.id))
        db.session.commit()
        self._login()
        html = self.client.get("/reports/bi-dashboard").get_data(as_text=True)
        self.assertIn("Alice Approver", html)
        self.assertIn("Acme Water", html)
        self.assertIn("Sami", html)
        self.assertIn("MD-1", html)
        self.assertIn("M10", html)
        # Bob approved nothing, so he is not offered as an approver.
        self.assertNotIn("Bob Approver", html)

    def test_an_active_filter_stays_selected(self):
        p = self._pipe("A1")
        db.session.add(PipeStage(pipe_id=p.id, stage_name=self.ccm,
                                 decision="Accept",
                                 approved_by_id=self.alice.id))
        db.session.commit()
        self._login()
        html = self.client.get(
            "/reports/bi-dashboard?approved_by=%s" % self.alice.id
        ).get_data(as_text=True)
        self.assertIn('value="%s" selected' % self.alice.id, html)


if __name__ == "__main__":
    unittest.main()
