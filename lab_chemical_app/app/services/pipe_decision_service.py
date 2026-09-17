"""
Pipe Decision Service - Core decision engine for pipe quality control.

Handles:
- Mechanical test role assignment based on chemical decision
- Mechanical result propagation to sibling pipes
- Final decision calculation combining lab + stage decisions
"""

from app import db
from app.models.pipe import Pipe, PipeStage


# Chemical decision → mechanical test scenario mapping
DECISION_SCENARIO = {
    "فحص أخيرة فقط": "LAST_ONLY",
    "Inspect Last pipes": "LAST_ONLY",
    "فحص أولى وأخيرة": "FIRST_LAST",
    "Inspect 1st and Last pipes": "FIRST_LAST",
    "فحص الشحنة 100%": "FULL_100",
    "Inspect 100%": "FULL_100",
    "تالف": "REJECT",
    "Reject": "REJECT",
}


def assign_mechanical_roles(ladle_id):
    """
    After chemical decision is saved, assign mechanical test roles to pipes.

    Scenarios:
    - LAST_ONLY: last pipe = LAST, others = ANY + lab_decision=WAITING
    - FIRST_LAST: first = FIRST, last = LAST, others = ANY + WAITING
    - FULL_100: all = ALL + WAITING
    - REJECT: all BLOCKED, final_decision = REJECT
    """
    from app.models.chemical import ChemicalAnalysis

    analysis = ChemicalAnalysis.query.filter_by(ladle_id=ladle_id).first()
    if not analysis or not analysis.decision:
        return

    scenario = DECISION_SCENARIO.get(analysis.decision)
    if not scenario:
        return

    pipes = (
        Pipe.query.filter_by(ladle_id=ladle_id).order_by(Pipe.arrange_pipe.asc()).all()
    )

    if not pipes:
        return

    if scenario == "REJECT":
        for pipe in pipes:
            pipe.mechanical_test_role = None
            pipe.lab_decision = "BLOCKED"
            pipe.lab_decision_reason = (
                f"Chemical analysis rejected: {analysis.reason or ''}"
            )
            pipe.cascade_from = "Ladle Rejected"
            pipe.final_decision_value = "REJECT"
            pipe.final_decision_reason = "Chemical analysis rejected"
        db.session.commit()
        return

    if scenario == "LAST_ONLY":
        for i, pipe in enumerate(pipes):
            if i == len(pipes) - 1:
                pipe.mechanical_test_role = "LAST"
                pipe.lab_decision = "WAITING"
            else:
                pipe.mechanical_test_role = "ANY"
                pipe.lab_decision = "WAITING"
            pipe.cascade_from = None

    elif scenario == "FIRST_LAST":
        for i, pipe in enumerate(pipes):
            if i == 0:
                pipe.mechanical_test_role = "FIRST"
            elif i == len(pipes) - 1:
                pipe.mechanical_test_role = "LAST"
            else:
                pipe.mechanical_test_role = "ANY"
            pipe.lab_decision = "WAITING"
            pipe.cascade_from = None

    elif scenario == "FULL_100":
        for pipe in pipes:
            pipe.mechanical_test_role = "ALL"
            pipe.lab_decision = "WAITING"
            pipe.cascade_from = None

    db.session.commit()

    # Auto-create the next production stage (CCM) for all pipes in the ladle
    # so they show up in the stages list with the active stage indicator
    # instead of all "-" dashes. Only runs for non-REJECT scenarios.
    # We look up the current display name by code so renaming the stage
    # in the admin UI does not break this auto-create.
    from app.models.stage import ProductionStage
    _ensure_next_stage_for_ladle(pipes, ProductionStage.name_for_code("ccm"))


def _ensure_next_stage_for_ladle(pipes, stage_name):
    """Create a PipeStage for ``stage_name`` on each pipe that doesn't have one yet.

    Idempotent — skips pipes that already have a record for the given stage.
    This is what makes the stages list show the active stage icon (arrow)
    instead of all dashes after the chemical decision is saved.
    """
    from datetime import date as date_type

    for pipe in pipes:
        existing = next(
            (s for s in pipe.stages if s.stage_name == stage_name), None
        )
        if existing is not None:
            continue
        db.session.add(
            PipeStage(
                pipe_id=pipe.id,
                stage_name=stage_name,
                stage_date=date_type.today(),
                decision=None,
            )
        )
    db.session.commit()


