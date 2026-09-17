"""Diagnosis — what is wrong, why, and the button that fixes it.

The canvas can already show that something is red. That on its own only tells
an engineer to go looking. This turns each stopped node into three things:

    what   one sentence naming the problem
    why    the evidence behind it — the reading, the limit, the rule
    fix    one or more actions, each a label and a deep link to the exact
           screen where the work gets done

The rules are read from where the app already enforces them, so an action
never contradicts what the save handler will do:

- chemistry limits          decision_service / element_rules.json
- mechanical criteria       mechanical_decision_service
- the sample cascade        pipe_decision_service (§4.2: a mechanical fail
                            holds, it never auto-scraps)
- the Zinc and Delivery gates in stages.update_stage
- measurement bands stored on the stage row itself
"""

from flask import url_for

from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.services import decision_service
from app.services import traceability_service as ts


def _action(label_en, label_ar, endpoint, primary=False, **kwargs):
    """One way out of the problem, as a link to the screen that fixes it.

    url_for is deliberately not guarded: a diagnosis whose buttons silently
    vanish is worse than no diagnosis at all, because the operator is told
    something is wrong and given nowhere to go. A missing endpoint is a bug
    and should fail loudly in the tests.
    """
    return {"label_en": label_en, "label_ar": label_ar,
            "url": url_for(endpoint, **kwargs), "primary": primary}


def _pack(what_en, what_ar, why=None, actions=(), severity="hold"):
    return {"what_en": what_en, "what_ar": what_ar,
            "why": [w for w in (why or []) if w],
            "actions": [a for a in actions if a],
            "severity": severity}


def _console_link(pipe, stage_name=None):
    """The Stage Console filtered to this pipe, at this stage.

    The console searches on `q` and filters on `stage`, so those two args land
    the operator on the exact row instead of the top of the list.
    """
    code = pipe.pipe_code or pipe.no_code
    if not code:
        return None
    kwargs = {"q": code}
    if stage_name:
        kwargs["stage"] = stage_name
    return _action("Fix it in the Stage Console", "أصلحها في كونسول المراحل",
                   "stages.console", primary=True, **kwargs)


# ---------------------------------------------------------------------------
# Chemistry
# ---------------------------------------------------------------------------

def _chem_evidence(ladle):
    """Which elements pushed the heat out of the optimal window."""
    why = []
    for code, value in ladle.get_element_values().items():
        if value is None:
            continue
        verdict = decision_service.get_element_decision(code, value)
        if verdict.get("in_spec"):
            continue
        why.append("%s = %s → %s" % (code, value, verdict.get("decision")))
    return why


def diagnose_ladle(ladle):
    state = ts.state_of(ladle.decision)
    if state not in ("fail", "hold", "blocked"):
        return None
    why = _chem_evidence(ladle)
    if not why and ladle.reason:
        why = [ladle.reason]
    actions = [
        _action("Review the analysis", "راجع التحليل", "chemical.edit",
                primary=True, id=ladle.id),
        _action("Open the record", "افتح السجل", "chemical.detail", id=ladle.id),
    ]
    if state == "fail":
        return _pack(
            "Heat rejected on chemistry — every pipe cast from it is blocked.",
            "الشحنة مرفوضة كيميائياً — كل المواسير منها محظورة.",
            why or ["Decision recorded as %s" % ladle.decision],
            actions, "fail")
    return _pack(
        "Heat is outside the optimal window, so it is on a tighter "
        "inspection level.",
        "الشحنة خارج النطاق الأمثل، لذلك مستوى الفحص أعلى.",
        why, actions, "hold")


# ---------------------------------------------------------------------------
# Mechanical
# ---------------------------------------------------------------------------

def diagnose_mechanical(test):
    state = ts.state_of(test.decision) if test.decision else "waiting"
    if state == "waiting":
        return _pack(
            "Test recorded but no verdict entered — the heat cannot be "
            "released until it is decided.",
            "الاختبار مسجل بدون قرار — لا يمكن الإفراج عن الشحنة قبل تحديده.",
            ["status = %s" % (test.status or "ACTIVE")],
            [_action("Enter the verdict", "أدخل القرار", "mechanical.edit",
                     primary=True, id=test.id)],
            "hold")
    if state != "fail":
        return None

    why = []
    for label, value in (("Tensile (MPa)", test.tensile_mpa),
                         ("Elongation %", test.elongation),
                         ("Hardness", test.hardness),
                         ("Nodularity %", test.nodularity_percent)):
        if value is not None:
            why.append("%s = %s" % (label, value))
    if test.reason:
        why.insert(0, test.reason)

    return _pack(
        "Mechanical test failed. On a sample-tested heat this holds every "
        "pipe from it — it never scraps them automatically (§4.2).",
        "الاختبار الميكانيكي فشل. في شحنة تُفحص بالعينة يُحجز كل إنتاجها ولا "
        "يُخرد تلقائياً (§4.2).",
        why,
        [_action("Record a retest", "سجّل إعادة اختبار", "mechanical.add",
                 primary=True),
         _action("Open the test", "افتح الاختبار", "mechanical.detail",
                 id=test.id)],
        "fail")


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------

