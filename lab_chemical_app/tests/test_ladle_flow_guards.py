"""Ladle flow guards (DrAlaa 2026-08-10):

A. A ladle that already has a mechanical test must refuse NEW pipe registrations.
B. Pipe registration requires a ladle (no ladle-less pipes like P1112).
C. A mechanical test on an arrange=ANY (non-sample) pipe decides THAT pipe
   only — it must not cascade to the ladle (P1113 / X3 incidents). Only
   FIRST/LAST sample pipes cascade per the decision tree.
D. Blocked (تالف) pipes must not appear in the mechanical-test pipe picker,
   and the test form must refuse them if posted anyway.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.user import User
from app.services.pipe_decision_service import (
    assign_mechanical_roles,
    propagate_mechanical_result,
)


class LadleFlowGuardsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, no_code, ladle_id, arrange, **kw):
        # pipes.ladle_id is a foreign key to chemical_analyses.ladle_id, so a
        # pipe cannot exist before its ladle. Most tests here call _ladle()
        # first; a few did not, and only got away with it because SQLite does
        # not enforce foreign keys. Creating the ladle on demand keeps the
        # explicit _ladle() calls meaningful (they set the decision) while
        # making it impossible to build a pipe with no ladle behind it.
        if ladle_id and not ChemicalAnalysis.query.filter_by(
            ladle_id=ladle_id
        ).first():
            self._ladle(ladle_id)
        p = Pipe(
            production_date=date(2026, 8, 1), ladle_id=ladle_id,
            no_code=no_code, pipe_code=no_code, arrange_pipe=arrange,
            diameter=300, pipe_class="K9", **kw,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _ladle(self, ladle_id, decision="فحص أخيرة فقط"):
        a = ChemicalAnalysis(
            test_date=date(2026, 8, 1), ladle_no=1,
            ladle_id=ladle_id, decision=decision,
        )
        db.session.add(a)
        db.session.commit()
        return a

    def _test(self, pipe, decision):
        t = MechanicalTest(
            test_date=date(2026, 8, 10), ladle_id=pipe.ladle_id,
            pipe_id=pipe.id, pipe_code=pipe.pipe_code,
            decision=decision, status="ACTIVE",
        )
        db.session.add(t)
        db.session.commit()
        return t

    # --- Bug C: non-sample (ANY) pipe test decides itself only -------------

    def _last_only_ladle(self):
        self._ladle("L1", "فحص أخيرة فقط")
        p1 = self._pipe("N1", "L1", 1)
        p2 = self._pipe("N2", "L1", 2)
        p3 = self._pipe("N3", "L1", 3)
        assign_mechanical_roles("L1")
        db.session.refresh(p1)
        db.session.refresh(p2)
        db.session.refresh(p3)
        assert (p1.mechanical_test_role, p2.mechanical_test_role,
                p3.mechanical_test_role) == ("ANY", "ANY", "LAST")
        return p1, p2, p3

    def test_any_pipe_accept_decides_itself_only(self):
        p1, p2, p3 = self._last_only_ladle()
        propagate_mechanical_result(self._test(p2, "ACCEPT"))
        for p, want in ((p1, "WAITING"), (p2, "ACCEPT"), (p3, "WAITING")):
            db.session.refresh(p)
            self.assertEqual(p.lab_decision, want, p.no_code)

    def test_any_pipe_fail_holds_itself_only(self):
        # §4.2: mechanical FAIL is recoverable HOLD, never auto-scrap — and
        # for a non-sample pipe it must not touch the rest of the ladle.
        p1, p2, p3 = self._last_only_ladle()
        propagate_mechanical_result(self._test(p2, "REJECT"))
        for p, want in ((p1, "WAITING"), (p2, "HOLD"), (p3, "WAITING")):
            db.session.refresh(p)
            self.assertEqual(p.lab_decision, want, p.no_code)

    def test_last_sample_accept_still_cascades(self):
        p1, p2, p3 = self._last_only_ladle()
        propagate_mechanical_result(self._test(p3, "ACCEPT"))
        for p in (p1, p2, p3):
            db.session.refresh(p)
            self.assertEqual(p.lab_decision, "ACCEPT", p.no_code)

    def test_first_last_any_pipe_decides_itself_only(self):
        self._ladle("L2", "فحص أولى وأخيرة")
        q1 = self._pipe("M1", "L2", 1)
        q2 = self._pipe("M2", "L2", 2)
        q3 = self._pipe("M3", "L2", 3)
        assign_mechanical_roles("L2")
        propagate_mechanical_result(self._test(q2, "ACCEPT"))
        for p, want in ((q1, "WAITING"), (q2, "ACCEPT"), (q3, "WAITING")):
            db.session.refresh(p)
            self.assertEqual(p.lab_decision, want, p.no_code)

    # --- Bug B: ladle is mandatory on registration --------------------------

    def _post_add_pipe(self, **fields):
        base = {"no_code": "N0001", "production_date": "2026-08-10", "shift": "1"}
        base.update(fields)
        return self.client.post("/stages/add", data=base, follow_redirects=False)

    def test_register_pipe_without_ladle_is_refused(self):
        self._login()
        resp = self._post_add_pipe(ladle_id="")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(Pipe.query.filter_by(no_code="N0001").first())

    def test_register_pipe_with_ladle_is_accepted(self):
        self._login()
        self._ladle("L9", "فحص أخيرة فقط")
        resp = self._post_add_pipe(ladle_id="L9")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNotNone(Pipe.query.filter_by(no_code="N0001").first())

    # --- Bug A: ladle with a mechanical test refuses new pipes --------------

    def test_register_pipe_into_mechanically_tested_ladle_is_refused(self):
        self._login()
        self._ladle("L5", "فحص أخيرة فقط")
        p = self._pipe("OLD1", "L5", 1)
        self._test(p, "ACCEPT")
        resp = self._post_add_pipe(ladle_id="L5")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(Pipe.query.filter_by(no_code="N0001").first())

    # --- Bug D: blocked pipes flagged red, still refused on post -----------

    def test_pipes_search_flags_blocked_pipes(self):
        self._login()
        blocked = self._pipe("BLK1", "LB", 1, lab_decision="BLOCKED")
        rejected = self._pipe("REJ1", "LR", 1, final_decision_value="REJECT")
        ok = self._pipe("OKP1", "LO", 1)
        data = self.client.get("/mechanical/api/pipes-search").get_json()
        by_code = {p["code"]: p for p in data["pipes"]}
        self.assertIn(blocked.pipe_code, by_code)
        self.assertIn(rejected.pipe_code, by_code)
        self.assertIn(ok.pipe_code, by_code)
        # Blocked/rejected pipes carry the red-flag; normal pipes do not.
        self.assertTrue(by_code[blocked.pipe_code]["blocked"])
        self.assertTrue(by_code[rejected.pipe_code]["blocked"])
        self.assertFalse(by_code[ok.pipe_code]["blocked"])

    def test_mechanical_add_refuses_blocked_pipe(self):
        self._login()
        blocked = self._pipe("BLK2", "LB2", 1, lab_decision="BLOCKED")
        resp = self.client.post("/mechanical/add", data={
            "test_date": "2026-08-10", "pipe_id": str(blocked.id),
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MechanicalTest.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