def propagate_mechanical_result(mechanical_test):
    """
    After mechanical test is saved, propagate result to sibling pipes.

    Logic depends on scenario:
    - LAST_ONLY: PASS → all ACCEPT, FAIL → all REJECT+BLOCK
    - FIRST_LAST: Both PASS → all ACCEPT, Both FAIL → all REJECT,
                  Mixed → passed=ACCEPT, failed=REJECT, others=HOLD
    - FULL_100: individual result only
    """
    ladle_id = mechanical_test.ladle_id
    if not ladle_id:
        return

    # Guard: a fresh retest has decision=None. Without this, None falls into
    # the FAIL path below and scraps the whole ladle. A None decision means
    # "not yet evaluated" — nothing to propagate.
    if not mechanical_test.decision:
        return

    pipes = (
        Pipe.query.filter_by(ladle_id=ladle_id).order_by(Pipe.arrange_pipe.asc()).all()
    )

    if not pipes:
        return

    # Determine scenario from first pipe's role
    roles = {p.mechanical_test_role for p in pipes if p.mechanical_test_role}
    test_decision = mechanical_test.decision  # ACCEPT or REJECT

    # Which pipe was actually tested? Its role decides whether the result
    # cascades. A test on an ANY (non-sample) pipe must NEVER cascade —
    # it decides that pipe alone (DrAlaa 2026-08-10: testing P1113/X3,
    # both ANY pipes, previously applied the sample cascade to the whole
    # ladle as if the representative pipe had been tested).
    tested_pipe = None
    if mechanical_test.pipe_id:
        tested_pipe = Pipe.query.get(mechanical_test.pipe_id)
    if not tested_pipe and mechanical_test.pipe_code:
        tested_pipe = Pipe.query.filter_by(
            ladle_id=ladle_id, pipe_code=mechanical_test.pipe_code
        ).first()

    # Non-sample pipes with their own recorded test keep that result: a later
    # ladle-sample cascade must not overwrite it, or the Lab Approval stage
    # disagrees with the pipe's own mechanical test.
    individually_tested = _individually_tested_pipe_ids(ladle_id, pipes)

    if "ALL" in roles:
        # FULL_100: only update the specific pipe
        pipe = tested_pipe
        if pipe:
            pipe.lab_decision = "ACCEPT" if test_decision == "ACCEPT" else "REJECT"
            pipe.lab_decision_reason = f"Mechanical test: {test_decision}"
            pipe.cascade_from = "individual"

    elif tested_pipe is not None and tested_pipe.mechanical_test_role == "ANY":
        # Non-sample pipe in a LAST_ONLY / FIRST_LAST ladle: individual
        # result only. Fail is recoverable HOLD (§4.2 — mechanical fail
        # never auto-scraps), pass is ACCEPT, siblings untouched.
        tested_pipe.lab_decision = "ACCEPT" if test_decision == "ACCEPT" else "HOLD"
        tested_pipe.lab_decision_reason = (
            f"Individual mechanical test (non-sample pipe): {test_decision}"
        )
        tested_pipe.cascade_from = "individual"
        db.session.commit()
        update_lab_stage_decision(tested_pipe)
        update_final_decision(tested_pipe)
        return

    elif "LAST" in roles and "FIRST" not in roles:
        # LAST_ONLY
        last_pipe = next((p for p in pipes if p.mechanical_test_role == "LAST"), None)
        source = last_pipe.no_code if last_pipe else "?"
        if test_decision == "ACCEPT":
            for pipe in pipes:
                if pipe.id in individually_tested:
                    continue
                pipe.lab_decision = "ACCEPT"
                pipe.lab_decision_reason = "Last pipe mechanical test passed"
                pipe.cascade_from = (
                    f"sample: {source}"
                    if pipe.mechanical_test_role == "LAST"
                    else f"cascade from {source}"
                )
        else:
            for pipe in pipes:
                if pipe.id in individually_tested:
                    continue
                # HTML decision spec v1.2: Last-pipe-only FAIL cascades HOLD to
                # the whole ladle — recoverable (delivery blocked), NOT a
                # terminal scrap. Liftable later via retest (§4.4).
                pipe.lab_decision = "HOLD"
                pipe.lab_decision_reason = (
                    "Last pipe mechanical test failed - cascade HOLD to ladle"
                )
                pipe.cascade_from = (
                    f"sample: {source}"
                    if pipe.mechanical_test_role == "LAST"
                    else f"cascade from {source}"
                )

    elif "FIRST" in roles and "LAST" in roles:
        # FIRST_LAST — mirror the HTML engine exactly: recompute EVERY pipe from
        # the current test state on each call, so a single recorded sample shows
        # its own decision immediately while the rest stay WAITING until both in.
        from app.models.mechanical import MechanicalTest as MT

        tests = MT.query.filter_by(ladle_id=ladle_id, status="ACTIVE").all()

        first_pipe = next((p for p in pipes if p.mechanical_test_role == "FIRST"), None)
        last_pipe = next((p for p in pipes if p.mechanical_test_role == "LAST"), None)

        first_test = None
        last_test = None
        for t in tests:
            if first_pipe and (
                t.pipe_id == first_pipe.id or t.pipe_code == first_pipe.pipe_code
            ):
                first_test = t
            if last_pipe and (
                t.pipe_id == last_pipe.id or t.pipe_code == last_pipe.pipe_code
            ):
                last_test = t

        # WAIT if no test recorded yet, else Pass/Fail from its decision.
        def _res(test):
            if not test or not test.decision:
                return "WAIT"
            return "Pass" if test.decision == "ACCEPT" else "Fail"

        f_res = _res(first_test)
        l_res = _res(last_test)
        both_ready = f_res != "WAIT" and l_res != "WAIT"
        both_pass = f_res == "Pass" and l_res == "Pass"
        source = f"First({f_res})+Last({l_res})"

        for pipe in pipes:
            if pipe.id in individually_tested:
                continue
            if pipe.mechanical_test_role in ("FIRST", "LAST"):
                # Sample: its OWN result decides it. Pass→ACCEPT, Fail→HOLD
                # (recoverable, never terminal REJECT), not-yet-tested→WAITING.
                own = f_res if pipe.mechanical_test_role == "FIRST" else l_res
                pipe.lab_decision = (
                    "ACCEPT" if own == "Pass" else "HOLD" if own == "Fail" else "WAITING"
                )
                pipe.lab_decision_reason = (
                    f"{pipe.mechanical_test_role} sample mechanical test: {own}"
                )
                pipe.cascade_from = "sample"
            else:
                # Non-sample: cascade only once both samples are in.
                pipe.lab_decision = (
                    "WAITING" if not both_ready else "ACCEPT" if both_pass else "HOLD"
                )
                pipe.lab_decision_reason = (
                    "First & Last cascade"
                    if both_ready
                    else "Awaiting first/last sample results"
                )
                pipe.cascade_from = f"cascade from {source}"

    db.session.commit()

    # Update Lab stage decisions and recalculate final decision
    for pipe in pipes:
        update_lab_stage_decision(pipe)
        update_final_decision(pipe)


