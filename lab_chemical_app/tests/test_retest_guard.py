"""Retest None-decision guard (§4.4): a fresh retest must never scrap the ladle."""

from datetime import date
import unittest

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe
from app.services.pipe_decision_service import (
    assign_mechanical_roles,
    propagate_mechanical_result,
)
from app.models.permission import seed_default_permissions


class RetestGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        f = Furnace(furnace_code="F1", furnace_name="F1")
        db.session.add(f)
        ca = ChemicalAnalysis(
            test_date=date(2026, 4, 14), furnace=f, ladle_no=1,
            ladle_id="R1", decision="فحص أخيرة فقط",
        )
        db.session.add(ca)
        db.session.commit()
        self.pipes = []
        for i in range(1, 5):
            p = Pipe(production_date=date(2026, 4, 14), ladle_id="R1",
                     pipe_code=f"R1-P{i}", no_code=f"R1N{i}", arrange_pipe=i,
                     diameter=300, pipe_class="K9")
            db.session.add(p)
            self.pipes.append(p)
        db.session.commit()
        assign_mechanical_roles("R1")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_none_decision_retest_no_scrap(self):
        last = self.pipes[-1]
        t = MechanicalTest(test_date=date(2026, 4, 16), ladle_id="R1",
                           pipe=last, pipe_code=last.pipe_code,
                           decision=None, status="ACTIVE")
        db.session.add(t)
        db.session.commit()
        propagate_mechanical_result(t)
        for p in self.pipes:
            db.session.refresh(p)
            self.assertNotEqual(p.final_decision_value, "REJECT")


if __name__ == "__main__":
    unittest.main()


class EditImmutabilityTestCase(unittest.TestCase):
    """§4.4: changing a recorded decision must preserve the prior result
    (new ACTIVE record + old SUPERSEDED), not overwrite in place."""

    def setUp(self):
        from app.models.user import User
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()
        u = User(username="sup", full_name="Sup", role="admin")
        u.set_password("x")
        db.session.add(u)
        f = Furnace(furnace_code="F1", furnace_name="F1")
        db.session.add(f)
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 4, 14), furnace=f, ladle_no=1,
            ladle_id="E1", decision="فحص أخيرة فقط"))
        db.session.commit()
        self.user_id = u.id
        t = MechanicalTest(test_date=date(2026, 4, 15), ladle_id="E1",
                           pipe_code="E1-P1", decision="ACCEPT",
                           status="ACTIVE", diameter=300)
        db.session.add(t)
        db.session.commit()
        self.test_id = t.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_decision_change_without_reason_aborts(self):
        self._login()
        self.client.post(f"/mechanical/{self.test_id}/edit",
                         data={"decision": "REJECT", "diameter": "300"})
        orig = db.session.get(MechanicalTest, self.test_id)
        db.session.refresh(orig)
        self.assertEqual(orig.decision, "ACCEPT", "original must be untouched")
        self.assertEqual(orig.status, "ACTIVE")
        self.assertEqual(MechanicalTest.query.count(), 1, "no new record created")

    def test_decision_change_with_reason_supersedes(self):
        self._login()
        self.client.post(f"/mechanical/{self.test_id}/edit",
                         data={"decision": "REJECT", "diameter": "300",
                               "reason": "Recalibrated tensile rig"})
        orig = db.session.get(MechanicalTest, self.test_id)
        db.session.refresh(orig)
        self.assertEqual(orig.status, "SUPERSEDED")
        self.assertEqual(orig.decision, "ACCEPT", "prior decision preserved")
        self.assertIsNotNone(orig.superseded_by_id)
        active = MechanicalTest.query.filter_by(status="ACTIVE").all()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].decision, "REJECT")
        self.assertEqual(active[0].original_test_id, self.test_id)
        self.assertEqual(orig.superseded_by_id, active[0].id)
