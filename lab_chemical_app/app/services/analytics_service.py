"""
Analytics service — shared query helpers for the 13 reports defined in the
development report V2 spec. Each function accepts a filters dict and returns
a results dict the route/template consumes.

Conventions
-----------
- All reports support Count / Weight / Length modes via `mode` in filters
  (one of 'count', 'weight', 'length').
- All reports accept optional filters: date_from, date_to, diameter,
  pipe_class, production_order_id, machine_id, mold_number, customer, shift.
- Returns dicts with shape: {rows, summary, chart_data, filters}.
- Statistical summaries (Total, Avg, Max, Min, Std Dev) are computed in
  `stat_summary()` so every report shares the same footer logic.
"""

from datetime import date, timedelta, datetime
from statistics import mean, pstdev
from collections import defaultdict

from sqlalchemy import func, and_, or_

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.production_order import ProductionOrder
from app.models.product import Customer, Product
from app.models.stage import BUILTIN_CODES, ProductionStage
from app.services import measurement_stats_service
from app.services import dimension_standard_service
from app.services import lining_points_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_filters(args):
    """Turn request.args into a clean filters dict with typed values."""

    def _date(key, default=None):
        raw = args.get(key)
        if not raw:
            return default
        try:
            return date.fromisoformat(raw)
        except (ValueError, TypeError):
            return default

    today = date.today()
    return {
        # No default date floor: historical data stays visible unless the
        # user picks a From date (same contract as the shift/delivery reports).
        "date_from": _date("date_from"),
        "date_to": _date("date_to", today),
        "diameter": args.get("diameter", type=int),
        "pipe_class": args.get("pipe_class"),
        "production_order_id": args.get("production_order_id", type=int),
        "machine_id": args.get("machine_id", type=int),
        "mold_number": args.get("mold_number"),
        "customer": args.get("customer"),
        "shift": args.get("shift", type=int),
        "mode": args.get("mode", "count"),  # count | weight | length
        "group_by": args.get(
            "group_by", "dn"
        ),  # dn | machine | mold | supervisor | month | stage
        "line": args.get("line"),
        "approved_by": args.get("approved_by", type=int),
        "shift_engineer": args.get("shift_engineer"),
        "stage": args.get("stage"),
        "decision": args.get("decision"),
    }


def apply_pipe_filters(query, f):
    """Apply common filters to a Pipe query."""
    if f.get("date_from"):
        query = query.filter(Pipe.production_date >= f["date_from"])
    if f.get("date_to"):
        query = query.filter(Pipe.production_date <= f["date_to"])
    if f.get("diameter"):
        query = query.filter(Pipe.diameter == f["diameter"])
    if f.get("pipe_class"):
        query = query.filter(Pipe.pipe_class == f["pipe_class"])
    if f.get("production_order_id"):
        query = query.filter(Pipe.production_order_id == f["production_order_id"])
    if f.get("machine_id"):
        # Machines are recorded per PipeStage (CCM = the casting machine);
        # Pipe.machine_id is legacy and always NULL.
        ccm = ProductionStage.name_for_code("ccm")
        query = query.filter(
            Pipe.stages.any(
                and_(
                    PipeStage.stage_name == ccm,
                    PipeStage.machine_id == f["machine_id"],
                )
            )
        )
    if f.get("mold_number"):
        query = query.filter(Pipe.mold_number == f["mold_number"])
    if f.get("shift"):
        query = query.filter(Pipe.shift == f["shift"])
    if f.get("customer"):
        # Customer lives on the production order, not the pipe.
        query = query.filter(
            Pipe.production_order.has(
                ProductionOrder.customer_name == f["customer"])
        )
    if f.get("shift_engineer"):
        query = query.filter(Pipe.shift_engineer == f["shift_engineer"])
    if f.get("approved_by"):
        # "Approved by" was parsed by parse_filters but applied by nothing
        # except approval_report, so every other report silently ignored it.
        # Any stage the user approved counts — a pipe they signed off at Zinc
        # is theirs even if someone else took the CCM decision.
        query = query.filter(
            Pipe.stages.any(PipeStage.approved_by_id == f["approved_by"])
        )
    if f.get("stage"):
        # Narrow to pipes that reached a given stage at all.
        query = query.filter(Pipe.stages.any(PipeStage.stage_name == f["stage"]))
    if f.get("decision"):
        query = query.filter(
            Pipe.stages.any(PipeStage.decision == f["decision"]))
    return query


def pipe_length(pipe):
    """Actual measured length for a pipe, in metres.

    Order of precedence:
      1. the Finish stage's measured length (operator-entered in the console
         popup, stored as ``PipeStage.measurement_value`` with
         ``measurement_type == "Length"``);
      2. the product's standard length (``Product.length_m``) when the Finish
         stage has no measurement yet.

    Returns None when neither exists so callers can render an honest no-data
    marker instead of a fabricated default.
    """
    finish_name = ProductionStage.name_for_code("finish")
    finish = pipe.get_stage(finish_name)
    if finish is not None and finish.measurement_value:
        return float(finish.measurement_value)
    product = None
    if pipe.production_order is not None:
        product = pipe.production_order.product
    if product is None and pipe.product_id:
        product = db.session.get(Product, pipe.product_id)
    if product is not None and product.length_m:
        return float(product.length_m)
    return None


def pipe_metric_value(pipe, mode):
    """Return the scalar metric for a pipe given the output mode."""
    if mode == "weight":
        return float(pipe.actual_weight or 0)
    if mode == "length":
        return pipe_length(pipe) or 0.0
    return 1.0  # count


def planned_weight(pipe):
    """Planned (ISO-equivalent) weight for a pipe.

    ``Pipe.iso_weight`` is 0 for every pipe in production (known data gap), so
    when it is not positive we fall back to the product's standard weight
    (``Product.weight_kg`` via the pipe's production order, then its direct
    product link). Returns ``(weight, source)`` where source is 'iso',
    'product', or None when neither exists.
    """
    iso = float(pipe.iso_weight or 0)
    if iso > 0:
        return iso, "iso"
    product = None
    if pipe.production_order is not None:
        product = pipe.production_order.product
    if product is None and pipe.product_id:
        product = db.session.get(Product, pipe.product_id)
    if product is not None and product.weight_kg:
        return float(product.weight_kg), "product"
    return 0.0, None


# A saving figure is only as good as the product a pipe is linked to, and the
# link is wrong on some production records: a DN300 pipe carrying DN100's 144 kg
# standard reports -247% saving, and one such pipe dominates any aggregate it
# lands in. Audited 2026-08-23: 14 of the 78 prod pipes with both weights are
# implausible, and they move the whole-range saving from 0.9% to 25%.
#
# These are excluded from saving ratios and counted, never silently dropped —
# the reports say how many were set aside so the number stays honest and the
# bad records stay findable.
MAX_PLAUSIBLE_SAVING_PCT = 25.0

# How far a pipe's standard weight may sit from the typical standard for its
# DN before the product link itself is the suspect.
MAX_STANDARD_DEVIATION = 0.25