def _band_evidence(stage):
    """Readings that fell outside the band stored on this stage row."""
    why = []
    for m in ts._stage_measurements(stage):
        if m.get("ok") is False:
            why.append("%s = %s (outside the recorded limits)"
                       % (m["label"], m["value"]))
    return why[:6]


def diagnose_stage(stage):
    state = ts.state_of(stage.decision)
    pipe = Pipe.query.get(stage.pipe_id)
    if pipe is None:
        return None

    why = _band_evidence(stage)
    if stage.defect_reason:
        why.insert(0, "Defect: %s" % stage.defect_reason)
    elif stage.reason:
        why.insert(0, stage.reason)

    if ts.is_rework(stage.decision):
        return _pack(
            "Sent back at %s — the pipe re-runs this step before it can move "
            "on." % stage.stage_name,
            "أُعيد عند %s — تعاد هذه المرحلة قبل المتابعة." % stage.stage_name,
            why or ["Decision: %s" % stage.decision],
            [_console_link(pipe, stage.stage_name),
             _action("Open the pipe", "افتح الماسورة", "stages.view",
                     id=pipe.id)],
            "hold")

    if state == "fail":
        return _pack(
            "Rejected at %s." % stage.stage_name,
            "مرفوضة عند %s." % stage.stage_name,
            why or ["Decision: %s" % stage.decision],
            [_console_link(pipe, stage.stage_name),
             _action("Open the pipe", "افتح الماسورة", "stages.view",
                     id=pipe.id)],
            "fail")

    if state == "hold":
        return _pack(
            "Held at %s — it needs a decision before the pipe moves on."
            % stage.stage_name,
            "محجوزة عند %s — تحتاج قراراً قبل المتابعة." % stage.stage_name,
            why or ["Decision: %s" % stage.decision],
            [_console_link(pipe, stage.stage_name)],
            "hold")

    return None


# ---------------------------------------------------------------------------
# Pipe — the gates it is sitting behind
# ---------------------------------------------------------------------------

