"""Non-Conformance Register service.

Collects every *non-conforming* record in the plant — chemistry out of
tolerance, failed mechanical properties, rejected/held production stages and
held/rejected pipes — into one flat list of rows a supervisor or manager can
act on.

Design notes
------------
* **Grain is the source, not the consequence.** A bad ladle cascades to dozens
  of pipes; the register shows ONE row for the ladle with the affected pipe
  count, so the manager acts once at the source instead of 30 times.
* **Hold is a non-conformance.** ``PipeStage.classify_decision`` only knows
  accept/reject — every Hold / Rework / Retest / Resample / Reheat / DownGrade
  falls through to ``'pending'`` there. That classifier is load-bearing for the
  progress + reporting views, so this module keeps its own
  :data:`NON_CONFORMING` set rather than widening it.
* **Thresholds are not redefined here.** Chemistry severity comes from
  ``decision_service`` (``element_rules.json``, admin-editable) and mechanical
  pass/fail from ``mechanical_decision_service`` (``mechanical_rules.json``).
* **Read + navigate, never write.** Rows deep-link to the screen that owns the
  decision so the real service functions (``propagate_mechanical_result``,
  ``update_lab_stage_decision``, ``update_final_decision``) and the §4.4
  supersede rule stay in charge.
"""

from datetime import date, timedelta

from flask import url_for

from app import db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.nonconformance_action import NonConformanceAction
from app.services import decision_service, mechanical_decision_service


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# Terminal rejections — the material is scrap / returned.
REJECT_DECISIONS = frozenset(
    {
        "Reject", "REJECT", "rejected", "رفض", "تالف",
        "Returned", "مرتجع", "BLOCKED", "Blocked",
        "FAIL", "Fail", "failed",
    }
)

# Recoverable non-conformances — the pipe is stopped but can come back.
HOLD_DECISIONS = frozenset(
    {
        "Hold", "HOLD", "hold", "حجز",
        "Rework", "إعادة عمل",
        "Retest", "إعادة اختبار",
        "Resample", "إعادة عينة",
        "Reheat treatment", "إعادة معالجة حرارية",
        "DownGrade", "Downgrade", "تخفيض درجة",
        "Micro-structure", "فحص البنية المجهرية",
        "FROZEN",  # inert legacy state — surfaced, never crashed on
    }
)

NON_CONFORMING = REJECT_DECISIONS | HOLD_DECISIONS

# Chemistry escalation levels that mean "outside the optimal window".
# priority 1 = فحص أخيرة فقط (in spec), 4 = تالف.
CHEM_MIN_PRIORITY = 2

# What the manager is expected to do next, keyed by the stored decision.
ACTION_BY_DECISION = {
    "Hold": ("Re-test / decide", "إعادة اختبار / اتخاذ قرار"),
    "HOLD": ("Re-test / decide", "إعادة اختبار / اتخاذ قرار"),
    "حجز": ("Re-test / decide", "إعادة اختبار / اتخاذ قرار"),
    "Retest": ("Re-test the sample", "إعادة اختبار العينة"),
    "إعادة اختبار": ("Re-test the sample", "إعادة اختبار العينة"),
    "Resample": ("Take a new sample", "أخذ عينة جديدة"),
    "إعادة عينة": ("Take a new sample", "أخذ عينة جديدة"),
    "Rework": ("Send back for rework", "إعادة العمل"),
    "إعادة عمل": ("Send back for rework", "إعادة العمل"),
    "Reheat treatment": ("Re-run heat treatment", "إعادة المعالجة الحرارية"),
    "إعادة معالجة حرارية": ("Re-run heat treatment", "إعادة المعالجة الحرارية"),
    "DownGrade": ("Downgrade the class", "تخفيض الدرجة"),
    "تخفيض درجة": ("Downgrade the class", "تخفيض الدرجة"),
    "Micro-structure": ("Run micro-structure test", "فحص البنية المجهرية"),
    "فحص البنية المجهرية": ("Run micro-structure test", "فحص البنية المجهرية"),
    "Reject": ("Segregate as scrap", "عزل كتالف"),
    "REJECT": ("Segregate as scrap", "عزل كتالف"),
    "رفض": ("Segregate as scrap", "عزل كتالف"),
    "تالف": ("Segregate as scrap", "عزل كتالف"),
    "BLOCKED": ("Segregate as scrap", "عزل كتالف"),
    "Returned": ("Handle the return", "معالجة المرتجع"),
    "مرتجع": ("Handle the return", "معالجة المرتجع"),
    "FROZEN": ("Review — legacy frozen state", "مراجعة — حالة قديمة"),
}

