"""
Truth-table tests for the lab-decision cascade engine (GCP_QC §4.2/§4.3).

State tokens (HTML decision spec v1.2):
  HOLD    = recoverable; delivery blocked. Used for LAST_ONLY fail and
            FIRST_LAST any-fail cascades.
  BLOCKED = terminal تالف chemical reject.
  REJECT  = terminal FULL_100 individual fail.
"""

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


class DecisionEngineTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.furnace = Furnace(furnace_code="F1", furnace_name="Furnace 1")
        db.session.add(self.furnace)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---- helpers -------------------------------------------------------
    def _ladle(self, ladle_id, decision):
        ca = ChemicalAnalysis(
            test_date=date(2026, 4, 14),
            furnace=self.furnace,
            ladle_no=int(ladle_id[-1]) if ladle_id[-1].isdigit() else 1,
            ladle_id=ladle_id,
            decision=decision,
        )
        db.session.add(ca)
        db.session.commit()
        return ca

    def _pipes(self, ladle_id, n):
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

    def _mech(self, ladle_id, pipe, decision):
        t = MechanicalTest(
            test_date=date(2026, 4, 15),
            ladle_id=ladle_id,
            pipe=pipe,
            pipe_code=pipe.pipe_code,
            decision=decision,
            status="ACTIVE",
        )
        db.session.add(t)
        db.session.commit()
        return t

    def _refresh(self, pipes):
        for p in pipes:
            db.session.refresh(p)

    # ---- LAST_ONLY -----------------------------------------------------
    def test_last_only_pass_all_accept(self):
        self._ladle("L1", "فحص أخيرة فقط")
        pipes = self._pipes("L1", 4)
        assign_mechanical_roles("L1")
        last = pipes[-1]
        self._mech("L1", last, "ACCEPT")
        propagate_mechanical_result(MechanicalTest.query.filter_by(ladle_id="L1").first())
        self._refresh(pipes)
        for p in pipes:
            self.assertEqual(p.lab_decision, "ACCEPT")
            self.assertNotEqual(p.final_decision_value, "REJECT")

    def test_last_only_fail_all_hold_not_reject(self):
        # HTML decision spec v1.2: Last-pipe-only FAIL cascades HOLD to the
        # whole ladle (recoverable; delivery blocked), never terminal REJECT.
        self._ladle("L2", "فحص أخيرة فقط")
        pipes = self._pipes("L2", 4)
        assign_mechanical_roles("L2")
        last = pipes[-1]
        t = self._mech("L2", last, "REJECT")
        propagate_mechanical_result(t)
        self._refresh(pipes)
        for p in pipes:
            self.assertEqual(p.lab_decision, "HOLD")
            self.assertNotEqual(
                p.final_decision_value, "REJECT",
                "LAST_ONLY fail must be recoverable HOLD, never terminal REJECT",
            )

    # ---- individually tested non-sample pipe keeps its own result ------
    def _lab_stage_decision(self, pipe):
        from app.models.stage import ProductionStage
        stage = pipe.get_stage(ProductionStage.name_for_code("lab"))
        return stage.decision if stage else None

    def test_last_only_sample_does_not_override_individual_test(self):
        self._ladle("L7", "فحص أخيرة فقط")
        pipes = self._pipes("L7", 4)
        assign_mechanical_roles("L7")
        other = pipes[1]
        propagate_mechanical_result(self._mech("L7", other, "REJECT"))
        propagate_mechanical_result(self._mech("L7", pipes[-1], "ACCEPT"))
        self._refresh(pipes)
        self.assertEqual(other.lab_decision, "HOLD")
        self.assertEqual(self._lab_stage_decision(other), "Hold")
        for p in pipes:
            if p is not other:
                self.assertEqual(p.lab_decision, "ACCEPT")

    def test_first_last_samples_do_not_override_individual_test(self):
        self._ladle("L8", "فحص أولى وأخيرة")
        pipes = self._pipes("L8", 4)
        assign_mechanical_roles("L8")
        other = pipes[1]
        propagate_mechanical_result(self._mech("L8", other, "REJECT"))
        self._mech("L8", pipes[0], "ACCEPT")
        propagate_mechanical_result(self._mech("L8", pipes[-1], "ACCEPT"))
        self._refresh(pipes)
        self.assertEqual(other.lab_decision, "HOLD")
        self.assertEqual(self._lab_stage_decision(other), "Hold")
        self.assertEqual(pipes[2].lab_decision, "ACCEPT")

    def test_blank_individual_test_still_receives_cascade(self):
        self._ladle("L10", "فحص أخيرة فقط")
        pipes = self._pipes("L10", 3)
        assign_mechanical_roles("L10")
        self._mech("L10", pipes[0], "")
        propagate_mechanical_result(self._mech("L10", pipes[-1], "ACCEPT"))
        self._refresh(pipes)
        self.assertEqual(pipes[0].lab_decision, "ACCEPT")

    def test_reapply_keeps_individual_test_whatever_the_order(self):
        from app.services.pipe_decision_service import reapply_mechanical_results
        self._ladle("L9", "فحص أخيرة فقط")
        pipes = self._pipes("L9", 4)
        assign_mechanical_roles("L9")
        other = pipes[1]
        # Sample recorded first, individual test second: replay order differs.
        propagate_mechanical_result(self._mech("L9", pipes[-1], "ACCEPT"))
        propagate_mechanical_result(self._mech("L9", other, "REJECT"))
        assign_mechanical_roles("L9")
        reapply_mechanical_results("L9")
        self._refresh(pipes)
        self.assertEqual(other.lab_decision, "HOLD")
        self.assertEqual(pipes[0].lab_decision, "ACCEPT")

    # ---- FIRST_LAST ----------------------------------------------------
    def _setup_first_last(self, ladle_id, first_dec, last_dec):
        self._ladle(ladle_id, "فحص أولى وأخيرة")
        pipes = self._pipes(ladle_id, 4)
        assign_mechanical_roles(ladle_id)
        first = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last = next(p for p in pipes if p.mechanical_test_role == "LAST")
        self._mech(ladle_id, first, first_dec)
        t2 = self._mech(ladle_id, last, last_dec)
        propagate_mechanical_result(t2)
        self._refresh(pipes)
        return pipes, first, last

    def test_first_last_both_pass_all_accept(self):
        pipes, _, _ = self._setup_first_last("L3", "ACCEPT", "ACCEPT")
        for p in pipes:
            self.assertEqual(p.lab_decision, "ACCEPT")
            self.assertNotEqual(p.final_decision_value, "REJECT")

    def test_first_last_both_fail_all_hold_not_reject(self):
        pipes, _, _ = self._setup_first_last("L4", "REJECT", "REJECT")
        for p in pipes:
            self.assertEqual(p.lab_decision, "HOLD")
            self.assertNotEqual(
                p.final_decision_value, "REJECT",
                "FIRST_LAST both-fail must be HOLD, never terminal REJECT",
            )

    def test_first_last_mixed_failed_sample_holds(self):
        pipes, first, last = self._setup_first_last("L5", "ACCEPT", "REJECT")
        self._refresh([first, last])
        self.assertEqual(first.lab_decision, "ACCEPT")   # passed sample
        self.assertEqual(last.lab_decision, "HOLD")       # failed sample -> HOLD
        for p in pipes:
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                self.assertEqual(p.lab_decision, "HOLD")
            self.assertNotEqual(p.final_decision_value, "REJECT")

    def test_first_last_partial_first_pass_shows_accept(self):
        # HTML engine: recording one sample shows ITS decision immediately;
        # the other sample + non-samples stay WAITING until both are in.
        self._ladle("L9", "فحص أولى وأخيرة")
        pipes = self._pipes("L9", 4)
        assign_mechanical_roles("L9")
        first = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last = next(p for p in pipes if p.mechanical_test_role == "LAST")
        t = self._mech("L9", first, "ACCEPT")
        propagate_mechanical_result(t)
        self._refresh(pipes)
        self.assertEqual(first.lab_decision, "ACCEPT")    # tested sample
        self.assertEqual(last.lab_decision, "WAITING")    # other sample pending
        for p in pipes:
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                self.assertEqual(p.lab_decision, "WAITING")

    def test_first_last_partial_first_fail_shows_hold(self):
        self._ladle("L10", "فحص أولى وأخيرة")
        pipes = self._pipes("L10", 4)
        assign_mechanical_roles("L10")
        first = next(p for p in pipes if p.mechanical_test_role == "FIRST")
        last = next(p for p in pipes if p.mechanical_test_role == "LAST")
        t = self._mech("L10", first, "REJECT")
        propagate_mechanical_result(t)
        self._refresh(pipes)
        self.assertEqual(first.lab_decision, "HOLD")      # tested sample failed
        self.assertEqual(last.lab_decision, "WAITING")    # other sample pending
        for p in pipes:
            if p.mechanical_test_role not in ("FIRST", "LAST"):
                self.assertEqual(p.lab_decision, "WAITING")
            self.assertNotEqual(p.final_decision_value, "REJECT")

    # ---- FULL_100 ------------------------------------------------------
    def test_full_100_individual_reject_is_terminal(self):
        self._ladle("L6", "فحص الشحنة 100%")
        pipes = self._pipes("L6", 3)
        assign_mechanical_roles("L6")
        target = pipes[1]
        t = self._mech("L6", target, "REJECT")
        propagate_mechanical_result(t)
        self._refresh(pipes)
        self.assertEqual(target.lab_decision, "REJECT")
        self.assertEqual(target.final_decision_value, "REJECT")

    # ---- تالف terminal -------------------------------------------------
    def test_chemical_reject_is_terminal_blocked(self):
        self._ladle("L7", "تالف")
        pipes = self._pipes("L7", 3)
        assign_mechanical_roles("L7")
        self._refresh(pipes)
        for p in pipes:
            self.assertEqual(p.lab_decision, "BLOCKED")
            self.assertEqual(p.final_decision_value, "REJECT")

    # ---- retest with None decision must NOT scrap ----------------------
    def test_retest_none_decision_does_not_scrap(self):
        self._ladle("L8", "فحص أخيرة فقط")
        pipes = self._pipes("L8", 4)
        assign_mechanical_roles("L8")
        last = pipes[-1]
        fresh_retest = MechanicalTest(
            test_date=date(2026, 4, 16),
            ladle_id="L8",
            pipe=last,
            pipe_code=last.pipe_code,
            decision=None,
            status="ACTIVE",
        )
        db.session.add(fresh_retest)
        db.session.commit()
        propagate_mechanical_result(fresh_retest)
        self._refresh(pipes)
        for p in pipes:
            self.assertNotEqual(
                p.final_decision_value, "REJECT",
                "A None-decision retest must never scrap the ladle",
            )


if __name__ == "__main__":
    unittest.main()