def _individually_tested_pipe_ids(ladle_id, pipes):
    """IDs of ANY-role pipes that have their own active, decided mechanical test."""
    from app.models.mechanical import MechanicalTest

    any_pipes = [p for p in pipes if p.mechanical_test_role == "ANY"]
    if not any_pipes:
        return set()
    tests = (
        MechanicalTest.query.filter_by(ladle_id=ladle_id, status="ACTIVE")
        .filter(MechanicalTest.decision.isnot(None))
        .all()
    )
    tests = [t for t in tests if t.decision]  # blank decision = not evaluated
    tested_ids = {t.pipe_id for t in tests if t.pipe_id}
    tested_codes = {t.pipe_code for t in tests if t.pipe_code}
    return {
        p.id for p in any_pipes if p.id in tested_ids or p.pipe_code in tested_codes
    }


def reapply_mechanical_results(ladle_id):
    """Re-apply recorded mechanical results for a ladle after a role re-assign.

    ``assign_mechanical_roles`` resets every pipe's ``lab_decision`` to WAITING —
    correct when the chemical decision is first saved, but destructive when it is
    re-run on a pipe ADD/EDIT for a ladle that already has recorded mechanical
    tests: the auto lab result (e.g. LAST pipe passed → whole ladle ACCEPT) is
    lost. Call this right after ``assign_mechanical_roles`` in the add/edit routes
    to recompute ``lab_decision`` from the still-active tests. Idempotent — each
    ``propagate_mechanical_result`` recomputes the ladle from current test state.
    """
    from app.models.mechanical import MechanicalTest

    tests = (
        MechanicalTest.query.filter_by(ladle_id=ladle_id, status="ACTIVE")
        .filter(MechanicalTest.decision.isnot(None))
        .all()
    )
    for t in tests:
        propagate_mechanical_result(t)