def diagnose_pipe(pipe):
    """Why this pipe is not moving, and the screen that releases it.

    Mirrors the gates enforced in stages.update_stage so the advice and the
    save handler cannot disagree.
    """
    lab = (pipe.lab_decision or "").strip().upper()
    zinc = ProductionStage.name_for_code("zinc")
    delivery = ProductionStage.name_for_code("delivery")

    if lab == "BLOCKED":
        ladle = pipe.chemical_analysis
        return _pack(
            "Blocked — the heat it was cast from was rejected on chemistry. "
            "This is terminal and cannot be overridden downstream.",
            "محظورة — الشحنة المصبوبة منها مرفوضة كيميائياً. القرار نهائي.",
            [pipe.lab_decision_reason or pipe.cascade_from or ""],
            [_action("Open the heat", "افتح الشحنة", "chemical.detail",
                     primary=True, id=ladle.id) if ladle else None,
             _action("Open the pipe", "افتح الماسورة", "stages.view",
                     id=pipe.id)],
            "blocked")

    if lab == "REJECT":
        return _pack(
            "Rejected by the lab — %s is gated on the lab decision, so it "
            "cannot proceed." % zinc,
            "مرفوضة من المعمل — مرحلة %s موقوفة على قرار المعمل." % zinc,
            [pipe.lab_decision_reason or pipe.cascade_from or ""],
            [_action("Open the pipe", "افتح الماسورة", "stages.view",
                     primary=True, id=pipe.id)],
            "fail")

    if lab == "HOLD":
        ladle = pipe.chemical_analysis
        cascaded = (pipe.cascade_from or "").startswith("cascade")
        what_en = ("On hold from the heat's sample test — a mechanical fail "
                   "holds the whole heat rather than scrapping it. It is "
                   "released by a passing retest."
                   if cascaded else
                   "On hold — %s will not accept it until the hold is "
                   "cleared." % delivery)
        actions = [_action("Record a retest", "سجّل إعادة اختبار",
                           "mechanical.add", primary=cascaded)]
        if ladle:
            actions.append(_action("Open the heat", "افتح الشحنة",
                                   "chemical.detail", id=ladle.id))
        actions.append(_action("Open the pipe", "افتح الماسورة",
                               "stages.view", primary=not cascaded, id=pipe.id))
        return _pack(what_en,
                     "محجوزة — تحتاج إعادة اختبار ناجحة للإفراج عنها.",
                     [pipe.lab_decision_reason or pipe.cascade_from or ""],
                     actions, "hold")

    if lab in ("WAITING", "") or lab is None:
        role = pipe.mechanical_test_role
        ladle = pipe.chemical_analysis
        if role in ("FIRST", "LAST", "ALL"):
            return _pack(
                "Waiting on its mechanical test — this pipe is the %s sample "
                "for its heat, and %s is gated until the lab decides."
                % (role, zinc),
                "في انتظار الاختبار الميكانيكي — هذه عينة %s للشحنة." % role,
                ["No active mechanical test has decided this pipe yet."],
                [_action("Record the test", "سجّل الاختبار", "mechanical.add",
                         primary=True),
                 _action("Open the pipe", "افتح الماسورة", "stages.view",
                         id=pipe.id)],
                "hold")
        if ladle is not None:
            return _pack(
                "Waiting on the heat's sample result — %s is gated until the "
                "lab decides." % zinc,
                "في انتظار نتيجة عينة الشحنة — مرحلة %s موقوفة." % zinc,
                ["Heat %s, this pipe is not a sample." % ladle.ladle_id],
                [_action("Open the heat", "افتح الشحنة", "chemical.detail",
                         primary=True, id=ladle.id),
                 _action("Record the sample test", "سجّل اختبار العينة",
                         "mechanical.add")],
                "hold")
        return _pack(
            "No heat recorded for this pipe, so nothing can decide it.",
            "لا توجد شحنة مسجلة لهذه الماسورة.",
            ["ladle_id is empty"],
            [_action("Open the pipe", "افتح الماسورة", "stages.view",
                     primary=True, id=pipe.id)],
            "hold")

    # The lab passed it but something downstream did not. This is the case an
    # operator finds hardest to read — the pipe shows ACCEPT on the lab line
    # and REJECT overall — so name the stage that actually turned it.
    state = ts.effective_pipe_state(pipe)
    if state in ("fail", "hold", "blocked"):
        culprit = None
        for st in pipe.stages:
            sstate = ts.state_of(st.decision)
            if sstate not in ("fail", "hold", "blocked"):
                continue
            if culprit is None or _SEV_RANK[sstate] < _SEV_RANK[
                    ts.state_of(culprit.decision)]:
                culprit = st
        if culprit is not None:
            why = _band_evidence(culprit)
            if culprit.defect_reason:
                why.insert(0, "Defect: %s" % culprit.defect_reason)
            elif culprit.reason:
                why.insert(0, culprit.reason)
            return _pack(
                "The lab passed this pipe, but %s turned it — the overall "
                "verdict follows the worst step, not the lab line."
                % culprit.stage_name,
                "المعمل قبل الماسورة لكن مرحلة %s رفضتها — القرار النهائي يتبع "
                "أسوأ مرحلة." % culprit.stage_name,
                why or ["%s: %s" % (culprit.stage_name, culprit.decision)],
                [_console_link(pipe, culprit.stage_name),
                 _action("Open the pipe", "افتح الماسورة", "stages.view",
                         id=pipe.id)],
                state)
        return _pack(
            "Marked %s overall, but no stage or lab decision explains it — "
            "the record is inconsistent." % state,
            "الحالة %s بدون سبب مسجل — السجل غير متسق." % state,
            ["lab_decision=%s, final_decision_value=%s"
             % (pipe.lab_decision, pipe.final_decision_value)],
            [_action("Open the pipe", "افتح الماسورة", "stages.view",
                     primary=True, id=pipe.id)],
            state)

    return None


_SEV_RANK = {"fail": 0, "blocked": 1, "hold": 2, "waiting": 3,
             "pass": 4, "none": 5}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def diagnose(kind, id):
    """Diagnosis for one canvas node, or None when nothing is wrong."""
    if kind == "ladle" or kind == "scenario":
        ladle = ChemicalAnalysis.query.get(id)
        return diagnose_ladle(ladle) if ladle else None
    if kind == "mechanical":
        test = MechanicalTest.query.get(id)
        return diagnose_mechanical(test) if test else None
    if kind == "stage":
        stage = PipeStage.query.get(id)
        return diagnose_stage(stage) if stage else None
    if kind == "pipe":
        pipe = Pipe.query.get(id)
        return diagnose_pipe(pipe) if pipe else None
    return None