DEFAULT_ACTION = ("Review and decide", "مراجعة واتخاذ قرار")

# Chemistry decisions that are inspection escalations rather than a decision the
# manager still owes — used to phrase the action for a ladle.
CHEM_ACTION = {
    "فحص الشحنة 100%": ("Inspect 100% of the batch", "فحص الشحنة 100%"),
    "فحص أولى وأخيرة": ("Inspect first & last pipes", "فحص أولى وأخيرة"),
    "تالف": ("Segregate the ladle as scrap", "عزل الشحنة كتالف"),
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

SOURCE_LABELS = {
    "chemical": ("Chemical", "التحليل الكيميائي"),
    "mechanical": ("Mechanical", "الاختبار الميكانيكي"),
    "stage": ("Stage", "المرحلة"),
    "pipe": ("Pipe / Lab decision", "قرار المعمل"),
}


def is_non_conforming(decision):
    """True when a stored decision string means "not accepted"."""
    if not decision:
        return False
    value = str(decision).strip()
    if value in NON_CONFORMING:
        return True
    lowered = value.lower()
    return any(d.lower() == lowered for d in NON_CONFORMING)


def is_reject(decision):
    """True when the decision is a terminal rejection (not recoverable)."""
    if not decision:
        return False
    value = str(decision).strip()
    if value in REJECT_DECISIONS:
        return True
    lowered = value.lower()
    return any(d.lower() == lowered for d in REJECT_DECISIONS)


def _action_for(decision):
    return ACTION_BY_DECISION.get(str(decision or "").strip(), DEFAULT_ACTION)


def _severity_for(decision):
    if is_reject(decision):
        return "critical"
    if is_non_conforming(decision):
        return "high"
    return "medium"


def _first_text(*values):
    """First non-empty stripped string among the arguments, else ''."""
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return ""


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def parse_filters(args):
    """Read the register's query params with safe defaults (last 90 days)."""
    def _date(key, default):
        raw = (args.get(key) or "").strip()
        try:
            return date.fromisoformat(raw).isoformat()
        except (ValueError, TypeError):
            return default

    today = date.today()
    return {
        "date_from": _date("date_from", (today - timedelta(days=90)).isoformat()),
        "date_to": _date("date_to", today.isoformat()),
        "source": (args.get("source") or "").strip(),
        "stage": (args.get("stage") or "").strip(),
        "severity": (args.get("severity") or "").strip(),
        "status": (args.get("status") or "").strip(),
        "search": (args.get("search") or "").strip(),
        "cover_status": (args.get("cover_status") or "").strip(),
        "responsible_from": _date("responsible_from", ""),
        "responsible_to": _date("responsible_to", ""),
    }


# --------------------------------------------------------------------------
# Collectors — one per source of truth
# --------------------------------------------------------------------------


def _chemical_rows(filters):
    """One row per ladle whose chemistry is outside the optimal window."""
    rows = []
    analyses = (
        ChemicalAnalysis.query.filter(
            ChemicalAnalysis.test_date >= filters["date_from"],
            ChemicalAnalysis.test_date <= filters["date_to"],
        )
        .order_by(ChemicalAnalysis.test_date.desc())
        .all()
    )
    if not analyses:
        return rows

    # Affected pipe count per ladle — the blast radius of one bad ladle.
    ladle_ids = [a.ladle_id for a in analyses if a.ladle_id]
    pipe_counts = {}
    if ladle_ids:
        for ladle_id, count in (
            db.session.query(Pipe.ladle_id, db.func.count(Pipe.id))
            .filter(Pipe.ladle_id.in_(ladle_ids))
            .group_by(Pipe.ladle_id)
            .all()
        ):
            pipe_counts[ladle_id] = count

    for analysis in analyses:
        auto = decision_service.calculate_auto_decision(
            {
                field: getattr(analysis, field, None)
                for field in decision_service.ELEMENT_MAP
            }
        )
        priority = auto.get("decision_priority") or 0
        chem_flagged = priority >= CHEM_MIN_PRIORITY
        decision_flagged = is_non_conforming(analysis.decision)

        if not (chem_flagged or decision_flagged or analysis.has_defect):
            continue

        # Problem text: the elements that drove the worst decision, with values.
        breakdown = auto.get("element_decisions") or {}
        offenders = [
            f"{code} = {info['value']:g}"
            for code, info in breakdown.items()
            if (info.get("priority") or 0) >= CHEM_MIN_PRIORITY
        ]
        recommended = str(auto.get("recommended_decision") or "").strip()
        if offenders:
            problem = "Out of tolerance: " + ", ".join(offenders)
            if recommended:
                problem += f" → rules require: {recommended}"
        elif analysis.has_defect:
            problem = _first_text(analysis.defect_reason, "Defect recorded")
        else:
            problem = _first_text(analysis.reason, "Non-conforming decision")

        stored = _first_text(analysis.decision)
        status = stored or "—"
        if priority >= 4 or is_reject(stored):
            severity = "critical"
        elif priority >= 3 or decision_flagged:
            severity = "high"
        else:
            severity = "medium"

        # A stored decision milder than what the rules require is the single
        # most important thing a manager can see here — surface it as the
        # action rather than parroting the rule's verdict as if it were live.
        stored_priority = decision_service.DECISION_PRIORITY.get(stored, 0)
        if not stored:
            action = ("Record the lab decision", "تسجيل قرار المعمل")
        elif stored_priority and stored_priority < (priority or 0):
            action = (
                f"Review override — rules require: {recommended}",
                f"مراجعة القرار — القاعدة تتطلب: {recommended}",
            )
        else:
            action = CHEM_ACTION.get(stored) or _action_for(stored)

        rows.append(
            {
                "source": "chemical",
                "source_code": analysis.ladle_id or f"#{analysis.id}",
                "stage": "Melting Ladle",
                "date": analysis.test_date,
                "problem": problem,
                "root_cause": _first_text(
                    analysis.reason, analysis.defect_reason, analysis.engineer_notes
                ),
                "status": status or "—",
                "severity": severity,
                "action": action,
                "affected": pipe_counts.get(analysis.ladle_id, 0),
                "cascade_from": "",
                "notes": _first_text(analysis.notes),
                "url": url_for("chemical.detail", id=analysis.id),
            }
        )
    return rows


def _mechanical_rows(filters):
    """One row per ACTIVE mechanical test that failed or was held.

    ``status == 'ACTIVE'`` matters: without it every superseded retest stays on
    the register forever and the "hold → retest → cleared" flow never closes.
    """
    rows = []
    tests = (
        MechanicalTest.query.filter(
            MechanicalTest.test_date >= filters["date_from"],
            MechanicalTest.test_date <= filters["date_to"],
            db.or_(
                MechanicalTest.status == "ACTIVE",
                MechanicalTest.status.is_(None),
            ),
        )
        .order_by(MechanicalTest.test_date.desc())
        .all()
    )

    for test in tests:
        failures = []
        for prop in (
            "tensile_strength",
            "elongation",
            "nodularity_percent",
            "nodule_count",
            "carbides",
            "hardness",
        ):
            value = getattr(test, prop, None)
            if value is None:
                continue
            try:
                result = mechanical_decision_service.validate_property(prop, value)
            except Exception:
                continue
            if not result.get("valid"):
                name = result.get("property_name") or prop
                condition = result.get("condition") or ""
                failures.append(f"{name} = {value:g} (needs {condition})".strip())

        decision_flagged = is_non_conforming(test.decision)
        if not (failures or decision_flagged or test.has_defect):
            continue

        if failures:
            problem = "Failed: " + "; ".join(failures)
        elif test.has_defect:
            problem = _first_text(test.defect_reason, "Defect recorded")
        else:
            problem = _first_text(test.reason, "Non-conforming decision")

        status = _first_text(test.decision, "FAIL" if failures else "—")
        rows.append(
            {
                "source": "mechanical",
                "source_code": _first_text(
                    test.pipe_code, test.code, test.ladle_id, f"#{test.id}"
                ),
                "stage": "Lab Approval",
                "date": test.test_date,
                "problem": problem,
                "root_cause": _first_text(
                    test.reason, test.defect_reason, test.retest_reason
                ),
                "status": status,
                "severity": "critical" if is_reject(status) else "high",
                "action": _action_for(status),
                "affected": 1,
                "cascade_from": _first_text(test.ladle_id),
                "notes": _first_text(test.comments),
                "url": url_for("mechanical.detail", id=test.id),
            }
        )
    return rows


def _stage_rows(filters):
    """One row per rejected / held / defective production stage."""
    rows = []
    stages = (
        db.session.query(PipeStage, Pipe)
        .join(Pipe, PipeStage.pipe_id == Pipe.id)
        .filter(
            Pipe.production_date >= filters["date_from"],
            Pipe.production_date <= filters["date_to"],
            db.or_(
                PipeStage.has_defect.is_(True),
                PipeStage.decision.isnot(None),
            ),
        )
        .order_by(Pipe.production_date.desc())
        .all()
    )

    for stage, pipe in stages:
        decision_flagged = is_non_conforming(stage.decision)
        if not (decision_flagged or stage.has_defect):
            continue

        problem = _first_text(
            stage.defect_type,
            stage.defect_reason,
            stage.reason,
            "Non-conforming decision",
        )
        status = _first_text(stage.decision, "Defect")
        rows.append(
            {
                "source": "stage",
                "source_code": _first_text(
                    pipe.pipe_code, pipe.no_code, f"#{pipe.id}"
                ),
                "stage": stage.stage_name,
                "date": stage.stage_date or pipe.production_date,
                "problem": problem,
                "root_cause": _first_text(stage.reason, stage.defect_reason),
                "status": status,
                "severity": _severity_for(stage.decision) if decision_flagged else "medium",
                "action": _action_for(stage.decision),
                "affected": 1,
                "cascade_from": _first_text(pipe.ladle_id),
                "notes": _first_text(stage.notes),
                "url": url_for("stages.view", id=pipe.id),
            }
        )
    return rows


def _pipe_rows(filters):
    """Lab decisions on pipes, grouped by ladle + decision.

    A ladle-level cascade sets the same ``lab_decision`` on every pipe it
    produced; emitting one row each would bury the manager. WAITING is excluded
    on purpose — it is the by-design "no decision yet" state, not a
    non-conformance.
    """
    pipes = (
        Pipe.query.filter(
            Pipe.production_date >= filters["date_from"],
            Pipe.production_date <= filters["date_to"],
            Pipe.lab_decision.isnot(None),
        )
        .order_by(Pipe.production_date.desc())
        .all()
    )

    groups = {}
    for pipe in pipes:
        if not is_non_conforming(pipe.lab_decision):
            continue
        key = (pipe.ladle_id or f"pipe:{pipe.id}", str(pipe.lab_decision).strip())
        group = groups.get(key)
        if group is None:
            groups[key] = group = {
                "source": "pipe",
                "source_code": pipe.ladle_id or _first_text(pipe.pipe_code, f"#{pipe.id}"),
                "stage": "Lab Approval",
                "date": pipe.production_date,
                "problem": _first_text(
                    pipe.lab_decision_reason,
                    f"Lab decision: {pipe.lab_decision}",
                ),
                "root_cause": _first_text(
                    pipe.lab_decision_reason, pipe.final_decision_reason
                ),
                "status": str(pipe.lab_decision).strip(),
                "severity": _severity_for(pipe.lab_decision),
                "action": _action_for(pipe.lab_decision),
                "affected": 0,
                "cascade_from": _first_text(pipe.cascade_from),
                "notes": "",
                "url": (
                    url_for("stages.list", ladle_id=pipe.ladle_id)
                    if pipe.ladle_id
                    else url_for("stages.view", id=pipe.id)
                ),
            }
        group["affected"] += 1
        if pipe.production_date and (
            group["date"] is None or pipe.production_date > group["date"]
        ):
            group["date"] = pipe.production_date

    return list(groups.values())


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

COLLECTORS = {
    "chemical": _chemical_rows,
    "mechanical": _mechanical_rows,
    "stage": _stage_rows,
    "pipe": _pipe_rows,
}


def build_register(filters):
    """Return ``(rows, summary, actions)`` for the register, newest & worst first."""
    wanted = filters.get("source")
    rows = []
    for name, collector in COLLECTORS.items():
        if wanted and wanted != name:
            continue
        rows.extend(collector(filters))

    # Post-filters that apply uniformly across every source.
    stage = filters.get("stage")
    if stage:
        rows = [r for r in rows if r["stage"] == stage]

    severity = filters.get("severity")
    if severity:
        rows = [r for r in rows if r["severity"] == severity]

    status = filters.get("status")
    if status == "reject":
        rows = [r for r in rows if is_reject(r["status"])]
    elif status == "hold":
        rows = [r for r in rows if not is_reject(r["status"])]

    search = (filters.get("search") or "").lower()
    if search:
        rows = [
            r
            for r in rows
            if search in str(r["source_code"]).lower()
            or search in str(r["problem"]).lower()
            or search in str(r["root_cause"]).lower()
            or search in str(r["status"]).lower()
        ]

    actions = load_actions(rows)

    cover_status = filters.get("cover_status")
    if cover_status == "none":
        rows = [r for r in rows if (r["source"], r["source_code"]) not in actions]
    elif cover_status:
        rows = [
            r
            for r in rows
            if getattr(actions.get((r["source"], r["source_code"])), "status", None)
            == cover_status
        ]

    responsible_from = filters.get("responsible_from")
    responsible_to = filters.get("responsible_to")
    if responsible_from or responsible_to:
        lower = date.fromisoformat(responsible_from) if responsible_from else None
        upper = date.fromisoformat(responsible_to) if responsible_to else None
        kept = []
        for r in rows:
            due = getattr(
                actions.get((r["source"], r["source_code"])), "responsible_date", None
            )
            if due is None:
                continue
            if lower and due < lower:
                continue
            if upper and due > upper:
                continue
            kept.append(r)
        rows = kept

    rows.sort(
        key=lambda r: (
            SEVERITY_ORDER.get(r["severity"], 9),
            -(r["date"].toordinal() if r["date"] else 0),
        )
    )

    summary = {
        "total": len(rows),
        "critical": sum(1 for r in rows if r["severity"] == "critical"),
        "high": sum(1 for r in rows if r["severity"] == "high"),
        "rejects": sum(1 for r in rows if is_reject(r["status"])),
        "holds": sum(1 for r in rows if not is_reject(r["status"])),
        "affected_pipes": sum(r["affected"] or 0 for r in rows),
        "by_source": {
            name: sum(1 for r in rows if r["source"] == name) for name in COLLECTORS
        },
    }
    return rows, summary, actions


def export_columns():
    """``(key, label, value_fn, default)`` registry for the export/print picker."""
    return [
        ("source", "Source", lambda r: SOURCE_LABELS.get(r["source"], (r["source"],))[0], True),
        ("source_code", "Source Code", lambda r: r["source_code"], True),
        ("stage", "Stage", lambda r: r["stage"], True),
        ("date", "Date", lambda r: r["date"].isoformat() if r["date"] else "", True),
        ("severity", "Severity", lambda r: r["severity"], True),
        ("status", "Status", lambda r: r["status"], True),
        ("problem", "Problem", lambda r: r["problem"], True),
        ("root_cause", "Root Cause", lambda r: r["root_cause"], True),
        ("action", "Required Action", lambda r: r["action"][0], True),
        ("affected", "Affected Pipes", lambda r: r["affected"], True),
        ("cascade_from", "Cascaded From", lambda r: r["cascade_from"], False),
        ("notes", "Notes", lambda r: r["notes"], False),
    ]


def load_actions(rows):
    """Return ``{(source, source_code): NonConformanceAction}`` for the
    given register rows. Rows without an action are simply omitted — the
    caller treats a missing key as "no cover action yet"."""
    if not rows:
        return {}
    keys = {(r["source"], r["source_code"]) for r in rows}
    sources = {k[0] for k in keys}
    codes = {k[1] for k in keys}
    found = (
        NonConformanceAction.query.filter(
            NonConformanceAction.source.in_(sources),
            NonConformanceAction.source_code.in_(codes),
        ).all()
    )
    return {(a.source, a.source_code): a for a in found}