def effective_lab_decision(pipe):
    """The authoritative lab decision used for gating and final calculation.

    There is a single lab stage (built-in code ``lab``, displayed as
    "Lab Approval"). The auto-computed ``pipe.lab_decision`` drives gating; this
    thin indirection gives the routes one place to read the lab state. Returns an
    uppercase state string (ACCEPT / REJECT / HOLD / WAITING / ...) or None.
    """
    return pipe.lab_decision


def calculate_final_decision(pipe):
    """
    Calculate final decision combining the effective lab decision + stage decisions.
    Returns the final decision string.
    """
    # Chemical rejection is terminal and cannot be overridden by a manual
    # Lab Approval (the ladle itself is scrap).
    if pipe.lab_decision == "BLOCKED":
        return "REJECT"

    # From here the supervisor's manual Lab Approval (if any) overrides the auto
    # lab result — his decision is authoritative end-to-end, per-pipe.
    eff = effective_lab_decision(pipe)

    # Terminal reject (FULL_100 individual fail, or a manual Reject override).
    if eff == "REJECT":
        return "REJECT"

    # Recoverable mechanical freeze: never terminal, never ACCEPT. Pending.
    if eff == "FROZEN":
        return None

    # NOTE: HOLD is intentionally NOT short-circuited here. A HOLD pipe is
    # allowed through production (§4.3); the stage scan below must still be
    # able to surface a genuine downstream Reject as the final value. Delivery
    # is blocked separately by the lab HOLD gate in stages.update_stage, so a
    # HOLD pipe can never ship regardless of this computed value.

    # If lab decision is not yet determined
    if eff in ("WAITING", None):
        return None

    # Check all post-Lab stages
    from app.models.stage import ProductionStage
    post_lab_stages = [
        ProductionStage.name_for_code("zinc"),
        ProductionStage.name_for_code("cutting"),
        ProductionStage.name_for_code("hydrotest"),
        ProductionStage.name_for_code("cement"),
        ProductionStage.name_for_code("coating"),
        ProductionStage.name_for_code("finish"),
    ]
    has_reject = False
    has_hold = False
    all_decided = True

    for stage_name in post_lab_stages:
        stage = pipe.get_stage(stage_name)
        if stage and stage.decision:
            if stage.decision in ("Reject", "REJECT"):
                has_reject = True
            elif stage.decision in ("Hold", "HOLD"):
                has_hold = True
        else:
            all_decided = False

    if has_reject:
        return "REJECT"
    if has_hold:
        return "HOLD"
    if all_decided and eff == "ACCEPT":
        return "ACCEPT"

    # Not all stages decided yet - decision is still pending
    return None


def update_lab_stage_decision(pipe):
    """Auto-set Lab Approval PipeStage decision from pipe.lab_decision.

    The auto (mechanical-derived) lab result and the supervisor's manual Lab
    Approval are TWO INDEPENDENT decisions. This auto-fill is a convenience for
    the untouched case only: once the line supervisor has manually decided the
    Lab Approval stage (``approved_by_id`` set — e.g. "Reheat treatment",
    "Reject", "100% inspection"), his decision is authoritative and must never
    be overwritten by the auto result. Skip it in that case.
    """
    from app.models.stage import ProductionStage
    lab_name = ProductionStage.name_for_code("lab")
    if not pipe.lab_decision or pipe.lab_decision == "WAITING":
        return

    lab_stage = pipe.get_stage(lab_name)
    # Never overwrite a manually-decided Lab Approval — keep the two separate.
    if lab_stage and lab_stage.approved_by_id:
        return
    if not lab_stage:
        from datetime import date as date_type, datetime

        lab_stage = PipeStage(
            pipe_id=pipe.id, stage_name=lab_name, stage_date=date_type.today()
        )
        db.session.add(lab_stage)

    decision_map = {
        "ACCEPT": "Accept",
        "REJECT": "Reject",
        "HOLD": "Hold",
        "BLOCKED": "Reject",      # terminal تالف
        "FROZEN": "Hold",         # recoverable mechanical freeze (NOT Reject)
    }
    lab_stage.decision = decision_map.get(pipe.lab_decision, pipe.lab_decision)
    lab_stage.reason = pipe.lab_decision_reason
    db.session.commit()


def update_final_decision(pipe):
    """Recalculate and store the final decision for a pipe"""
    decision = calculate_final_decision(pipe)
    if decision:
        pipe.final_decision_value = decision
        db.session.commit()
