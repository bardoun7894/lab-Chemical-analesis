"""
End-to-end integration tests for the lab decision engine, exercising the
full HTTP route chain:

  POST /chemical/add  →  assign_mechanical_roles  →  pipes.lab_decision
  POST /mechanical/add  →  propagate_mechanical_result  →  pipes.lab_decision

Four truth-table scenarios (canonical spec):
  1. تالف (REJECT)        → all pipes BLOCKED, final_decision_value == REJECT (terminal)
  2. فحص أخيرة فقط (LAST_ONLY)
       pass  → all ACCEPT
       fail  → all HOLD (recoverable, never terminal REJECT)
  3. فحص أولى وأخيرة (FIRST_LAST)
       both ACCEPT  → all ACCEPT
       both REJECT  → all HOLD
       mixed        → passed sample ACCEPT, failed sample HOLD, non-samples HOLD
       partial      → tested sample shows own decision, other sample+non-samples WAITING
  4. فحص الشحنة 100% (FULL_100) → individual: ACCEPT→ACCEPT, REJECT→REJECT, untested→WAITING
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe
from app.models.user import User
from app.models.permission import seed_default_permissions


# ---------------------------------------------------------------------------
# Helper — ladle_id format matches chemical.add:
#   f"{ladle_no}{day:02d}{month:02d}{year}"
# ---------------------------------------------------------------------------
def _ladle_id(ladle_no, d=date(2026, 4, 14)):
    return f"{ladle_no}{d.day:02d}{d.month:02d}{d.year}"


class E2EDecisionFlowTestCase(unittest.TestCase):
    """Integration tests: routes are exercised via real HTTP POST."""

    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        # Furnace required by /chemical/add
        self.furnace = Furnace(furnace_code="F1", furnace_name="Furnace 1")
        db.session.add(self.furnace)

        # Admin user — passes is_admin short-circuit and can_edit
        u = User(username="admin", full_name="Admin", role="admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id
        self.furnace_id = self.furnace.id

        self.client = self.app.test_client()
        self._login()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # -----------------------------------------------------------------------
    # Login helper (session-cookie approach from test_stage_gating)
    # -----------------------------------------------------------------------
    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    # -----------------------------------------------------------------------
    # Pre-create pipes for a ladle so assign_mechanical_roles finds them.
    # Pipes are created BEFORE POSTing chemical/add so the route's own call
    # to assign_mechanical_roles operates on real data.
    # SQLite does not enforce FKs by default, so ladle_id FK is safe.
    # -----------------------------------------------------------------------
    def _create_pipes(self, ladle_id, n):
        pipes = []
        for i in range(1, n + 1):
            p = Pipe(
                production_date=date(2026, 4, 14),
                ladle_id=ladle_id,
                pipe_code=f"{ladle_id}-P{i}",
                no_code=f"{ladle_id}N{i}",
                arrange_pipe=i,
                diameter=300,
                pipe_class="K9",
            )
            db.session.add(p)
            pipes.append(p)
        db.session.commit()
        return pipes

    # -----------------------------------------------------------------------
    # POST /chemical/add with the given Arabic decision string.
    # Returns the created ChemicalAnalysis.
    # -----------------------------------------------------------------------
    def _post_chemical_add(self, ladle_no, decision, test_date=None):
        d = test_date or date(2026, 4, 14)
        resp = self.client.post(
            "/chemical/add",
            data={
                "test_date": str(d),
                "furnace_id": str(self.furnace_id),
                "ladle_no": str(ladle_no),
                "decision": decision,
                "carbon": "3.6",
                "silicon": "2.2",
                "magnesium": "0.045",
            },
            follow_redirects=False,
        )
        self.assertEqual(
            resp.status_code, 302,
            f"/chemical/add returned {resp.status_code} — form validation "
            f"failed, not a redirect: {resp.get_data(as_text=True)[:400]}"
        )
        expected_ladle_id = _ladle_id(ladle_no, d)
        ca = ChemicalAnalysis.query.filter_by(ladle_id=expected_ladle_id).first()
        self.assertIsNotNone(ca, f"ChemicalAnalysis with ladle_id={expected_ladle_id} not found after POST")
        return ca

    # -----------------------------------------------------------------------
    # POST /mechanical/add for a specific pipe.
    # Returns the created MechanicalTest.
    # -----------------------------------------------------------------------
    def _post_mechanical_add(self, pipe, decision):
        resp = self.client.post(
            "/mechanical/add",
            data={
                "test_date": "2026-04-15",
                "ladle_id": pipe.ladle_id,
                "pipe_id": str(pipe.id),
                "decision": decision,
                "diameter": str(pipe.diameter),
                "test_number": "1",
                "shift": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(
            resp.status_code, 302,
            f"/mechanical/add returned {resp.status_code} — likely form error: "
            f"{resp.get_data(as_text=True)[:400]}"
        )
        mt = (
            MechanicalTest.query
            .filter_by(ladle_id=pipe.ladle_id, pipe_id=pipe.id)
            .order_by(MechanicalTest.id.desc())
            .first()
        )
        self.assertIsNotNone(mt, "MechanicalTest not found after POST")
        return mt

    def _refresh_pipes(self, pipes):
        for p in pipes:
            db.session.refresh(p)

    # =======================================================================
    # Scenario 1 — تالف (REJECT / BLOCKED)
    # =======================================================================
    def test_scenario_talef_all_blocked_terminal(self):
        """تالف: every pipe → BLOCKED + final_decision_value == REJECT (no mechanical test)."""
        ladle_no = 1
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 4)

        # POST chemical/add — route calls assign_mechanical_roles immediately
        self._post_chemical_add(ladle_no, "تالف")

        self._refresh_pipes(pipes)
        for i, pipe in enumerate(pipes):
            self.assertEqual(
                pipe.lab_decision, "BLOCKED",
                f"Pipe {i+1}: expected BLOCKED, got {pipe.lab_decision}"
            )
            self.assertEqual(
                pipe.final_decision_value, "REJECT",
                f"Pipe {i+1}: expected final_decision_value==REJECT, got {pipe.final_decision_value}"
            )

    # =======================================================================
    # Scenario 2a — LAST_ONLY pass: all → ACCEPT
    # =======================================================================
    def test_scenario_last_only_pass_all_accept(self):
        """فحص أخيرة فقط + last pipe ACCEPT → all pipes ACCEPT."""
        ladle_no = 2
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 4)

        self._post_chemical_add(ladle_no, "فحص أخيرة فقط")
        self._refresh_pipes(pipes)

        # All should be WAITING after assign
        for p in pipes:
            self.assertEqual(p.lab_decision, "WAITING",
                             f"Pre-mech: pipe arrange={p.arrange_pipe} not WAITING: {p.lab_decision}")

        # Identify the LAST sample pipe
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        # POST mechanical test on last pipe → ACCEPT
        self._post_mechanical_add(last_pipe, "ACCEPT")
        self._refresh_pipes(pipes)

        for i, pipe in enumerate(pipes):
            self.assertEqual(
                pipe.lab_decision, "ACCEPT",
                f"Pipe {i+1}: expected ACCEPT, got {pipe.lab_decision}"
            )
            self.assertNotEqual(
                pipe.final_decision_value, "REJECT",
                f"Pipe {i+1}: final_decision_value must not be REJECT"
            )

    # =======================================================================
    # Scenario 2b — LAST_ONLY fail: all → HOLD (recoverable, never REJECT)
    # =======================================================================
    def test_scenario_last_only_fail_all_hold_not_reject(self):
        """فحص أخيرة فقط + last pipe REJECT → all pipes HOLD (recoverable, not terminal)."""
        ladle_no = 3
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 4)

        self._post_chemical_add(ladle_no, "فحص أخيرة فقط")
        self._refresh_pipes(pipes)

        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")
        self._post_mechanical_add(last_pipe, "REJECT")
        self._refresh_pipes(pipes)

        for i, pipe in enumerate(pipes):
            self.assertEqual(
                pipe.lab_decision, "HOLD",
                f"Pipe {i+1}: expected HOLD, got {pipe.lab_decision}"
            )
            self.assertNotEqual(
                pipe.final_decision_value, "REJECT",
                f"Pipe {i+1}: LAST_ONLY fail must be recoverable HOLD, not terminal REJECT"
            )

    # =======================================================================
    # Scenario 3a — FIRST_LAST both ACCEPT → all ACCEPT
    # =======================================================================
    def test_scenario_first_last_both_accept(self):
        """فحص أولى وأخيرة: FIRST=ACCEPT, LAST=ACCEPT → all pipes ACCEPT."""
        ladle_no = 4
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 5)

        self._post_chemical_add(ladle_no, "فحص أولى وأخيرة")
        self._refresh_pipes(pipes)

        first_pipe = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        self._post_mechanical_add(first_pipe, "ACCEPT")
        self._post_mechanical_add(last_pipe, "ACCEPT")
        self._refresh_pipes(pipes)

        for i, pipe in enumerate(pipes):
            self.assertEqual(
                pipe.lab_decision, "ACCEPT",
                f"Pipe {i+1} (role={pipe.mechanical_test_role}): expected ACCEPT, got {pipe.lab_decision}"
            )
            self.assertNotEqual(
                pipe.final_decision_value, "REJECT",
                f"Pipe {i+1}: final_decision_value must not be REJECT"
            )

    # =======================================================================
    # Scenario 3b — FIRST_LAST both REJECT → all HOLD (recoverable)
    # =======================================================================
    def test_scenario_first_last_both_reject_all_hold(self):
        """فحص أولى وأخيرة: FIRST=REJECT, LAST=REJECT → all pipes HOLD (not terminal REJECT)."""
        ladle_no = 5
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 5)

        self._post_chemical_add(ladle_no, "فحص أولى وأخيرة")
        self._refresh_pipes(pipes)

        first_pipe = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        self._post_mechanical_add(first_pipe, "REJECT")
        self._post_mechanical_add(last_pipe, "REJECT")
        self._refresh_pipes(pipes)

        for i, pipe in enumerate(pipes):
            self.assertEqual(
                pipe.lab_decision, "HOLD",
                f"Pipe {i+1} (role={pipe.mechanical_test_role}): expected HOLD, got {pipe.lab_decision}"
            )
            self.assertNotEqual(
                pipe.final_decision_value, "REJECT",
                f"Pipe {i+1}: FIRST_LAST both-fail must be recoverable HOLD, never terminal REJECT"
            )

    # =======================================================================
    # Scenario 3c — FIRST_LAST mixed (FIRST=ACCEPT, LAST=REJECT)
    # =======================================================================
    def test_scenario_first_last_mixed(self):
        """فحص أولى وأخيرة: FIRST=ACCEPT, LAST=REJECT → FIRST pipe ACCEPT, rest HOLD."""
        ladle_no = 6
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 5)

        self._post_chemical_add(ladle_no, "فحص أولى وأخيرة")
        self._refresh_pipes(pipes)

        first_pipe = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        self._post_mechanical_add(first_pipe, "ACCEPT")
        self._post_mechanical_add(last_pipe, "REJECT")
        self._refresh_pipes(pipes)

        # First sample: ACCEPT (passed)
        db.session.refresh(first_pipe)
        self.assertEqual(
            first_pipe.lab_decision, "ACCEPT",
            f"FIRST pipe: expected ACCEPT, got {first_pipe.lab_decision}"
        )
        # Last sample: HOLD (failed)
        db.session.refresh(last_pipe)
        self.assertEqual(
            last_pipe.lab_decision, "HOLD",
            f"LAST pipe: expected HOLD, got {last_pipe.lab_decision}"
        )
        # Non-sample pipes: HOLD (cascaded failure)
        for p in pipes:
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                db.session.refresh(p)
                self.assertEqual(
                    p.lab_decision, "HOLD",
                    f"Non-sample pipe arrange={p.arrange_pipe}: expected HOLD, got {p.lab_decision}"
                )
            self.assertNotEqual(
                p.final_decision_value, "REJECT",
                f"Pipe arrange={p.arrange_pipe}: final_decision_value must not be REJECT"
            )

    # =======================================================================
    # Scenario 3d — FIRST_LAST partial: only FIRST recorded
    # =======================================================================
    def test_scenario_first_last_partial_first_only_accept(self):
        """فحص أولى وأخيرة: only FIRST recorded (ACCEPT) → FIRST=ACCEPT, others=WAITING."""
        ladle_no = 7
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 5)

        self._post_chemical_add(ladle_no, "فحص أولى وأخيرة")
        self._refresh_pipes(pipes)

        first_pipe = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        # Only record the first pipe's test
        self._post_mechanical_add(first_pipe, "ACCEPT")
        self._refresh_pipes(pipes)

        db.session.refresh(first_pipe)
        db.session.refresh(last_pipe)

        self.assertEqual(
            first_pipe.lab_decision, "ACCEPT",
            f"FIRST (tested) should be ACCEPT, got {first_pipe.lab_decision}"
        )
        self.assertEqual(
            last_pipe.lab_decision, "WAITING",
            f"LAST (untested) should be WAITING, got {last_pipe.lab_decision}"
        )
        for p in pipes:
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                db.session.refresh(p)
                self.assertEqual(
                    p.lab_decision, "WAITING",
                    f"Non-sample pipe arrange={p.arrange_pipe}: expected WAITING, got {p.lab_decision}"
                )

    def test_scenario_first_last_partial_first_only_reject(self):
        """فحص أولى وأخيرة: only FIRST recorded (REJECT) → FIRST=HOLD, others=WAITING."""
        ladle_no = 8
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 5)

        self._post_chemical_add(ladle_no, "فحص أولى وأخيرة")
        self._refresh_pipes(pipes)

        first_pipe = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last_pipe = next(p for p in pipes if p.mechanical_test_role == "LAST")

        self._post_mechanical_add(first_pipe, "REJECT")
        self._refresh_pipes(pipes)

        db.session.refresh(first_pipe)
        db.session.refresh(last_pipe)

        self.assertEqual(
            first_pipe.lab_decision, "HOLD",
            f"FIRST (failed) should be HOLD, got {first_pipe.lab_decision}"
        )
        self.assertEqual(
            last_pipe.lab_decision, "WAITING",
            f"LAST (untested) should be WAITING, got {last_pipe.lab_decision}"
        )
        for p in pipes:
            db.session.refresh(p)
            self.assertNotEqual(
                p.final_decision_value, "REJECT",
                f"Pipe arrange={p.arrange_pipe}: partial FIRST_LAST must never be terminal REJECT"
            )
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                self.assertEqual(
                    p.lab_decision, "WAITING",
                    f"Non-sample arrange={p.arrange_pipe}: expected WAITING, got {p.lab_decision}"
                )

    # =======================================================================
    # Scenario 4 — FULL_100 individual decisions
    # =======================================================================
    def test_scenario_full_100_individual_decisions(self):
        """فحص الشحنة 100%: ACCEPT→ACCEPT, REJECT→REJECT (terminal), untested→WAITING."""
        ladle_no = 9
        ladle_id = _ladle_id(ladle_no)
        pipes = self._create_pipes(ladle_id, 4)

        self._post_chemical_add(ladle_no, "فحص الشحنة 100%")
        self._refresh_pipes(pipes)

        # All should be WAITING + role ALL initially
        for p in pipes:
            self.assertEqual(p.lab_decision, "WAITING",
                             f"Pre-mech: arrange={p.arrange_pipe} not WAITING: {p.lab_decision}")
            self.assertEqual(p.mechanical_test_role, "ALL",
                             f"Pre-mech: arrange={p.arrange_pipe} role not ALL: {p.mechanical_test_role}")

        accept_pipe = pipes[0]
        reject_pipe = pipes[1]
        # pipes[2] and pipes[3] left untested

        # POST ACCEPT for first pipe
        self._post_mechanical_add(accept_pipe, "ACCEPT")
        # POST REJECT for second pipe
        self._post_mechanical_add(reject_pipe, "REJECT")

        self._refresh_pipes(pipes)

        # Tested-accept pipe
        db.session.refresh(accept_pipe)
        self.assertEqual(accept_pipe.lab_decision, "ACCEPT",
                         f"Accepted pipe: expected ACCEPT, got {accept_pipe.lab_decision}")
        self.assertNotEqual(accept_pipe.final_decision_value, "REJECT")

        # Tested-reject pipe → terminal REJECT
        db.session.refresh(reject_pipe)
        self.assertEqual(reject_pipe.lab_decision, "REJECT",
                         f"Rejected pipe: expected REJECT, got {reject_pipe.lab_decision}")
        self.assertEqual(reject_pipe.final_decision_value, "REJECT",
                         f"Rejected pipe: final_decision_value must be REJECT (terminal)")

        # Untested pipes → still WAITING
        for p in pipes[2:]:
            db.session.refresh(p)
            self.assertEqual(p.lab_decision, "WAITING",
                             f"Untested pipe arrange={p.arrange_pipe}: expected WAITING, got {p.lab_decision}")


if __name__ == "__main__":
    unittest.main()