def dn_standard_weights(pipes):
    """Median standard weight per DN, learned from the pipes themselves.

    Self-calibrating rather than hard-coded: the plant's product table is the
    only source of truth for what a DN should weigh, and hard-coding a table
    here would rot the moment a new product is added.
    """
    by_dn = {}
    for pipe in pipes:
        planned, source = planned_weight(pipe)
        if not source or not pipe.diameter:
            continue
        by_dn.setdefault(pipe.diameter, []).append(planned)
    return {
        dn: sorted(vals)[len(vals) // 2]
        for dn, vals in by_dn.items() if vals
    }


def implausible_weight(pipe, planned, dn_medians=None):
    """Why this pipe's weight pair cannot be trusted, or None if it can.

    Returns a short reason string suitable for showing to a lab engineer, so
    an excluded pipe can be chased down rather than just disappearing.
    """
    actual = float(pipe.actual_weight or 0)
    if not planned or not actual:
        return None

    median = (dn_medians or {}).get(pipe.diameter)
    if median and abs(planned - median) / median > MAX_STANDARD_DEVIATION:
        return (f"الوزن المعياري {planned:g} بعيد عن المعتاد لـ DN{pipe.diameter} "
                f"({median:g}) — راجع المنتج المربوط")

    saving_pct = (planned - actual) / planned * 100
    if abs(saving_pct) > MAX_PLAUSIBLE_SAVING_PCT:
        return f"فرق {saving_pct:.0f}% بين الوزن المعياري والفعلي"
    return None


def weight_pairs(pipes):
    """(pipe, planned, actual) for pipes whose weights can be trusted, plus
    the ones set aside and why.

    Returns (usable, excluded) where excluded is a list of
    {"pipe", "planned", "actual", "reason"}.
    """
    medians = dn_standard_weights(pipes)
    usable, excluded = [], []
    for pipe in pipes:
        planned, source = planned_weight(pipe)
        actual = float(pipe.actual_weight or 0)
        if not source or not actual:
            continue
        reason = implausible_weight(pipe, planned, medians)
        if reason:
            excluded.append({"pipe": pipe, "planned": planned,
                             "actual": actual, "reason": reason})
        else:
            usable.append((pipe, planned, actual))
    return usable, excluded


def iso_metric_value(pipe, mode):
    """Return the planned (ISO) equivalent for a pipe for weight/length modes."""
    if mode == "weight":
        return planned_weight(pipe)[0]
    if mode == "length":
        return pipe_length(pipe) or 0.0
    return 1.0


def stat_summary(values):
    """Return total/avg/min/max/std for a list of numeric values."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"total": 0, "avg": 0, "min": 0, "max": 0, "std": 0, "count": 0}
    return {
        "total": round(sum(vals), 2),
        "avg": round(mean(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "std": round(pstdev(vals), 2) if len(vals) > 1 else 0,
        "count": len(vals),
    }


def pipe_machine_code(pipe):
    """Machine code for a pipe, from its CCM (casting) stage record.

    ``Pipe.machine_id`` is legacy and always NULL — machines are recorded
    per PipeStage, and a pipe's "machine" is its casting (CCM) machine.
    Returns None when no CCM machine was recorded.
    """
    ccm = ProductionStage.name_for_code("ccm")
    for s in pipe.stages:
        if s.machine_id and s.stage_name == ccm:
            return s.machine.machine_code if s.machine else None
    return None


def group_key(pipe, group_by):
    """Return the group-by key for a pipe."""
    if group_by == "dn":
        return f"DN{pipe.diameter}" if pipe.diameter else "Unknown DN"
    if group_by == "machine":
        return pipe_machine_code(pipe) or "Unknown"
    if group_by == "mold":
        return pipe.mold_number or "Unknown"
    if group_by == "supervisor":
        return pipe.shift_engineer or "Unknown"
    if group_by == "month":
        return (
            pipe.production_date.strftime("%Y-%m")
            if pipe.production_date
            else "Unknown"
        )
    if group_by == "class":
        return pipe.pipe_class or "Unknown"
    return "All"


# ---------------------------------------------------------------------------
# 1. Production Summary
# ---------------------------------------------------------------------------


def production_summary(filters):
    """Total Produced | Accepted | Rejected | Scrap % | RFT % per group."""
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()

    grouped = defaultdict(
        lambda: {
            "produced": 0,
            "accepted": 0,
            "rejected": 0,
            "hold": 0,
            "pending": 0,
            "metric": 0,
        }
    )
    mode = filters["mode"]
    total_metric_values = []

    for pipe in pipes:
        key = group_key(pipe, filters["group_by"])
        g = grouped[key]
        metric = pipe_metric_value(pipe, mode)
        g["produced"] += 1
        g["metric"] += metric
        total_metric_values.append(metric)
        fd = (pipe.final_decision_value or "").upper()
        if fd == "ACCEPT":
            g["accepted"] += 1
        elif fd == "REJECT":
            g["rejected"] += 1
        elif fd == "HOLD":
            g["hold"] += 1
        else:
            g["pending"] += 1

    rows = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        produced = g["produced"]
        rows.append(
            {
                "group": key,
                "produced": produced,
                "accepted": g["accepted"],
                "rejected": g["rejected"],
                "hold": g["hold"],
                "pending": g["pending"],
                "metric": round(g["metric"], 2),
                "scrap_pct": round(g["rejected"] / produced * 100, 1)
                if produced
                else 0,
                "rft_pct": round(g["accepted"] / produced * 100, 1) if produced else 0,
            }
        )

    total_produced = sum(r["produced"] for r in rows)
    total_accepted = sum(r["accepted"] for r in rows)
    total_rejected = sum(r["rejected"] for r in rows)

    return {
        "rows": rows,
        "summary": {
            "total_produced": total_produced,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
            "overall_rft": round(total_accepted / total_produced * 100, 1)
            if total_produced
            else 0,
            "overall_scrap": round(total_rejected / total_produced * 100, 1)
            if total_produced
            else 0,
            "metric_stats": stat_summary(total_metric_values),
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 2. Order Performance
# ---------------------------------------------------------------------------


def order_performance(filters):
    """Per production order: target, produced, accepted, rejected, completion %, delay, efficiency."""
    query = ProductionOrder.query
    if filters.get("date_from"):
        query = query.filter(ProductionOrder.order_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(ProductionOrder.order_date <= filters["date_to"])

    orders = query.order_by(ProductionOrder.order_date.desc()).all()
    today = date.today()
    rows = []
    for o in orders:
        produced = o.pipes.count()
        target = o.target_quantity or 0
        accepted = sum(
            1 for p in o.pipes if (p.final_decision_value or "").upper() == "ACCEPT"
        )
        rejected = sum(
            1 for p in o.pipes if (p.final_decision_value or "").upper() == "REJECT"
        )

        delay_days = 0
        if o.expected_end_date:
            end_date = o.actual_end_date or today
            delay_days = max(0, (end_date - o.expected_end_date).days)

        efficiency = round(accepted / produced * 100, 1) if produced else 0

        rows.append(
            {
                "order_id": o.id,
                "order_number": o.order_number,
                "customer": o.customer_name or "",
                "target": target,
                "produced": produced,
                "accepted": accepted,
                "rejected": rejected,
                "completion_pct": round(produced / target * 100, 1) if target else 0,
                "delay_days": delay_days,
                "efficiency_pct": efficiency,
                "status": o.status,
            }
        )
    return {
        "rows": rows,
        "summary": {
            "orders": len(rows),
            "avg_efficiency": round(mean([r["efficiency_pct"] for r in rows]), 1)
            if rows
            else 0,
            "avg_completion": round(mean([r["completion_pct"] for r in rows]), 1)
            if rows
            else 0,
            "total_delay_days": sum(r["delay_days"] for r in rows),
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 3. Customer Production
# ---------------------------------------------------------------------------


def customer_production(filters):
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()

    grouped = defaultdict(
        lambda: {"produced": 0, "accepted": 0, "rejected": 0, "delivered": 0}
    )
    for p in pipes:
        cust = p.production_order.customer_name if p.production_order else "Unknown"
        grouped[cust]["produced"] += 1
        fd = (p.final_decision_value or "").upper()
        if fd == "ACCEPT":
            grouped[cust]["accepted"] += 1
        elif fd == "REJECT":
            grouped[cust]["rejected"] += 1
        delivery = p.get_stage(ProductionStage.name_for_code("delivery"))
        if delivery and delivery.decision == "Delivered":
            grouped[cust]["delivered"] += 1

    rows = []
    for cust, g in sorted(grouped.items()):
        produced = g["produced"]
        rows.append(
            {
                "customer": cust,
                "produced": produced,
                "accepted": g["accepted"],
                "rejected": g["rejected"],
                "delivered": g["delivered"],
                "reject_pct": round(g["rejected"] / produced * 100, 1)
                if produced
                else 0,
                "delivery_pct": round(g["delivered"] / produced * 100, 1)
                if produced
                else 0,
            }
        )
    return {"rows": rows, "summary": {"customers": len(rows)}, "filters": filters}


# ---------------------------------------------------------------------------
# 4. Delivery Report
# ---------------------------------------------------------------------------


def delivery_report(filters):
    query = PipeStage.query.filter(
        PipeStage.stage_name == ProductionStage.name_for_code("delivery")
    )
    if filters.get("date_from"):
        query = query.filter(PipeStage.delivery_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(PipeStage.delivery_date <= filters["date_to"])

    rows = []
    deliveries = query.order_by(PipeStage.delivery_date.desc()).all()
    for d in deliveries:
        pipe = d.pipe
        rows.append(
            {
                "delivery_date": d.delivery_date,
                "receipt": d.delivery_receipt,
                "bundle": d.bundle_number,
                "sales_order": d.sales_order,
                "customer": d.delivery_customer,
                "pipe_code": pipe.pipe_code or pipe.no_code,
                "diameter": pipe.diameter,
                "weight": float(pipe.actual_weight or 0),
            }
        )
    return {
        "rows": rows,
        "summary": {
            "deliveries": len(rows),
            "total_weight": round(sum(r["weight"] for r in rows), 2),
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 5. RFT Report (Right First Time)
# ---------------------------------------------------------------------------


def rft_report(filters):
    """Per group: pipes accepted on first pass vs reworked."""
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()
    grouped = defaultdict(lambda: {"total": 0, "first_pass": 0, "rework": 0})

    for pipe in pipes:
        key = group_key(pipe, filters["group_by"])
        g = grouped[key]
        g["total"] += 1
        # A pipe is "first pass" if no stage has a rework decision. Matched
        # through is_rework() so the Arabic spellings count too — comparing the
        # raw English strings missed every AR-entered decision.
        has_rework = any(is_rework(s.decision) for s in pipe.stages if s.decision)
        if has_rework:
            g["rework"] += 1
        elif (pipe.final_decision_value or "").upper() == "ACCEPT":
            g["first_pass"] += 1

    rows = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        rows.append(
            {
                "group": key,
                "total": g["total"],
                "first_pass": g["first_pass"],
                "rework": g["rework"],
                "rft_pct": round(g["first_pass"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
            }
        )
    return {
        "rows": rows,
        "summary": {
            "total_pipes": sum(r["total"] for r in rows),
            "total_first_pass": sum(r["first_pass"] for r in rows),
            "overall_rft": round(
                sum(r["first_pass"] for r in rows)
                / sum(r["total"] for r in rows)
                * 100,
                1,
            )
            if sum(r["total"] for r in rows)
            else 0,
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 6. Defect Analysis Advanced (with Pareto)
# ---------------------------------------------------------------------------


def defect_analysis(filters):
    query = (
        db.session.query(PipeStage).join(Pipe).filter(PipeStage.has_defect.is_(True))
    )
    if filters.get("date_from"):
        query = query.filter(Pipe.production_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(Pipe.production_date <= filters["date_to"])
    if filters.get("diameter"):
        query = query.filter(Pipe.diameter == filters["diameter"])

    stages = query.all()

    by_stage = defaultdict(int)
    by_type = defaultdict(int)
    by_dn = defaultdict(lambda: defaultdict(int))  # dn -> defect_type -> count

    for s in stages:
        by_stage[s.stage_name] += 1
        dtype = s.defect_type or "Other"
        by_type[dtype] += 1
        dn_key = f"DN{s.pipe.diameter}" if s.pipe.diameter else "Unknown"
        by_dn[dn_key][dtype] += 1

    # Pareto: sorted by count descending with cumulative %
    total = sum(by_type.values())
    pareto = []
    cumulative = 0
    for defect, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        cumulative += count
        pareto.append(
            {
                "defect": defect,
                "count": count,
                "pct": round(count / total * 100, 1) if total else 0,
                "cumulative_pct": round(cumulative / total * 100, 1) if total else 0,
            }
        )

    return {
        "rows": pareto,
        "by_stage": dict(by_stage),
        "by_dn_matrix": {k: dict(v) for k, v in by_dn.items()},
        "summary": {
            "total_defects": total,
            "unique_defect_types": len(by_type),
            "affected_stages": len(by_stage),
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 7. Heat / Ladle Traceability
# ---------------------------------------------------------------------------


def heat_traceability(filters):
    query = ChemicalAnalysis.query
    if filters.get("date_from"):
        query = query.filter(ChemicalAnalysis.test_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(ChemicalAnalysis.test_date <= filters["date_to"])

    ladles = query.order_by(ChemicalAnalysis.test_date.desc()).all()
    rows = []
    export_rows = []

    def _latest_active_mech(pipe, tests):
        linked = [
            t
            for t in tests
            if (pipe.id and t.pipe_id == pipe.id)
            or (pipe.pipe_code and t.pipe_code == pipe.pipe_code)
        ]
        linked.sort(key=lambda t: ((t.test_date or date.min), t.id or 0), reverse=True)
        return linked[0] if linked else None

    for ladle in ladles:
        pipes = list(ladle.pipes.order_by(Pipe.arrange_pipe.asc(), Pipe.id.asc()).all())
        mech_tests = list(
            ladle.mechanical_tests.filter(MechanicalTest.status == "ACTIVE")
            .order_by(MechanicalTest.test_date.desc(), MechanicalTest.id.desc())
            .all()
        )
        rows.append(
            {
                "ladle_id": ladle.ladle_id,
                "test_date": ladle.test_date,
                "furnace": ladle.furnace.furnace_code if ladle.furnace else "",
                "decision": ladle.decision,
                "carbon": ladle.carbon,
                "silicon": ladle.silicon,
                "magnesium": ladle.magnesium,
                "pipe_count": len(pipes),
                "accepted": sum(
                    1 for p in pipes if (p.final_decision_value or "") == "ACCEPT"
                ),
                "rejected": sum(
                    1 for p in pipes if (p.final_decision_value or "") == "REJECT"
                ),
                "mech_tests_count": len(mech_tests),
                "mech_passes": sum(1 for m in mech_tests if m.decision == "ACCEPT"),
                "mech_fails": sum(1 for m in mech_tests if m.decision == "REJECT"),
            }
        )

        for pipe in pipes:
            order = pipe.production_order
            stage_map = {
                stage.stage_name: stage
                for stage in pipe.stages.order_by(PipeStage.id.asc()).all()
            }
            mech = _latest_active_mech(pipe, mech_tests)
            row = {
                "ladle_id": ladle.ladle_id,
                "chemical_test_date": ladle.test_date,
                "chemical_decision": ladle.decision,
                "chemical_reason": ladle.reason,
                "carbon": ladle.carbon,
                "silicon": ladle.silicon,
                "magnesium": ladle.magnesium,
                "manganese": ladle.manganese,
                "phosphorus": ladle.phosphorus,
                "sulfur": ladle.sulfur,
                "furnace": ladle.furnace.furnace_code if ladle.furnace else "",
                "production_date": pipe.production_date,
                "production_order": order.order_number
                if order
                else pipe.manufacturing_order,
                "sales_order": order.sales_number if order else "",
                "customer": order.customer_name if order else "",
                "product_code": order.product_code if order else "",
                "product_description": order.product_description if order else "",
                "pipe_code": pipe.pipe_code,
                "no_code": pipe.no_code,
                "arrange_pipe": pipe.arrange_pipe,
                "diameter": pipe.diameter,
                "pipe_class": pipe.pipe_class,
                "mold_number": pipe.mold_number,
                "iso_weight": pipe.iso_weight,
                "actual_weight": pipe.actual_weight,
                "mechanical_test_role": pipe.mechanical_test_role,
                "pipe_lab_decision": pipe.lab_decision,
                "pipe_lab_decision_reason": pipe.lab_decision_reason,
                "final_decision": pipe.final_decision_value,
                "current_stage": pipe.current_stage,
                "finish_bundle_number": (
                    stage_map.get(ProductionStage.name_for_code("finish")).bundle_number
                    if stage_map.get(ProductionStage.name_for_code("finish"))
                    else ""
                ),
                "delivery_sales_order": (
                    stage_map.get(ProductionStage.name_for_code("delivery")).sales_order
                    if stage_map.get(ProductionStage.name_for_code("delivery"))
                    else ""
                ),
                "delivery_customer": (
                    stage_map.get(
                        ProductionStage.name_for_code("delivery")
                    ).delivery_customer
                    if stage_map.get(ProductionStage.name_for_code("delivery"))
                    else ""
                ),
                "delivery_receipt": (
                    stage_map.get(
                        ProductionStage.name_for_code("delivery")
                    ).delivery_receipt
                    if stage_map.get(ProductionStage.name_for_code("delivery"))
                    else ""
                ),
                "delivery_bundle_number": (
                    stage_map.get(
                        ProductionStage.name_for_code("delivery")
                    ).bundle_number
                    if stage_map.get(ProductionStage.name_for_code("delivery"))
                    else ""
                ),
                "mechanical_test_date": mech.test_date if mech else None,
                "mechanical_decision": mech.decision if mech else "",
                "mechanical_reason": mech.reason if mech else "",
                "tensile_strength": mech.tensile_strength if mech else None,
                "tensile_mpa": mech.tensile_mpa if mech else None,
                "elongation": mech.elongation if mech else None,
                "hardness": mech.hardness if mech else None,
                "nodularity_percent": mech.nodularity_percent if mech else None,
                "carbides": mech.carbides if mech else None,
            }

            for stage_name in Pipe.STAGES:
                stage = stage_map.get(stage_name)
                key = stage_name.lower().replace(" ", "_")
                row[f"{key}_date"] = stage.stage_date if stage else None
                row[f"{key}_decision"] = stage.decision if stage else ""
                row[f"{key}_defect"] = (
                    stage.defect_type if stage and stage.has_defect else ""
                )
                row[f"{key}_machine"] = (
                    stage.machine.machine_code if stage and stage.machine else ""
                )

            export_rows.append(row)

    return {
        "rows": rows,
        "export_rows": export_rows,
        "summary": {"ladles": len(rows), "pipes": len(export_rows)},
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 8. Mechanical Statistical
# ---------------------------------------------------------------------------


def mechanical_statistical(filters):
    query = MechanicalTest.query.filter(MechanicalTest.status == "ACTIVE")
    if filters.get("date_from"):
        query = query.filter(MechanicalTest.test_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(MechanicalTest.test_date <= filters["date_to"])
    if filters.get("diameter"):
        query = query.filter(MechanicalTest.diameter == filters["diameter"])

    tests = query.all()

    tensile_kgf = [t.tensile_strength for t in tests if t.tensile_strength is not None]
    tensile_mpa = [t.tensile_mpa for t in tests if t.tensile_mpa is not None]
    elongation = [t.elongation for t in tests if t.elongation is not None]
    hardness = [t.hardness for t in tests if t.hardness is not None]
    nodularity = [
        t.nodularity_percent for t in tests if t.nodularity_percent is not None
    ]
    carbides = [t.carbides for t in tests if t.carbides is not None]

    # Histogram buckets for tensile (10 bins)
    def histogram(values, bins=10):
        if not values:
            return []
        lo, hi = min(values), max(values)
        if lo == hi:
            return [{"range": f"{lo:.1f}", "count": len(values)}]
        width = (hi - lo) / bins
        buckets = [0] * bins
        for v in values:
            idx = min(int((v - lo) / width), bins - 1)
            buckets[idx] += 1
        return [
            {"range": f"{lo + i * width:.1f}-{lo + (i + 1) * width:.1f}", "count": c}
            for i, c in enumerate(buckets)
        ]

    return {
        "stats": {
            "tensile_kgf": stat_summary(tensile_kgf),
            "tensile_mpa": stat_summary(tensile_mpa),
            "elongation": stat_summary(elongation),
            "hardness": stat_summary(hardness),
            "nodularity": stat_summary(nodularity),
            "carbides": stat_summary(carbides),
        },
        "histogram": histogram(tensile_mpa),
        "tests": len(tests),
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 9-11. Performance reports (Stage, Machine, Mold)
# ---------------------------------------------------------------------------


def stage_performance(filters):
    query = db.session.query(PipeStage).join(Pipe)
    if filters.get("date_from"):
        query = query.filter(Pipe.production_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(Pipe.production_date <= filters["date_to"])
    stages = query.all()

    grouped = defaultdict(
        lambda: {"total": 0, "accept": 0, "reject": 0, "hold": 0, "defects": 0}
    )
    for s in stages:
        g = grouped[s.stage_name]
        g["total"] += 1
        decision = (s.decision or "").lower()
        if "accept" in decision:
            g["accept"] += 1
        elif "reject" in decision:
            g["reject"] += 1
        elif "hold" in decision:
            g["hold"] += 1
        if s.has_defect:
            g["defects"] += 1

    rows = []
    for name in Pipe.STAGES:
        g = grouped.get(
            name, {"total": 0, "accept": 0, "reject": 0, "hold": 0, "defects": 0}
        )
        rows.append(
            {
                "stage": name,
                "total": g["total"],
                "accept": g["accept"],
                "reject": g["reject"],
                "hold": g["hold"],
                "defects": g["defects"],
                "accept_pct": round(g["accept"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
                "reject_pct": round(g["reject"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
            }
        )
    return {"rows": rows, "summary": {"stages": len(rows)}, "filters": filters}


def machine_performance(filters):
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()
    grouped = defaultdict(lambda: {"total": 0, "accept": 0, "reject": 0, "defects": 0})
    for pipe in pipes:
        key = pipe_machine_code(pipe) or "Unknown"
        g = grouped[key]
        g["total"] += 1
        fd = (pipe.final_decision_value or "").upper()
        if fd == "ACCEPT":
            g["accept"] += 1
        elif fd == "REJECT":
            g["reject"] += 1
        g["defects"] += sum(1 for s in pipe.stages if s.has_defect)
    rows = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        rows.append(
            {
                "machine": key,
                "total": g["total"],
                "accept": g["accept"],
                "reject": g["reject"],
                "defects": g["defects"],
                "accept_pct": round(g["accept"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
                "reject_pct": round(g["reject"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
            }
        )
    return {"rows": rows, "summary": {"machines": len(rows)}, "filters": filters}


def mold_performance(filters):
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()
    grouped = defaultdict(lambda: {"total": 0, "accept": 0, "reject": 0})
    for pipe in pipes:
        key = pipe.mold_number or "Unknown"
        g = grouped[key]
        g["total"] += 1
        fd = (pipe.final_decision_value or "").upper()
        if fd == "ACCEPT":
            g["accept"] += 1
        elif fd == "REJECT":
            g["reject"] += 1
    rows = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        rows.append(
            {
                "mold": key,
                "total": g["total"],
                "accept": g["accept"],
                "reject": g["reject"],
                "accept_pct": round(g["accept"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
                "reject_pct": round(g["reject"] / g["total"] * 100, 1)
                if g["total"]
                else 0,
            }
        )
    return {"rows": rows, "summary": {"molds": len(rows)}, "filters": filters}


# ---------------------------------------------------------------------------
# 12. Weight Saving
# ---------------------------------------------------------------------------


def weight_saving(filters):
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()

    grouped = defaultdict(lambda: {"iso_weight": 0.0, "actual_weight": 0.0, "count": 0})
    excluded = 0
    via_product = 0
    for pipe in pipes:
        planned, source = planned_weight(pipe)
        if source is None:
            # Neither iso_weight nor a product standard — exclude from the
            # aggregates and count for the coverage note (never show garbage
            # negatives from a zero planned weight).
            excluded += 1
            continue
        if source == "product":
            via_product += 1
        key = group_key(pipe, filters["group_by"])
        g = grouped[key]
        g["iso_weight"] += planned
        g["actual_weight"] += float(pipe.actual_weight or 0)
        g["count"] += 1

    rows = []
    total_iso = 0
    total_act = 0
    for key in sorted(grouped.keys()):
        g = grouped[key]
        iso = g["iso_weight"]
        act = g["actual_weight"]
        saving = iso - act
        total_iso += iso
        total_act += act
        rows.append(
            {
                "group": key,
                "count": g["count"],
                "iso_weight": round(iso, 2),
                "actual_weight": round(act, 2),
                "saving": round(saving, 2),
                "saving_pct": round(saving / iso * 100, 2) if iso else 0,
            }
        )
    total_saving = total_iso - total_act
    return {
        "rows": rows,
        "summary": {
            "total_iso": round(total_iso, 2),
            "total_actual": round(total_act, 2),
            "total_saving": round(total_saving, 2),
            "total_saving_pct": round(total_saving / total_iso * 100, 2)
            if total_iso
            else 0,
            "via_product_standard": via_product,
            "excluded_no_weight": excluded,
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 13. Annealing Hourly Entry
# ---------------------------------------------------------------------------


def annealing_hourly(filters):
    query = (
        db.session.query(PipeStage)
        .join(Pipe)
        .filter(
            PipeStage.stage_name == ProductionStage.name_for_code("annealing"),
            PipeStage.stage_time.isnot(None),
        )
    )
    if filters.get("date_from"):
        query = query.filter(PipeStage.stage_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(PipeStage.stage_date <= filters["date_to"])
    if filters.get("production_order_id"):
        query = query.filter(Pipe.production_order_id == filters["production_order_id"])

    stages = query.all()

    by_hour = defaultdict(list)
    for s in stages:
        if not s.stage_time:
            continue
        hour = s.stage_time.hour
        by_hour[hour].append(
            {
                "pipe_code": s.pipe.pipe_code or s.pipe.no_code,
                "ladle_id": s.pipe.ladle_id,
                "dn": s.pipe.diameter,
                "stage_date": s.stage_date.isoformat() if s.stage_date else "",
                "stage_time": s.stage_time.strftime("%H:%M"),
            }
        )

    rows = []
    for hour in range(24):
        pipes = by_hour.get(hour, [])
        rows.append(
            {
                "hour": f"{hour:02d}:00-{(hour + 1) % 24:02d}:00",
                "pipes": pipes,
                "count": len(pipes),
            }
        )
    return {
        "rows": rows,
        "summary": {
            "total_pipes": sum(len(p) for p in by_hour.values()),
            "active_hours": len(by_hour),
        },
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# 14. Management Dashboard (KPI summary)
# ---------------------------------------------------------------------------


def management_dashboard(filters):
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.all()
    total = len(pipes)
    accepted = sum(1 for p in pipes if (p.final_decision_value or "") == "ACCEPT")
    rejected = sum(1 for p in pipes if (p.final_decision_value or "") == "REJECT")
    hold = sum(1 for p in pipes if (p.final_decision_value or "") == "HOLD")

    # Top defects
    defect_counts = defaultdict(int)
    for p in pipes:
        for s in p.stages:
            if s.has_defect and s.defect_type:
                defect_counts[s.defect_type] += 1
    top_defects = sorted(defect_counts.items(), key=lambda x: x[1], reverse=True)[:3]

    # Best line = machine with highest accept %
    machine_res = machine_performance(filters)["rows"]
    best_machine = (
        max(machine_res, key=lambda r: r["accept_pct"], default=None)
        if machine_res
        else None
    )

    # Worst stage = stage with highest reject %
    stage_res = stage_performance(filters)["rows"]
    worst_stage = (
        max(stage_res, key=lambda r: r["reject_pct"], default=None)
        if stage_res
        else None
    )

    return {
        "kpis": {
            "total_production": total,
            "accepted": accepted,
            "rejected": rejected,
            "hold": hold,
            "rft_pct": round(accepted / total * 100, 1) if total else 0,
            "scrap_pct": round(rejected / total * 100, 1) if total else 0,
        },
        "top_defects": [{"defect": d, "count": c} for d, c in top_defects],
        "best_machine": best_machine,
        "worst_stage": worst_stage,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Shift Engineer report (reports section)
# ---------------------------------------------------------------------------


def _shift_pipe_query(filters):
    """Pipe query for shift reports — date/shift/engineer aware, no default
    date floor (so historical shifts are visible, not just today)."""
    query = Pipe.query
    if filters.get("date_from"):
        query = query.filter(Pipe.production_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(Pipe.production_date <= filters["date_to"])
    if filters.get("shift"):
        query = query.filter(Pipe.shift == filters["shift"])
    if filters.get("shift_engineer"):
        query = query.filter(Pipe.shift_engineer == filters["shift_engineer"])
    return query


def shift_engineer_report(filters):
    """Per-shift overview: KPIs, pipes waiting for lab decision, defects, and
    the full pipe list — filterable by date range, shift, and engineer."""
    pipes = (
        _shift_pipe_query(filters)
        .order_by(Pipe.production_date.desc(), Pipe.no_code)
        .all()
    )

    total = len(pipes)
    accepted = sum(
        1 for p in pipes if (p.final_decision_value or "").upper() == "ACCEPT"
    )
    rejected = sum(
        1 for p in pipes if (p.final_decision_value or "").upper() == "REJECT"
    )
    pending = total - accepted - rejected

    waiting = [p for p in pipes if (p.lab_decision or "") == "WAITING"]

    pipe_ids = [p.id for p in pipes]
    if pipe_ids:
        defects = PipeStage.query.filter(
            PipeStage.pipe_id.in_(pipe_ids),
            PipeStage.has_defect.is_(True),
        ).all()
    else:
        defects = []

    # Distinct engineers for the filter dropdown (across all data, not filtered)
    engineers = [
        r[0]
        for r in db.session.query(Pipe.shift_engineer)
        .filter(Pipe.shift_engineer.isnot(None), Pipe.shift_engineer != "")
        .distinct()
        .order_by(Pipe.shift_engineer)
        .all()
    ]

    export_rows = [
        {
            "code": p.no_code,
            "dn": f"DN{p.diameter}" if p.diameter else "",
            "class": p.pipe_class or "",
            "ladle": p.ladle_id or "",
            "mold": p.mold_number or "",
            "shift": p.shift if p.shift is not None else "",
            "engineer": p.shift_engineer or "",
            "production_date": p.production_date.isoformat()
            if p.production_date
            else "",
            "lab_decision": p.lab_decision or "",
            "final_decision": p.final_decision_value or "",
        }
        for p in pipes
    ]

    return {
        "stats": {
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "pending": pending,
        },
        "pipes": pipes,
        "waiting": waiting,
        "defects": defects,
        "engineers": engineers,
        "rows": export_rows,
        "export_rows": export_rows,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Shift Engineer COMPARISON report — group BY engineer, compare side by side
# ---------------------------------------------------------------------------

# Engineer/machine attribution facts (verified against prod data):
#   - Pipe.shift_engineer is the only populated attribution field
#     (PipeStage.shift_responsible is 100% NULL) -> attribute by pipe engineer.
#   - lab_decision is the live decision signal (final_decision_value ~empty).
#   - The production machine lives on the CCM PipeStage, not on Pipe.machine_id
#     (which is always NULL).
_UNSPECIFIED_ENGINEER = "غير محدد"
_UNSPECIFIED_CUSTOMER = "غير محدد"
_UNSPECIFIED_ORDER = "بدون أمر"
_REJECT_DECISIONS = ("REJECT", "BLOCKED")


def _ccm_machine_code(pipe):
    """Machine code from the pipe's CCM (casting) stage, or None."""
    ccm_name = ProductionStage.name_for_code("ccm")
    for s in pipe.stages:
        if s.stage_name == ccm_name and s.machine:
            return s.machine.machine_code
    return None


def shift_engineer_comparison(filters):
    """Per-engineer comparison: production, accept/reject/hold/pending,
    defect rate, saving weight, and a per-CCM-machine breakdown — so shift
    engineers can be ranked against each other. Date/shift/engineer aware via
    the same _shift_pipe_query the basic shift report uses."""
    pipes = (
        _shift_pipe_query(filters)
        .order_by(Pipe.production_date.desc(), Pipe.no_code)
        .all()
    )

    def _new_group():
        return {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "hold": 0,
            "pending": 0,
            "defect_pipes": 0,
            "iso_sum": 0.0,
            "actual_sum": 0.0,
            "weight_pipes": 0,
            "by_machine": defaultdict(int),
        }

    by_engineer = defaultdict(_new_group)
    machine_grp = defaultdict(lambda: {"total": 0, "defects": 0, "rejected": 0})
    weighted_pipes = 0  # pipes that carry a recorded engineer (non-empty)

    for p in pipes:
        eng = (p.shift_engineer or "").strip() or _UNSPECIFIED_ENGINEER
        if (p.shift_engineer or "").strip():
            weighted_pipes += 1
        g = by_engineer[eng]
        g["total"] += 1

        ld = (p.lab_decision or "").upper()
        is_reject = ld in _REJECT_DECISIONS
        if ld == "ACCEPT":
            g["accepted"] += 1
        elif is_reject:
            g["rejected"] += 1
        elif ld == "HOLD":
            g["hold"] += 1
        else:
            g["pending"] += 1

        has_defect = any(s.has_defect for s in p.stages)
        if has_defect:
            g["defect_pipes"] += 1

        # Saving weight needs BOTH a real standard (iso) and actual weight.
        # In prod, iso_weight is frequently stored as 0.0 (never entered) — a
        # zero standard would yield a meaningless huge-negative "saving", so we
        # require both > 0 and otherwise treat the pipe as having no weight data.
        iso = float(p.iso_weight or 0)
        act = float(p.actual_weight or 0)
        if iso > 0 and act > 0:
            g["iso_sum"] += iso
            g["actual_sum"] += act
            g["weight_pipes"] += 1

        machine = _ccm_machine_code(p)
        if machine:
            g["by_machine"][machine] += 1
            mg = machine_grp[machine]
            mg["total"] += 1
            if has_defect:
                mg["defects"] += 1
            if is_reject:
                mg["rejected"] += 1

    engineer_rows = []
    for eng in sorted(by_engineer, key=lambda e: by_engineer[e]["total"], reverse=True):
        g = by_engineer[eng]
        total = g["total"]
        saving = g["iso_sum"] - g["actual_sum"]
        engineer_rows.append(
            {
                "engineer": eng,
                "total": total,
                "accepted": g["accepted"],
                "rejected": g["rejected"],
                "hold": g["hold"],
                "pending": g["pending"],
                "defect_pipes": g["defect_pipes"],
                "defect_rate": round(g["defect_pipes"] / total * 100, 1)
                if total
                else 0,
                "reject_rate": round(g["rejected"] / total * 100, 1) if total else 0,
                "saving": round(saving, 2),
                "saving_pct": round(saving / g["iso_sum"] * 100, 2)
                if g["iso_sum"]
                else 0,
                "weight_pipes": g["weight_pipes"],
                "by_machine": dict(g["by_machine"]),
            }
        )

    machine_rows = []
    for code in sorted(machine_grp):
        mg = machine_grp[code]
        machine_rows.append(
            {
                "machine": code,
                "total": mg["total"],
                "defects": mg["defects"],
                "rejected": mg["rejected"],
                "defect_rate": round(mg["defects"] / mg["total"] * 100, 1)
                if mg["total"]
                else 0,
                "reject_rate": round(mg["rejected"] / mg["total"] * 100, 1)
                if mg["total"]
                else 0,
            }
        )

    # cross-tab axes: engineer (rows) x machine (cols) -> count
    machine_codes = [m["machine"] for m in machine_rows]

    # distinct engineers for the filter dropdown (all data, like basic report)
    engineers = [
        r[0]
        for r in db.session.query(Pipe.shift_engineer)
        .filter(Pipe.shift_engineer.isnot(None), Pipe.shift_engineer != "")
        .distinct()
        .order_by(Pipe.shift_engineer)
        .all()
    ]

    total_pipes = len(pipes)
    # flat rows for the generic Excel exporter
    export_rows = [
        {
            "engineer": r["engineer"],
            "pipes": r["total"],
            "accepted": r["accepted"],
            "rejected": r["rejected"],
            "hold": r["hold"],
            "pending": r["pending"],
            "defect_pipes": r["defect_pipes"],
            "defect_rate_%": r["defect_rate"],
            "reject_rate_%": r["reject_rate"],
            "saving_kg": r["saving"],
            "saving_%": r["saving_pct"],
            "weight_coverage": f"{r['weight_pipes']}/{r['total']}",
        }
        for r in engineer_rows
    ]

    # Only crown a "worst defect" / "best saving" when there is real signal —
    # otherwise (all zero) these highlights would arbitrarily flag whichever
    # row sorts first, which is misleading.
    worst_defect = max(engineer_rows, key=lambda r: r["defect_rate"], default=None)
    if worst_defect and worst_defect["defect_rate"] <= 0:
        worst_defect = None
    best_saving = max(engineer_rows, key=lambda r: r["saving"], default=None)
    if best_saving and best_saving["saving"] <= 0:
        best_saving = None

    return {
        "engineer_rows": engineer_rows,
        "machine_rows": machine_rows,
        "machine_codes": machine_codes,
        "engineers": engineers,
        "summary": {
            "engineers": len(
                [r for r in engineer_rows if r["engineer"] != _UNSPECIFIED_ENGINEER]
            ),
            "machines": len(machine_rows),
            "worst_defect_engineer": worst_defect["engineer"] if worst_defect else None,
            "best_saving_engineer": best_saving["engineer"] if best_saving else None,
        },
        "coverage": {
            "total_pipes": total_pipes,
            "with_engineer": weighted_pipes,
            "pct": round(weighted_pipes / total_pipes * 100, 1) if total_pipes else 0,
        },
        "rows": export_rows,
        "export_rows": export_rows,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Delivery comparison — same "compare side by side" idea as the engineer report,
# but over delivered pipes, grouped by customer / engineer / sales order.
# Shared by the /stages/deliveries panel and the /reports/delivery-comparison
# report so both render identical numbers.
# ---------------------------------------------------------------------------


def summarize_deliveries(delivery_stages):
    """Build by-customer / by-engineer / by-sales-order comparison tables from
    a list of Delivery PipeStage records (already filtered by the caller).

    Pure aggregation — no DB query — so callers control the filtered set."""
    by_customer = defaultdict(lambda: {"pipes": 0, "orders": set(), "bundles": set()})
    by_engineer = defaultdict(lambda: {"pipes": 0, "customers": set()})
    by_order = defaultdict(lambda: {"pipes": 0, "customer": None, "bundles": set()})

    all_customers, all_orders = set(), set()

    for s in delivery_stages:
        customer = (s.delivery_customer or "").strip() or _UNSPECIFIED_CUSTOMER
        order = (s.sales_order or "").strip() or _UNSPECIFIED_ORDER
        bundle = (s.bundle_number or "").strip()
        pipe = s.pipe
        engineer = (
            (pipe.shift_engineer if pipe else None) or ""
        ).strip() or _UNSPECIFIED_ENGINEER

        all_customers.add(customer)
        all_orders.add(order)

        c = by_customer[customer]
        c["pipes"] += 1
        c["orders"].add(order)
        if bundle:
            c["bundles"].add(bundle)

        e = by_engineer[engineer]
        e["pipes"] += 1
        e["customers"].add(customer)

        o = by_order[order]
        o["pipes"] += 1
        o["customer"] = o["customer"] or customer
        if bundle:
            o["bundles"].add(bundle)

    customer_rows = [
        {
            "customer": k,
            "pipes": v["pipes"],
            "orders": len(v["orders"]),
            "bundles": len(v["bundles"]),
        }
        for k in sorted(
            by_customer, key=lambda k: by_customer[k]["pipes"], reverse=True
        )
        for v in [by_customer[k]]
    ]
    engineer_rows = [
        {"engineer": k, "pipes": v["pipes"], "customers": len(v["customers"])}
        for k in sorted(
            by_engineer, key=lambda k: by_engineer[k]["pipes"], reverse=True
        )
        for v in [by_engineer[k]]
    ]
    order_rows = [
        {
            "sales_order": k,
            "pipes": v["pipes"],
            "customer": v["customer"],
            "bundles": len(v["bundles"]),
        }
        for k in sorted(by_order, key=lambda k: by_order[k]["pipes"], reverse=True)
        for v in [by_order[k]]
    ]

    return {
        "by_customer": customer_rows,
        "by_engineer": engineer_rows,
        "by_order": order_rows,
        "totals": {
            "pipes": len(delivery_stages),
            "customers": len(all_customers),
            "orders": len(all_orders),
        },
    }


def delivery_comparison(filters):
    """Report-facing wrapper: pull Delivery stages for the date/customer window
    (reusing _delivery_stage_query) and summarize them. export_rows flattens the
    per-customer table for the generic Excel exporter."""
    stages = _delivery_stage_query(filters).all()
    summary = summarize_deliveries(stages)
    export_rows = [
        {
            "customer": r["customer"],
            "pipes": r["pipes"],
            "orders": r["orders"],
            "bundles": r["bundles"],
        }
        for r in summary["by_customer"]
    ]
    return {
        **summary,
        "rows": export_rows,
        "export_rows": export_rows,
        "summary": summary["totals"],
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Delivery overview (per heat / per batch / per order) — "what each heat made"
# ---------------------------------------------------------------------------


def _delivered_pipe_ids():
    """Set of pipe ids that have a completed Delivery stage (delivery_date set)."""
    rows = (
        db.session.query(PipeStage.pipe_id)
        .filter(
            PipeStage.stage_name == ProductionStage.name_for_code("delivery"),
            PipeStage.delivery_date.isnot(None),
        )
        .all()
    )
    return {r[0] for r in rows}


def _delivery_stage_query(filters):
    query = PipeStage.query.filter(
        PipeStage.stage_name == ProductionStage.name_for_code("delivery")
    )
    if filters.get("date_from"):
        query = query.filter(PipeStage.delivery_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(PipeStage.delivery_date <= filters["date_to"])
    if filters.get("customer"):
        query = query.filter(
            PipeStage.delivery_customer.ilike(f"%{filters['customer']}%")
        )
    return query


def _delivery_heat_groups(filters):
    """Per heat/ladle: produced, accepted, rejected, pending, delivered, weight.
    Keyed on production_date so it answers 'what did each heat produce'."""
    query = Pipe.query
    if filters.get("date_from"):
        query = query.filter(Pipe.production_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(Pipe.production_date <= filters["date_to"])
    pipes = query.all()

    delivered_ids = _delivered_pipe_ids()
    grouped = {}
    for p in pipes:
        key = p.ladle_id or "No ladle"
        g = grouped.setdefault(
            key,
            {
                "key": key,
                "produced": 0,
                "accepted": 0,
                "rejected": 0,
                "pending": 0,
                "delivered": 0,
                "weight": 0.0,
                "dns": set(),
                "customers": set(),
            },
        )
        g["produced"] += 1
        g["weight"] += float(p.actual_weight or 0)
        if p.diameter:
            g["dns"].add(f"DN{p.diameter}")
        fd = (p.final_decision_value or "").upper()
        if fd == "ACCEPT":
            g["accepted"] += 1
        elif fd == "REJECT":
            g["rejected"] += 1
        else:
            g["pending"] += 1
        if p.id in delivered_ids:
            g["delivered"] += 1

    groups = []
    for g in grouped.values():
        g["weight"] = round(g["weight"], 2)
        g["dns"] = ", ".join(sorted(g["dns"]))
        g["customers"] = ", ".join(sorted(c for c in g["customers"] if c))
        groups.append(g)
    groups.sort(key=lambda x: x["key"])
    return groups


def _delivery_grouped_by(filters, key_fn, label):
    """Group Delivery stages by an arbitrary key (batch / order / customer)."""
    deliveries = _delivery_stage_query(filters).all()
    grouped = {}
    for d in deliveries:
        key = key_fn(d) or f"No {label}"
        g = grouped.setdefault(
            key,
            {
                "key": key,
                "pipes": 0,
                "weight": 0.0,
                "dns": set(),
                "customer": d.delivery_customer or "",
                "sales_order": d.sales_order or "",
                "bundle": d.bundle_number or "",
                "last_delivery": None,
            },
        )
        g["pipes"] += 1
        p = d.pipe
        if p:
            g["weight"] += float(p.actual_weight or 0)
            if p.diameter:
                g["dns"].add(f"DN{p.diameter}")
        if d.delivery_date and (
            g["last_delivery"] is None or d.delivery_date > g["last_delivery"]
        ):
            g["last_delivery"] = d.delivery_date

    groups = []
    for g in grouped.values():
        g["weight"] = round(g["weight"], 2)
        g["dns"] = ", ".join(sorted(g["dns"]))
        g["last_delivery"] = (
            g["last_delivery"].isoformat() if g["last_delivery"] else ""
        )
        groups.append(g)
    groups.sort(key=lambda x: x["key"])
    return groups


def _delivery_flat_rows(filters):
    deliveries = (
        _delivery_stage_query(filters).order_by(PipeStage.delivery_date.desc()).all()
    )
    rows = []
    for d in deliveries:
        p = d.pipe
        rows.append(
            {
                "delivery_date": d.delivery_date.isoformat() if d.delivery_date else "",
                "pipe_code": (p.pipe_code or p.no_code) if p else "",
                "dn": f"DN{p.diameter}" if p and p.diameter else "",
                "sales_order": d.sales_order or "",
                "customer": d.delivery_customer or "",
                "receipt": d.delivery_receipt or "",
                "bundle": d.bundle_number or "",
                "weight": round(float(p.actual_weight or 0), 2) if p else 0,
                "final_decision": (p.final_decision_value or "") if p else "",
            }
        )
    return rows


def delivery_overview(filters):
    """Delivery report with selectable grouping:
    heat  -> per ladle: produced / accepted / rejected / delivered (what each heat made)
    batch -> per bundle/delivery note
    order -> per sales order
    flat  -> per delivered pipe (raw list)
    """
    group_by = filters.get("group_by") or "heat"

    if group_by == "heat":
        groups = _delivery_heat_groups(filters)
    elif group_by == "batch":
        groups = _delivery_grouped_by(filters, lambda d: d.bundle_number, "bundle")
    elif group_by == "order":
        groups = _delivery_grouped_by(filters, lambda d: d.sales_order, "order")
    else:
        groups = []

    rows = _delivery_flat_rows(filters)

    summary = {
        "groups": len(groups),
        "delivered_pipes": len(rows),
        "total_weight": round(sum(r["weight"] for r in rows), 2),
    }

    return {
        "group_by": group_by,
        "groups": groups,
        "rows": rows,
        "export_rows": rows,
        "summary": summary,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Stage measurements — surface the console-popup data (length, thickness,
# temperature, per-meter profile, CCM dimension grid) that no report showed
# before. One row per pipe × stage that carries any measurement.
# ---------------------------------------------------------------------------


# Column order for the two JSON profiles that have a fixed shape. Kept local
# (rather than imported from app.routes.stages) so the service layer stays
# free of route imports; both mirror the popup layout.
OVALITY_POINT_ORDER = ["ID"] + [f"D{n}" for n in range(1, 16)]
VISUAL_CHECK_KEYS = [
    "marking",
    "ovality",
    "straightness",
    "internal_finish",
    "external_finish",
]


def _fmt_num(v):
    """Trim a float to its shortest exact representation (3.50 -> 3.5)."""
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _flat_thickness(profile):
    """Lining thickness -> one string.

    Cement is measured X/Y at the socket and the spigot, so it flattens as
    'cement socket_1: 3.5/3.9' per position plus the overall average. Coating
    is measured once at each metre and flattens as 'coating: 70/69/...'. A
    per-metre cement list from before the grid is still read where it exists.
    """
    if not profile:
        return ""
    parts = []
    for layer in lining_points_service.LAYERS:
        points = profile.get(lining_points_service.points_key(layer)) or {}
        for key, _end, _n in lining_points_service.POINTS:
            pv = points.get(key) or {}
            if pv.get("x") is None and pv.get("y") is None:
                continue
            parts.append("%s %s: %s/%s"
                         % (layer, key, _fmt_num(pv.get("x")),
                            _fmt_num(pv.get("y"))))
        overall = profile.get(lining_points_service.average_key(layer))
        if overall is not None:
            parts.append("%s avg: %s" % (layer, _fmt_num(overall)))
    # Coating is per metre; a cement list only exists on rows written before
    # the socket/spigot grid.
    for layer in ("cement", "coating"):
        vals = profile.get(layer)
        if vals and any(v is not None for v in vals):
            parts.append(f"{layer}: " + "/".join(_fmt_num(v) for v in vals))
    return " | ".join(parts)


def _flat_dimension(profile):
    """CCM dimension profile -> one string.

    Covers all three sections a profile can carry: the wall-thickness grid
    (positions since 2026-08-22, flat samples before that), the diameter
    sample rows, and the TA 1012 symbol readings. Position labels are kept so
    an exported row still says which metre a reading came from.
    """
    if not profile:
        return ""
    parts = []

    thickness = profile.get("thickness") or {}
    positions = thickness.get("positions")
    if not positions and thickness.get("samples"):
        # Fold a legacy flat row so old records export like new ones.
        positions = measurement_stats_service.thickness_matrix(profile)
    for label, vals in (positions or {}).items():
        if vals and any(v is not None for v in vals):
            parts.append(
                f"thickness {label}m: " + ",".join(_fmt_num(v) for v in vals)
            )

    diameter = profile.get("diameter") or {}
    for label, vals in sorted((diameter.get("samples") or {}).items()):
        if vals:
            parts.append(
                f"diameter {label}: " + ",".join(_fmt_num(v) for v in vals)
            )

    symbols = profile.get("symbols") or {}
    for symbol in dimension_standard_service.symbol_keys():
        if symbol in symbols and symbols[symbol] is not None:
            parts.append(f"{symbol}={_fmt_num(symbols[symbol])}")

    return " | ".join(parts)


def _flat_ovality(profile):
    """Annealing ovality -> 'ID X=101.5 Y=99.5 1.0% | D3 ...' (measured points)."""
    if not profile:
        return ""
    points = profile.get("points") or {}
    parts = []
    for name in OVALITY_POINT_ORDER:
        pv = points.get(name)
        if not pv:
            continue
        parts.append(
            f"{name} X={_fmt_num(pv.get('x'))} Y={_fmt_num(pv.get('y'))} "
            f"{_fmt_num(pv.get('ovality'))}%"
        )
    return " | ".join(parts)


def _flat_visual(profile):
    """Finish visual checklist -> 'marking: yes | ovality: no | ...'."""
    if not profile:
        return ""
    return " | ".join(
        f"{key}: " + ("yes" if profile.get(key) else "no")
        for key in VISUAL_CHECK_KEYS
    )


def stage_measurements(filters):
    """Per-pipe, per-stage measurement data entered in the stage console popup.

    Every pipe×stage with at least one of: measurement_value, temperature,
    thickness_profile, dimension_profile, ovality_profile, visual_profile,
    thickness_socket/spigot. The Finish stage's measurement_value is the
    operator-entered length; CCM carries the casting temperature + dimension
    grid; Coating carries the per-meter thickness profile; Annealing carries
    the ovality X/Y/% grid; Finish carries the visual checklist.

    ``rows`` keep the raw JSON profiles so the HTML can render them as grids.
    ``export_rows`` flatten each profile to a single string, since xlsxwriter
    cannot write a dict into a cell.
    """
    query = apply_pipe_filters(Pipe.query, filters)
    pipes = query.order_by(Pipe.production_date.desc(), Pipe.id.desc()).all()
    pipe_ids = [p.id for p in pipes]

    rows = []
    if pipe_ids:
        stages = (
            PipeStage.query.filter(PipeStage.pipe_id.in_(pipe_ids))
            .order_by(PipeStage.pipe_id, PipeStage.id)
            .all()
        )
        pipe_map = {p.id: p for p in pipes}
        for s in stages:
            has_measurement = (
                s.measurement_value is not None
                or s.temperature is not None
                or s.thickness_profile is not None
                or s.dimension_profile is not None
                or s.ovality_profile is not None
                or s.visual_profile is not None
                or s.thickness_socket is not None
                or s.thickness_spigot is not None
            )
            if not has_measurement:
                continue
            p = pipe_map.get(s.pipe_id)
            rows.append(
                {
                    "pipe_code": (p.pipe_code or p.no_code) if p else "",
                    "dn": p.diameter if p else None,
                    "pipe_class": p.pipe_class if p else "",
                    "production_date": (
                        p.production_date.isoformat() if p and p.production_date else ""
                    ),
                    "stage": s.stage_name,
                    "measurement_type": s.measurement_type or "",
                    "measurement_value": s.measurement_value,
                    "temperature": s.temperature,
                    "thickness_socket": s.thickness_socket,
                    "thickness_spigot": s.thickness_spigot,
                    "thickness_profile": s.thickness_profile,
                    "dimension_profile": s.dimension_profile,
                    "ovality_profile": s.ovality_profile,
                    "visual_profile": s.visual_profile,
                    "decision": s.decision or "",
                }
            )

    export_rows = [
        dict(
            r,
            thickness_profile=_flat_thickness(r["thickness_profile"]),
            dimension_profile=_flat_dimension(r["dimension_profile"]),
            ovality_profile=_flat_ovality(r["ovality_profile"]),
            visual_profile=_flat_visual(r["visual_profile"]),
        )
        for r in rows
    ]

    return {
        "rows": rows,
        "export_rows": export_rows,
        "summary": {"measured_rows": len(rows), "pipes": len(pipes)},
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Approval register — pipes with a final decision, attributed to the user who
# approved them (Lab Approval stage approved_by_id), filterable by approver.
# ---------------------------------------------------------------------------


def approval_report(filters):
    """Pipes with a final decision ACCEPT/REJECT, plus the approving user.

    The approver is read from the Lab Approval stage's ``approved_by_id`` (the
    single supervisor decision between Annealing and Zinc), falling back to
    the pipe's ``modified_by``. ``approved_by`` filter narrows to one user.
    """
    query = apply_pipe_filters(Pipe.query, filters)
    if filters.get("approved_by"):
        lab_name = ProductionStage.name_for_code("lab")
        query = query.filter(
            Pipe.stages.any(
                and_(
                    PipeStage.stage_name == lab_name,
                    PipeStage.approved_by_id == filters["approved_by"],
                )
            )
        )
    pipes = query.order_by(Pipe.production_date.desc(), Pipe.id.desc()).all()

    from app.models.user import User

    users = {u.id: u for u in User.query.all()}
    lab_name = ProductionStage.name_for_code("lab")

    rows = []
    for p in pipes:
        fd = (p.final_decision_value or "").upper()
        approver = _pipe_approver(p, users, lab_name)
        rows.append(
            {
                "pipe_code": p.pipe_code or p.no_code,
                "dn": p.diameter,
                "pipe_class": p.pipe_class or "",
                "production_date": (
                    p.production_date.isoformat() if p.production_date else ""
                ),
                "final_decision": fd,
                "lab_decision": p.lab_decision or "",
                "approver": (
                    (approver.full_name or approver.username) if approver else ""
                ),
            }
        )

    accept = sum(1 for r in rows if r["final_decision"] == "ACCEPT")
    reject = sum(1 for r in rows if r["final_decision"] == "REJECT")
    return {
        "rows": rows,
        "export_rows": rows,
        "approvers": [
            {"id": u.id, "name": u.full_name or u.username}
            for u in sorted(users.values(), key=lambda u: u.full_name or u.username)
        ],
        "summary": {
            "total": len(rows),
            "accepted": accept,
            "rejected": reject,
        },
        "filters": filters,
    }


_UNSPECIFIED_APPROVER = "غير محدد"


def _pipe_approver(pipe, users, lab_name):
    """The user who signed this pipe off, by the same rule approval_report uses:
    the Lab Approval stage's approver, else whoever last modified the pipe."""
    lab_stage = pipe.get_stage(lab_name)
    if lab_stage is not None and lab_stage.approved_by_id:
        user = users.get(lab_stage.approved_by_id)
        if user is not None:
            return user
    if pipe.modified_by_id:
        return users.get(pipe.modified_by_id)
    return None


def approver_comparison(filters):
    """Approvers side by side — the comparison half of the Approval Register.

    Same population and same approver rule as ``approval_report``; what differs
    is the shape: one row per approver with accept/reject/pending split, reject
    and defect rates, and a per-DN breakdown, so two supervisors signing off the
    same product can be read against each other.

    Outcome comes from ``_effective_decision`` (final decision, else the lab
    decision) — reading final_decision_value alone leaves nearly every row
    blank on production data and the comparison says nothing.
    """
    from app.models.user import User
    from app.services.bi_service import _effective_decision

    pipes = (
        apply_pipe_filters(Pipe.query, filters)
        .order_by(Pipe.production_date.desc(), Pipe.id.desc())
        .all()
    )
    users = {u.id: u for u in User.query.all()}
    lab_name = ProductionStage.name_for_code("lab")

    def _new_group():
        return {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "pending": 0,
            "defect_pipes": 0,
            "by_dn": defaultdict(int),
            "first_date": None,
            "last_date": None,
        }

    by_approver = defaultdict(_new_group)
    attributed = 0

    for p in pipes:
        user = _pipe_approver(p, users, lab_name)
        if user is not None:
            attributed += 1
            name = (user.full_name or user.username or str(user.id)).strip()
        else:
            name = _UNSPECIFIED_APPROVER

        g = by_approver[name]
        g["total"] += 1

        outcome = PipeStage.classify_decision(_effective_decision(p))
        if outcome == "accept":
            g["accepted"] += 1
        elif outcome == "reject":
            g["rejected"] += 1
        else:
            g["pending"] += 1

        if any(s.has_defect for s in p.stages):
            g["defect_pipes"] += 1

        if p.diameter:
            g["by_dn"]["DN%s" % p.diameter] += 1

        if p.production_date:
            if g["first_date"] is None or p.production_date < g["first_date"]:
                g["first_date"] = p.production_date
            if g["last_date"] is None or p.production_date > g["last_date"]:
                g["last_date"] = p.production_date

    rows = []
    for name in sorted(by_approver, key=lambda n: by_approver[n]["total"],
                       reverse=True):
        g = by_approver[name]
        total = g["total"]
        # Rates over decided pipes only: a supervisor with a big pending queue
        # must not look like one with a low reject rate.
        decided = g["accepted"] + g["rejected"]
        rows.append({
            "approver": name,
            "total": total,
            "accepted": g["accepted"],
            "rejected": g["rejected"],
            "pending": g["pending"],
            "decided": decided,
            "accept_rate": round(g["accepted"] / decided * 100, 1) if decided else None,
            "reject_rate": round(g["rejected"] / decided * 100, 1) if decided else None,
            "defect_pipes": g["defect_pipes"],
            "defect_rate": round(g["defect_pipes"] / total * 100, 1) if total else 0,
            "by_dn": dict(g["by_dn"]),
            "first_date": g["first_date"].isoformat() if g["first_date"] else "",
            "last_date": g["last_date"].isoformat() if g["last_date"] else "",
        })

    dn_codes = sorted({dn for r in rows for dn in r["by_dn"]})

    named = [r for r in rows if r["approver"] != _UNSPECIFIED_APPROVER]
    # Only crown a highest-reject approver when there is real signal and more
    # than one of them — "worst of one" is not a comparison.
    strictest = None
    if len(named) > 1:
        rated = [r for r in named if r["reject_rate"] is not None]
        strictest = max(rated, key=lambda r: r["reject_rate"], default=None)
        if strictest and strictest["reject_rate"] <= 0:
            strictest = None

    export_rows = [
        {
            "approver": r["approver"],
            "pipes": r["total"],
            "accepted": r["accepted"],
            "rejected": r["rejected"],
            "pending": r["pending"],
            "accept_rate_%": r["accept_rate"],
            "reject_rate_%": r["reject_rate"],
            "defect_pipes": r["defect_pipes"],
            "defect_rate_%": r["defect_rate"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    ]

    return {
        "approver_rows": rows,
        "dn_codes": dn_codes,
        "approvers": [
            {"id": u.id, "name": u.full_name or u.username}
            for u in sorted(users.values(), key=lambda u: u.full_name or u.username)
        ],
        "summary": {
            "approvers": len(named),
            "pipes": len(pipes),
            "strictest": strictest["approver"] if strictest else None,
            "strictest_rate": strictest["reject_rate"] if strictest else None,
        },
        "coverage": {
            "total_pipes": len(pipes),
            "with_approver": attributed,
            "pct": round(attributed / len(pipes) * 100, 1) if pipes else 0,
        },
        "rows": export_rows,
        "export_rows": export_rows,
        "filters": filters,
    }


# ---------------------------------------------------------------------------
# Rework by Stage
# ---------------------------------------------------------------------------

# "Do it again" decisions — the pipe is sent back through work that was already
# done. Deliberately narrower than nonconformance_service.HOLD_DECISIONS: a
# plain Hold parks the pipe pending a decision and a DownGrade re-classes it;
# neither repeats production work, so neither is rework.
REWORK_DECISIONS = (
    "Rework", "إعادة عمل",
    "Retest", "إعادة اختبار",
    "Resample", "إعادة عينة",
    "Reheat treatment", "إعادة معالجة حرارية",
    "Micro-structure", "فحص البنية المجهرية",
)

_REWORK_LOWER = frozenset(d.lower() for d in REWORK_DECISIONS)

# Canonical label per decision, so the AR and EN spellings collapse into one
# column instead of two half-filled ones.
REWORK_LABELS = {
    "rework": ("Rework", "إعادة عمل"),
    "إعادة عمل": ("Rework", "إعادة عمل"),
    "retest": ("Retest", "إعادة اختبار"),
    "إعادة اختبار": ("Retest", "إعادة اختبار"),
    "resample": ("Resample", "إعادة عينة"),
    "إعادة عينة": ("Resample", "إعادة عينة"),
    "reheat treatment": ("Reheat treatment", "إعادة معالجة حرارية"),
    "إعادة معالجة حرارية": ("Reheat treatment", "إعادة معالجة حرارية"),
    "micro-structure": ("Micro-structure", "فحص البنية المجهرية"),
    "فحص البنية المجهرية": ("Micro-structure", "فحص البنية المجهرية"),
}

REWORK_KINDS = ["Rework", "Retest", "Resample", "Reheat treatment", "Micro-structure"]

REWORK_KINDS_AR = {
    "Rework": "إعادة عمل",
    "Retest": "إعادة اختبار",
    "Resample": "إعادة عينة",
    "Reheat treatment": "إعادة معالجة حرارية",
    "Micro-structure": "فحص البنية المجهرية",
}


def is_rework(decision):
    """True when a stored stage decision means the work is repeated."""
    if not decision:
        return False
    return str(decision).strip().lower() in _REWORK_LOWER


def _rework_kind(decision):
    """Canonical English label for a rework decision ('' when not rework)."""
    key = str(decision or "").strip().lower()
    pair = REWORK_LABELS.get(key)
    return pair[0] if pair else ""


def stage_name_aliases():
    """Map a retired stage name to the name that replaced it.

    ``PipeStageHistory`` stores the stage name that was current when the row was
    written, and Stage Management lets a built-in stage be renamed. The Lab
    Approval collapse is the live case: ``collapse_lab_approval()`` migrated
    ``pipe_stages`` from "Lab" to "Lab Approval" but left the history rows on
    the old name, so a rework recorded before the rename would otherwise appear
    as a phantom stage with a rework count and no denominator.
    """
    aliases = {}
    for original, code in BUILTIN_CODES.items():
        current = ProductionStage.name_for_code(code)
        if current and current != original:
            aliases[original] = current
    # "Lab" predates the collapse and is no longer in BUILTIN_CODES, so the loop
    # above cannot produce it.
    lab = ProductionStage.name_for_code("lab")
    if lab and lab != "Lab":
        aliases["Lab"] = lab
    return aliases


def rework_report(filters):
    """Rework counts for every stage.

    A stage record counts as reworked when it *ever* carried a rework decision,
    not only when it still does: ``PipeStage.decision`` is overwritten the
    moment the operator re-decides, so a Rework followed by an Accept leaves no
    trace on the live row. ``PipeStageHistory`` keeps the snapshot (written
    before each update and after each create), so the two sources are unioned
    and de-duplicated on ``pipe_stage_id`` — a stage reworked three times is
    one reworked stage record, not three.
    """
    from app.models.stage_history import PipeStageHistory

    pipe_ids = apply_pipe_filters(db.session.query(Pipe.id), filters).subquery()
    pipe_id_select = db.session.query(pipe_ids.c.id)

    alias = stage_name_aliases()

    def canonical(name):
        return alias.get(name, name)

    # Denominator: stage records that actually reached a decision.
    totals = defaultdict(int)
    for stage_name, count in (
        db.session.query(PipeStage.stage_name, func.count(PipeStage.id))
        .filter(
            PipeStage.pipe_id.in_(pipe_id_select),
            PipeStage.decision.isnot(None),
            PipeStage.decision != "",
        )
        .group_by(PipeStage.stage_name)
        .all()
    ):
        totals[canonical(stage_name)] += count

    # Currently sitting on a rework decision.
    current = (
        db.session.query(
            PipeStage.id,
            PipeStage.stage_name,
            PipeStage.pipe_id,
            PipeStage.decision,
        )
        .filter(
            PipeStage.pipe_id.in_(pipe_id_select),
            func.lower(PipeStage.decision).in_(_REWORK_LOWER),
        )
        .all()
    )

    # Ever carried one, including stages that have since moved on.
    historic = (
        db.session.query(
            PipeStageHistory.pipe_stage_id,
            PipeStageHistory.stage_name,
            PipeStageHistory.pipe_id,
            PipeStageHistory.decision,
        )
        .filter(
            PipeStageHistory.pipe_id.in_(pipe_id_select),
            func.lower(PipeStageHistory.decision).in_(_REWORK_LOWER),
        )
        .all()
    )

    agg = defaultdict(
        lambda: {
            "stage_ids": set(),
            "open_ids": set(),
            "pipe_ids": set(),
            "kinds": defaultdict(set),
        }
    )
    for stage_id, stage_name, pipe_id, decision in current:
        g = agg[canonical(stage_name)]
        g["stage_ids"].add(stage_id)
        g["open_ids"].add(stage_id)
        g["pipe_ids"].add(pipe_id)
        g["kinds"][_rework_kind(decision)].add(stage_id)
    for stage_id, stage_name, pipe_id, decision in historic:
        name = canonical(stage_name)
        g = agg[name]
        # A deleted stage record still has history rows; a synthetic key keeps
        # them countable without merging different pipes together.
        key = stage_id if stage_id is not None else "h%s-%s" % (pipe_id, name)
        g["stage_ids"].add(key)
        g["pipe_ids"].add(pipe_id)
        g["kinds"][_rework_kind(decision)].add(key)

    # Every active stage gets a row (zeros included), plus any historical stage
    # name that no longer exists in Stage Management.
    ordered = ProductionStage.active_names()
    extra = sorted((set(totals) | set(agg)) - set(ordered))
    stage_names = ordered + extra

    rows = []
    for name in stage_names:
        g = agg.get(name)
        total = totals.get(name, 0)
        reworked = len(g["stage_ids"]) if g else 0
        row = {
            "stage": name,
            "total": total,
            "rework": reworked,
            "open": len(g["open_ids"]) if g else 0,
            "pipes": len(g["pipe_ids"]) if g else 0,
            "rate": round(reworked / total * 100, 1) if total else 0.0,
        }
        for kind in REWORK_KINDS:
            row[kind] = len(g["kinds"].get(kind, ())) if g else 0
        rows.append(row)

    total_stages = sum(r["total"] for r in rows)
    total_rework = sum(r["rework"] for r in rows)
    ranked = [r for r in rows if r["total"] and r["rework"]]
    worst = max(ranked, key=lambda r: r["rate"]) if ranked else None
    pipes_reworked = set()
    for g in agg.values():
        pipes_reworked |= g["pipe_ids"]
    return {
        "rows": rows,
        "export_rows": rows,
        "kinds": REWORK_KINDS,
        "kinds_ar": REWORK_KINDS_AR,
        "summary": {
            "total_stage_records": total_stages,
            "total_rework": total_rework,
            "open_rework": sum(r["open"] for r in rows),
            "pipes_reworked": len(pipes_reworked),
            "rework_rate": round(total_rework / total_stages * 100, 1)
            if total_stages
            else 0.0,
            "worst_stage": worst["stage"] if worst else "",
        },
        "filters": filters,
    }
