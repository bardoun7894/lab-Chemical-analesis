"""
Reports Routes
"""

from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    make_response,
    jsonify,
    redirect,
    url_for,
)
from flask_login import login_required, current_user
from datetime import date, timedelta
from io import BytesIO
from app import db, csrf
from sqlalchemy import func
from app.models.chemical import ChemicalAnalysis, Furnace, Machine
from app.models.pipe import Pipe, PipeStage
from app.models.mechanical import MechanicalTest
from app.services.ai_service import generate_report_summary
from app.services.filter_service import get_filter_values
from app.services.permission_service import requires_permission

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/")
@login_required
@requires_permission("reports", "list")
def index():
    """Reports dashboard"""
    return render_template("reports/index.html")


# ===========================================================================
# Analytics / advanced reports (Phase 4)
# ===========================================================================

from app.services import analytics_service
from app.models.production_order import ProductionOrder


def _render_analytics_report(template_name, title, data):
    """Shared render helper for analytics reports."""
    return render_template(
        f"reports/{template_name}",
        report_title=title,
        data=data,
        orders=ProductionOrder.query.order_by(ProductionOrder.order_date.desc())
        .limit(50)
        .all(),
    )


@reports_bp.route("/production-summary")
@login_required
@requires_permission("reports", "view")
def production_summary():
    data = analytics_service.production_summary(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "production_summary.html", "Production Summary", data
    )


@reports_bp.route("/order-performance")
@login_required
@requires_permission("reports", "view")
def order_performance():
    data = analytics_service.order_performance(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report("order_performance.html", "Order Performance", data)


@reports_bp.route("/customer-production")
@login_required
@requires_permission("reports", "view")
def customer_production():
    data = analytics_service.customer_production(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "customer_production.html", "Customer Production", data
    )


@reports_bp.route("/delivery-report")
@login_required
@requires_permission("reports", "view")
def delivery_report():
    data = analytics_service.delivery_report(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report("delivery_report.html", "Delivery Report", data)


@reports_bp.route("/rft-report")
@login_required
@requires_permission("reports", "view")
def rft_report():
    data = analytics_service.rft_report(analytics_service.parse_filters(request.args))
    return _render_analytics_report("rft_report.html", "Right First Time (RFT)", data)


@reports_bp.route("/defect-analysis")
@login_required
@requires_permission("reports", "view")
def defect_analysis():
    data = analytics_service.defect_analysis(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "defect_analysis.html", "Defect Analysis (Pareto)", data
    )


@reports_bp.route("/heat-traceability")
@login_required
@requires_permission("reports", "view")
def heat_traceability():
    data = analytics_service.heat_traceability(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "heat_traceability.html", "Heat / Ladle Traceability", data
    )


@reports_bp.route("/mechanical-statistical")
@login_required
@requires_permission("reports", "view")
def mechanical_statistical():
    data = analytics_service.mechanical_statistical(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "mechanical_statistical.html", "Mechanical Statistical", data
    )


@reports_bp.route("/stage-performance")
@login_required
@requires_permission("reports", "view")
def stage_performance_v2():
    data = analytics_service.stage_performance(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "stage_performance_v2.html", "Stage Performance", data
    )


@reports_bp.route("/machine-performance")
@login_required
@requires_permission("reports", "view")
def machine_performance_v2():
    data = analytics_service.machine_performance(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "machine_performance_v2.html", "Machine Performance", data
    )


@reports_bp.route("/mold-performance")
@login_required
@requires_permission("reports", "view")
def mold_performance():
    data = analytics_service.mold_performance(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report("mold_performance.html", "Mold Performance", data)


# ---------------------------------------------------------------------------
# SPC control charts (I-MR, X̄-R, P, C) + Nelson rules
# ---------------------------------------------------------------------------

from app.services import spc_service, export_service


def _spc_params():
    """Validate and normalize the SPC-specific query params."""
    characteristic = request.args.get("characteristic", "tensile_mpa")
    if characteristic not in spc_service.CHARACTERISTICS:
        characteristic = "tensile_mpa"
    chart = request.args.get("chart", "imr")
    if chart not in ("imr", "xbar", "p", "c"):
        chart = "imr"
    group_by = request.args.get("group_by", "day")
    p_groups = ("day", "shift", "machine", "dn", "order")
    if group_by not in p_groups + ("stage",):
        group_by = "day"
    if chart == "p" and group_by == "stage":
        # P charts group decisions; 'stage' is only meaningful for C charts.
        group_by = "day"
    return characteristic, chart, group_by


def _spc_data(characteristic, chart, group_by, filters):
    """Assemble the view model for the SPC report."""
    data = {
        "filters": filters,
        "characteristic": characteristic,
        "characteristics": spc_service.CHARACTERISTICS,
        "chart": chart,
        "group_by": group_by,
    }

    def _tag_display(violations):
        for v in violations:
            v["display"] = [i + 1 for i in v["indices"]]  # 1-based for humans
        return violations

    if chart in ("p", "c"):
        attr = (
            spc_service.p_chart_data(filters, group_by)
            if chart == "p"
            else spc_service.c_chart_data(filters, group_by)
        )
        data["attr"] = attr
        if chart == "p":
            ps = [g["p"] for g in attr["groups"]]
            ns = [g["n"] for g in attr["groups"]]
            data["limits"] = spc_service.p_chart_limits(ps, ns)
        else:
            data["limits"] = spc_service.c_chart_limits(
                [g["count"] for g in attr["groups"]]
            )
        data["out_of_control"] = _attr_ooc(chart, attr["groups"], data["limits"])
        return data

    series = spc_service.build_series(characteristic, filters)
    data["series"] = series
    values = [p["value"] for p in series["points"]]

    if chart == "imr":
        limits = spc_service.imr_limits(values)
        data["limits"] = limits
        data["violations"] = (
            _tag_display(
                spc_service.nelson_violations(values, limits["cl"], limits["sigma"])
            )
            if limits
            else []
        )
        data["out_of_control"] = (
            [i for i, v in enumerate(values) if v > limits["ucl"] or v < limits["lcl"]]
            if limits and not limits["no_variation"]
            else []
        )
    else:  # xbar
        sg = spc_service.subgroup_values(series["points"])
        data["subgroups"] = sg
        limits = spc_service.xbar_r_limits([g["values"] for g in sg["subgroups"]])
        data["limits"] = limits
        means = limits["subgroup_means"] if limits else []
        data["violations"] = (
            _tag_display(
                spc_service.nelson_violations(means, limits["cl"], limits["sigma"])
            )
            if limits and limits["sigma"] > 0
            else []
        )
        data["out_of_control"] = (
            [i for i, v in enumerate(means) if v > limits["ucl"] or v < limits["lcl"]]
            if limits and not limits.get("no_variation")
            else []
        )
    return data


def _attr_ooc(chart, groups, limits):
    """Indices of attribute-chart points outside their (per-point) limits."""
    if not limits:
        return []
    out = []
    for i, g in enumerate(groups):
        value = g["p"] if chart == "p" else g["count"]
        if chart == "p":
            ucl, lcl = limits["limits"][i]
        else:
            ucl, lcl = limits["ucl"], limits["lcl"]
        if ucl is not None and (value > ucl or value < lcl):
            out.append(i)
    return out


@reports_bp.route("/spc")
@login_required
@requires_permission("reports", "spc")
def spc():
    filters = analytics_service.parse_filters(request.args)
    characteristic, chart, group_by = _spc_params()
    data = _spc_data(characteristic, chart, group_by, filters)
    return _render_analytics_report("spc.html", "SPC Control Charts", data)


# ---------------------------------------------------------------------------
# Process capability (Cp/Cpk/Pp/Ppk/DPMO)
# ---------------------------------------------------------------------------

from app.services import capability_service


@reports_bp.route("/capability")
@login_required
@requires_permission("reports", "capability")
def capability():
    filters = analytics_service.parse_filters(request.args)
    characteristic = request.args.get("characteristic", "carbon")
    if characteristic not in spc_service.CHARACTERISTICS:
        characteristic = "carbon"

    series = spc_service.build_series(characteristic, filters)
    values = [p["value"] for p in series["points"]]
    limits = capability_service.spec_limits_for(characteristic)

    data = {
        "filters": filters,
        "characteristic": characteristic,
        "characteristics": spc_service.CHARACTERISTICS,
        "series": series,
        "spec": limits,
        "indices": (
            capability_service.capability_indices(values, limits["lsl"], limits["usl"])
            if limits
            else None
        ),
        "histogram": capability_service.histogram(values),
        # Characteristics with no configured limits — shown as unavailable,
        # never given fabricated limits (FR-004).
        "unavailable": [
            key
            for key in spc_service.CHARACTERISTICS
            if capability_service.spec_limits_for(key) is None
        ],
    }
    return _render_analytics_report("capability.html", "Process Capability", data)


# ---------------------------------------------------------------------------
# Lab trends dashboard — six independent trend panels
# ---------------------------------------------------------------------------

LAB_TREND_PANELS = [
    ("tensile_mpa", "Tensile (MPa)"),
    ("hardness", "Hardness (HB)"),
    ("elongation", "Elongation (%)"),
    ("nodularity", "Nodularity (%Nd)"),
    ("carbon_equivalent", "Carbon Equivalent (CE)"),
    # Honest label: these are the microstructure % fields, not a measured
    # ferrite value (pending DrAlaa's confirmation of which field is ferrite).
    ("microstructure_70", "Microstructure % (>70)"),
]


@reports_bp.route("/lab-trends")
@login_required
@requires_permission("reports", "view")
def lab_trends():
    filters = analytics_service.parse_filters(request.args)
    panels = []
    for key, label in LAB_TREND_PANELS:
        series = spc_service.build_series(key, filters)
        # JSON-safe copies for the Chart.js payload (dates -> ISO strings).
        points = [
            {
                "date": p["date"].isoformat() if p["date"] else "",
                "value": p["value"],
                "source_url": p["source_url"],
            }
            for p in series["points"]
        ]
        panels.append(
            {
                "key": key,
                "label": label,
                "points": points,
                "total": series["total"],
                "truncated": series["truncated"],
            }
        )
    data = {"filters": filters, "panels": panels}
    return _render_analytics_report("lab_trends.html", "Lab Trends", data)


# ---------------------------------------------------------------------------
# Stage cycle-time analytics
# ---------------------------------------------------------------------------

from app.services import cycle_time_service


@reports_bp.route("/stage-cycle-time")
@login_required
@requires_permission("reports", "view")
def stage_cycle_time():
    data = cycle_time_service.stage_dwell(analytics_service.parse_filters(request.args))
    return _render_analytics_report("stage_cycle_time.html", "Stage Cycle Time", data)


# ---------------------------------------------------------------------------
# Stage audit trail — PipeStageHistory browser
# ---------------------------------------------------------------------------

from app.models.stage_history import PipeStageHistory
from app.models.stage import ProductionStage
from app.models.user import User


def _stage_audit_query(args):
    """Filtered PipeStageHistory query, newest first.

    pipe/changed_by are eager-loaded — the table renders up to 1000 rows and
    touches both relationships per row.
    """
    from sqlalchemy.orm import joinedload

    query = PipeStageHistory.query.options(
        joinedload(PipeStageHistory.pipe),
        joinedload(PipeStageHistory.changed_by),
    )
    pipe_code = (args.get("pipe") or "").strip()
    if pipe_code:
        query = query.join(Pipe, PipeStageHistory.pipe_id == Pipe.id).filter(
            Pipe.pipe_code.contains(pipe_code) | Pipe.no_code.contains(pipe_code)
        )
    stage = (args.get("stage") or "").strip()
    if stage:
        query = query.filter(PipeStageHistory.stage_name == stage)
    user_id = args.get("user_id", type=int)
    if user_id:
        query = query.filter(PipeStageHistory.changed_by_id == user_id)
    try:
        if args.get("date_from"):
            query = query.filter(
                PipeStageHistory.changed_at >= date.fromisoformat(args["date_from"])
            )
        if args.get("date_to"):
            # Inclusive of the whole day.
            query = query.filter(
                PipeStageHistory.changed_at
                < date.fromisoformat(args["date_to"]) + timedelta(days=1)
            )
    except ValueError:
        pass
    return query.order_by(PipeStageHistory.changed_at.desc())


def _stage_audit_columns():
    def _pipe(h):
        return (h.pipe.pipe_code or h.pipe.no_code) if h.pipe else h.pipe_id

    def _user(h):
        return h.changed_by.full_name or h.changed_by.username if h.changed_by else ""

    return [
        ("id", "ID", lambda h: h.id, False),
        (
            "changed_at",
            "Changed at",
            lambda h: h.changed_at.strftime("%Y-%m-%d %H:%M") if h.changed_at else "",
            True,
        ),
        ("pipe", "Pipe", _pipe, True),
        ("stage", "Stage", lambda h: h.stage_name, True),
        ("action", "Action", lambda h: h.action, True),
        ("decision", "Decision", lambda h: h.decision or "", True),
        ("reason", "Reason", lambda h: h.reason or "", True),
        ("user", "Changed by", _user, True),
        ("machine", "Machine", lambda h: h.machine_code or "", False),
        ("defect", "Defect", lambda h: h.defect_type or h.defect_reason or "", False),
        ("notes", "Notes", lambda h: h.notes or "", False),
        (
            "measurement",
            "Measurement",
            lambda h: h.measurement_value if h.measurement_value is not None else "",
            False,
        ),
    ]


@reports_bp.route("/stage-audit")
@login_required
@requires_permission("reports", "view")
def stage_audit():
    rows = _stage_audit_query(request.args).limit(1000).all()
    return render_template(
        "reports/stage_audit.html",
        report_title="Stage Audit Trail",
        rows=rows,
        stages=ProductionStage.active_names(),
        users=User.query.filter_by(is_active=True).order_by(User.username).all(),
        picker_columns=export_service.picker_meta(_stage_audit_columns()),
        filters=request.args,
    )


@reports_bp.route("/stage-audit.xlsx")
@login_required
@requires_permission("reports", "export")
def stage_audit_export():
    """Honours cols=, ids= and format=print from the Table Tools picker."""
    query = _stage_audit_query(request.args)
    query = export_service.filter_ids(
        query, PipeStageHistory.id, request.args.get("ids")
    )
    rows = query.all()
    selected = export_service.resolve_columns(
        _stage_audit_columns(), request.args.get("cols")
    )
    if request.args.get("format") == "print":
        return export_service.build_print("Stage Audit Trail", selected, rows)
    return export_service.build_xlsx("Stage Audit", selected, rows, "stage_audit.xlsx")


# ---------------------------------------------------------------------------
# BI gap-closure panels (Phase 2) — YTD, period compare, funnel, heatmap,
# saving matrix, data quality, auto diagnosis
# ---------------------------------------------------------------------------

from app.services import bi_service


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _date_arg(name, default):
    try:
        return date.fromisoformat(request.args[name])
    except (KeyError, ValueError, TypeError):
        return default


@reports_bp.route("/ytd-comparison")
@login_required
@requires_permission("reports", "view")
def ytd_comparison():
    today = date.today()
    year = min(max(_int_arg("year", today.year), 2000), 2100)
    compare_year = min(max(_int_arg("compare_year", year - 1), 2000), 2100)
    data = bi_service.ytd_comparison(year, compare_year)
    data["matrix"] = bi_service.stage_reject_matrix(year, compare_year)
    # Tuple keys aren't JSON-serializable — string-keyed copy for Chart.js.
    data["matrix"]["cells_json"] = {
        f"{s}|{m}": c for (s, m), c in data["matrix"]["cells"].items()
    }
    data["filters"] = {"year": year, "compare_year": compare_year}
    return _render_analytics_report(
        "ytd_comparison.html", f"YTD Comparison {year} vs {compare_year}", data
    )


@reports_bp.route("/period-compare")
@login_required
@requires_permission("reports", "view")
def period_compare():
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    a_from = _date_arg("a_from", prev_month_start)
    a_to = _date_arg("a_to", prev_month_end)
    b_from = _date_arg("b_from", month_start)
    b_to = _date_arg("b_to", today)

    thresholds = {}
    for key in ("reject_pct", "defect_count", "produced", "saving_kg"):
        raw = request.args.get(f"th_{key}", "")
        if raw:
            try:
                thresholds[key] = float(raw)
            except ValueError:
                pass

    data = bi_service.period_compare(a_from, a_to, b_from, b_to, thresholds)
    data["filters"] = {
        "a_from": a_from,
        "a_to": a_to,
        "b_from": b_from,
        "b_to": b_to,
        # Show the EFFECTIVE thresholds (defaults merged), not just overrides —
        # otherwise inputs render blank while defaults actively alert.
        "thresholds": {**bi_service.DEFAULT_THRESHOLDS, **thresholds},
    }
    return _render_analytics_report("period_compare.html", "Period Compare", data)


@reports_bp.route("/stage-funnel")
@login_required
@requires_permission("reports", "view")
def stage_funnel():
    data = bi_service.stage_funnel(analytics_service.parse_filters(request.args))
    return _render_analytics_report("stage_funnel.html", "Stage Funnel", data)


@reports_bp.route("/defect-heatmap")
@login_required
@requires_permission("reports", "view")
def defect_heatmap():
    metric = request.args.get("metric", "count")
    if metric not in ("count", "pct"):
        metric = "count"
    data = bi_service.defect_heatmap(
        analytics_service.parse_filters(request.args), metric=metric
    )
    # Toggle links preserving every other filter param.
    from urllib.parse import urlencode

    args = request.args.to_dict()
    links = {m: "?" + urlencode(dict(args, metric=m)) for m in ("count", "pct")}
    data["metric_links"] = links
    return _render_analytics_report(
        "defect_heatmap.html", "Defect Heatmap — DN × Class", data
    )


@reports_bp.route("/saving-matrix")
@login_required
@requires_permission("reports", "view")
def saving_matrix():
    data = bi_service.saving_matrix(analytics_service.parse_filters(request.args))
    return _render_analytics_report(
        "saving_matrix.html", "Saving Matrix — DN × Class", data
    )


@reports_bp.route("/data-quality")
@login_required
@requires_permission("reports", "view")
def data_quality():
    data = bi_service.data_quality()
    data["filters"] = {}
    return _render_analytics_report("data_quality.html", "Data Quality", data)


@reports_bp.route("/diagnosis")
@login_required
@requires_permission("reports", "view")
def diagnosis():
    data = bi_service.diagnose()
    data["filters"] = {}
    return _render_analytics_report("diagnosis.html", "Auto Diagnosis", data)


@reports_bp.route("/bi-dashboard")
@login_required
@requires_permission("reports", "view")
def bi_dashboard():
    """One-page replica of DrAlaa's CCM BI dashboard — same shape, live data."""
    filters = analytics_service.parse_filters(request.args)
    data = bi_service.bi_dashboard(filters)
    data["saving"] = bi_service.saving_matrix(filters)
    # JSON-safe chart payload (saving matrix has tuple keys — table-only).
    data["payload"] = {
        "kpis": data["kpis"],
        "groups": data["groups"],
        "reasons": data["reasons"],
        "stage": data["stage"],
        "trends": data["trends"],
    }
    return _render_analytics_report("bi_dashboard.html", "CCM BI Dashboard", data)


@reports_bp.route("/spc.xlsx")
@login_required
@requires_permission("reports", "spc")
@requires_permission("reports", "export")
def spc_export():
    filters = analytics_service.parse_filters(request.args)
    characteristic, chart, group_by = _spc_params()

    if chart in ("p", "c"):
        attr = (
            spc_service.p_chart_data(filters, group_by)
            if chart == "p"
            else spc_service.c_chart_data(filters, group_by)
        )
        if chart == "p":
            columns = [
                ("key", "Group", lambda g: g["key"]),
                ("n", "Pipes", lambda g: g["n"]),
                ("defectives", "Non-conforming", lambda g: g["defectives"]),
                ("hold", "of which HOLD", lambda g: g["hold"]),
                ("p", "Proportion", lambda g: round(g["p"], 4)),
            ]
        else:
            columns = [
                ("key", "Group", lambda g: g["key"]),
                ("count", "Defect count", lambda g: g["count"]),
            ]
        return export_service.build_xlsx(
            "SPC", columns, attr["groups"], f"spc_{chart}_chart.xlsx"
        )

    series = spc_service.build_series(characteristic, filters)
    rows = list(enumerate(series["points"], start=1))
    columns = [
        ("idx", "#", lambda r: r[0]),
        ("date", "Date", lambda r: r[1]["date"].isoformat() if r[1]["date"] else ""),
        ("subgroup", "Ladle / Subgroup", lambda r: r[1]["subgroup"]),
        (
            "value",
            spc_service.characteristic_label(characteristic),
            lambda r: r[1]["value"],
        ),
        ("source", "Source record", lambda r: r[1]["source_id"]),
    ]
    return export_service.build_xlsx("SPC", columns, rows, f"spc_{characteristic}.xlsx")


@reports_bp.route("/weight-saving")
@login_required
@requires_permission("reports", "view")
def weight_saving():
    data = analytics_service.weight_saving(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "weight_saving.html", "Weight Saving Analysis", data
    )


@reports_bp.route("/annealing-hourly")
@login_required
@requires_permission("reports", "view")
def annealing_hourly():
    data = analytics_service.annealing_hourly(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "annealing_hourly.html", "Annealing Hourly Entry", data
    )


@reports_bp.route("/management-dashboard")
@login_required
@requires_permission("reports", "view")
def management_dashboard():
    data = analytics_service.management_dashboard(
        analytics_service.parse_filters(request.args)
    )
    return _render_analytics_report(
        "management_dashboard.html", "Management Dashboard", data
    )


def _parse_shift_filters(args):
    """Filters for shift/delivery reports — NO default date floor so historical
    shifts and deliveries are visible (the old pages only showed today)."""

    def _date(key):
        raw = args.get(key)
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    return {
        "date_from": _date("date_from"),
        "date_to": _date("date_to"),
        "shift": args.get("shift", type=int),
        "shift_engineer": args.get("shift_engineer"),
        "customer": args.get("customer"),
        "group_by": args.get("group_by", "heat"),
    }


@reports_bp.route("/shift-engineer")
@login_required
@requires_permission("reports", "view")
def shift_engineer():
    """Shift Engineer report — KPIs, waiting, defects and pipe list for any
    date range / shift / engineer."""
    filters = _parse_shift_filters(request.args)
    data = analytics_service.shift_engineer_report(filters)
    return _render_analytics_report(
        "shift_engineer.html", "Shift Engineer Report", data
    )


@reports_bp.route("/shift-engineer-comparison")
@login_required
@requires_permission("reports", "view")
def shift_engineer_comparison():
    """Per-engineer comparison — production, defect rate, reject rate, saving
    weight, and per-CCM-machine breakdown, side by side for all engineers."""
    filters = _parse_shift_filters(request.args)
    data = analytics_service.shift_engineer_comparison(filters)
    return _render_analytics_report(
        "shift_engineer_comparison.html", "Shift Engineer Comparison", data
    )


@reports_bp.route("/delivery-comparison")
@login_required
@requires_permission("reports", "view")
def delivery_comparison():
    """Delivery comparison — delivered pipes grouped by customer / engineer /
    sales order, side by side."""
    filters = _parse_shift_filters(request.args)
    data = analytics_service.delivery_comparison(filters)
    return _render_analytics_report(
        "delivery_comparison.html", "Delivery Comparison", data
    )


@reports_bp.route("/delivery-overview")
@login_required
@requires_permission("reports", "view")
def delivery_overview():
    """Delivery overview — grouped per heat / per batch / per order."""
    filters = _parse_shift_filters(request.args)
    data = analytics_service.delivery_overview(filters)
    return _render_analytics_report("delivery_overview.html", "Delivery Overview", data)


@reports_bp.route("/stage-measurements")
@login_required
@requires_permission("reports", "view")
def stage_measurements():
    """Stage measurements — the popup-entered data (length, thickness,
    temperature, per-meter profile, CCM dimensions) that no report surfaced."""
    filters = analytics_service.parse_filters(request.args)
    data = analytics_service.stage_measurements(filters)
    return _render_analytics_report(
        "stage_measurements.html", "Stage Measurements", data
    )


@reports_bp.route("/approvals")
@login_required
@requires_permission("reports", "view")
def approvals():
    """Approval register — pipes with a final decision, attributed to the
    approving user, filterable by approver."""
    filters = analytics_service.parse_filters(request.args)
    data = analytics_service.approval_report(filters)
    return _render_analytics_report("approvals.html", "Approval Register", data)


@reports_bp.route("/non-conformance")
@login_required
@requires_permission("reports", "non_conformance")
def non_conformance():
    """Non-Conformance Register — every out-of-spec / held / rejected record
    from any stage in one actionable list."""
    from app.services import export_service, nonconformance_service
    from app.models.stage import ProductionStage

    filters = nonconformance_service.parse_filters(request.args)
    rows, summary, actions = nonconformance_service.build_register(filters)

    if request.args.get("format") == "print":
        columns = export_service.resolve_columns(
            nonconformance_service.export_columns(), request.args.get("cols")
        )
        return export_service.build_print("Non-Conformance Register", columns, rows)

    return render_template(
        "reports/non_conformance.html",
        rows=rows,
        summary=summary,
        filters=filters,
        stage_names=ProductionStage.active_names(),
        source_labels=nonconformance_service.SOURCE_LABELS,
        picker_columns=export_service.picker_meta(
            nonconformance_service.export_columns()
        ),
        is_reject=nonconformance_service.is_reject,
        actions_by_key={k: v.to_dict() for k, v in actions.items()},
    )


@reports_bp.route("/non-conformance/action/save", methods=["POST"])
@csrf.exempt
@login_required
@requires_permission("reports", "non_conformance")
def non_conformance_action_save():
    """Upsert one cover action for a non-conformance register row."""
    from datetime import datetime

    from app.models.nonconformance_action import NonConformanceAction

    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "").strip()
    source_code = (payload.get("source_code") or "").strip()
    if source not in ("chemical", "mechanical", "stage", "pipe") or not source_code:
        return jsonify({"error": "source and source_code are required"}), 400

    status_value = (payload.get("status") or NonConformanceAction.DEFAULT_STATUS).strip()
    if status_value not in NonConformanceAction.STATUSES:
        status_value = NonConformanceAction.DEFAULT_STATUS

    responsible_date = None
    rd = (payload.get("responsible_date") or "").strip()
    if rd:
        try:
            responsible_date = datetime.strptime(rd, "%Y-%m-%d").date()
        except ValueError:
            responsible_date = None

    action = NonConformanceAction.query.filter_by(
        source=source, source_code=source_code
    ).first()
    username = current_user.username if current_user.is_authenticated else None

    if action is None:
        action = NonConformanceAction(
            source=source,
            source_code=source_code,
            created_by=username,
        )
        db.session.add(action)

    action.root_cause = (payload.get("root_cause") or "").strip() or None
    action.corrective_action = (payload.get("corrective_action") or "").strip() or None
    action.responsible = (payload.get("responsible") or "").strip() or None
    action.responsible_date = responsible_date
    action.status = status_value
    action.updated_by = username

    db.session.commit()
    return jsonify({"success": True, "status": status_value, "action": action.to_dict()})


@reports_bp.route("/non-conformance/export.xlsx")
@login_required
@requires_permission("reports", "export")
def non_conformance_export():
    """Excel export of the register, honouring the current filters + picker."""
    from app.services import export_service, nonconformance_service

    filters = nonconformance_service.parse_filters(request.args)
    rows, _summary, _actions = nonconformance_service.build_register(filters)
    columns = export_service.resolve_columns(
        nonconformance_service.export_columns(), request.args.get("cols")
    )
    return export_service.build_xlsx(
        "Non-Conformance", columns, rows, "non_conformance_register.xlsx"
    )


# Generic export (Excel) for any analytics report
@reports_bp.route("/export/<report_name>.xlsx")
@login_required
@requires_permission("reports", "export")
def export_analytics_excel(report_name):
    """Export any analytics report to Excel, respecting current filters."""
    import xlsxwriter
    from io import BytesIO

    if report_name in (
        "shift-engineer",
        "shift-engineer-comparison",
        "delivery-overview",
        "delivery-comparison",
    ):
        filters = _parse_shift_filters(request.args)
    else:
        filters = analytics_service.parse_filters(request.args)
    func_map = {
        "production-summary": analytics_service.production_summary,
        "order-performance": analytics_service.order_performance,
        "customer-production": analytics_service.customer_production,
        "delivery-report": analytics_service.delivery_report,
        "rft-report": analytics_service.rft_report,
        "defect-analysis": analytics_service.defect_analysis,
        "heat-traceability": analytics_service.heat_traceability,
        "stage-performance": analytics_service.stage_performance,
        "machine-performance": analytics_service.machine_performance,
        "mold-performance": analytics_service.mold_performance,
        "weight-saving": analytics_service.weight_saving,
        "shift-engineer": lambda f: analytics_service.shift_engineer_report(f),
        "shift-engineer-comparison": lambda f: (
            analytics_service.shift_engineer_comparison(f)
        ),
        "delivery-overview": lambda f: analytics_service.delivery_overview(f),
        "delivery-comparison": lambda f: analytics_service.delivery_comparison(f),
        "stage-measurements": analytics_service.stage_measurements,
        "approvals": analytics_service.approval_report,
    }
    fn = func_map.get(report_name)
    if not fn:
        return jsonify({"error": "Unknown report"}), 404

    data = fn(filters)
    rows = data.get("export_rows") or data.get("rows", [])

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Data")
    if rows:
        headers = list(rows[0].keys())
        # Honour a cols= picker (subset of the dynamic dict keys), keeping the
        # report's native column order. Unknown/empty cols => all columns.
        cols_arg = request.args.get("cols")
        if cols_arg:
            wanted = {c.strip() for c in cols_arg.split(",") if c.strip()}
            subset = [h for h in headers if h in wanted]
            if subset:
                headers = subset
        bold = wb.add_format({"bold": True, "bg_color": "#D3D3D3"})
        for c, h in enumerate(headers):
            ws.write(0, c, h, bold)
        for r, row in enumerate(rows, start=1):
            for c, h in enumerate(headers):
                ws.write(r, c, row.get(h))
    # Summary on a second sheet
    summary_ws = wb.add_worksheet("Summary")
    summary = data.get("summary", {})
    for r, (k, v) in enumerate(summary.items()):
        summary_ws.write(r, 0, str(k))
        summary_ws.write(r, 1, v if isinstance(v, (int, float, str)) else str(v))
    wb.close()
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{report_name}.xlsx",
    )


@reports_bp.route("/daily-production")
@login_required
@requires_permission("reports", "daily_production")
def daily_production():
    """Daily production report"""
    report_date_str = request.args.get("date", date.today().isoformat())
    try:
        report_date = date.fromisoformat(report_date_str)
    except (ValueError, TypeError):
        report_date = date.today()

    # Get pipes for this date
    pipes = (
        Pipe.query.filter_by(production_date=report_date)
        .order_by(Pipe.shift, Pipe.no_code)
        .all()
    )

    # Group by shift
    by_shift = {1: [], 2: [], 3: []}
    for pipe in pipes:
        shift = pipe.shift or 1
        if shift in by_shift:
            by_shift[shift].append(pipe)

    # Summary stats
    total = len(pipes)
    by_diameter = {}
    for pipe in pipes:
        dn = pipe.diameter or "Unknown"
        by_diameter[dn] = by_diameter.get(dn, 0) + 1

    return render_template(
        "reports/daily_production.html",
        report_date=report_date,
        pipes=pipes,
        by_shift=by_shift,
        total=total,
        by_diameter=by_diameter,
    )


@reports_bp.route("/chemical-analysis")
@login_required
@requires_permission("reports", "chemical_analysis")
def chemical_analysis():
    """Chemical analysis report"""
    date_from_str = request.args.get(
        "date_from", (date.today() - timedelta(days=7)).isoformat()
    )
    date_to_str = request.args.get("date_to", date.today().isoformat())
    furnace_id = request.args.get("furnace_id", type=int)

    try:
        date_from = date.fromisoformat(date_from_str)
    except (ValueError, TypeError):
        date_from = date.today() - timedelta(days=7)
    try:
        date_to = date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_to = date.today()

    date_from = date_from.isoformat()
    date_to = date_to.isoformat()

    query = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from, ChemicalAnalysis.test_date <= date_to
    )

    if furnace_id:
        query = query.filter_by(furnace_id=furnace_id)

    analyses = query.order_by(ChemicalAnalysis.test_date.desc()).all()
    furnaces = Furnace.query.filter_by(is_active=True).all()

    # Stats
    total = len(analyses)
    accepted = sum(1 for a in analyses if a.decision == "ACCEPT")
    rejected = sum(1 for a in analyses if a.decision == "REJECT")
    defects = sum(1 for a in analyses if a.has_defect)

    return render_template(
        "reports/chemical_analysis.html",
        analyses=analyses,
        furnaces=furnaces,
        date_from=date_from,
        date_to=date_to,
        selected_furnace=furnace_id,
        stats={
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "defects": defects,
            "rate": round(accepted / total * 100, 1) if total > 0 else 0,
        },
    )


@reports_bp.route("/defect-summary")
@login_required
@requires_permission("reports", "defect_summary")
def defect_summary():
    """Defect summary report"""
    date_from_str = request.args.get(
        "date_from", (date.today() - timedelta(days=30)).isoformat()
    )
    date_to_str = request.args.get("date_to", date.today().isoformat())

    try:
        date_from = date.fromisoformat(date_from_str).isoformat()
    except (ValueError, TypeError):
        date_from = (date.today() - timedelta(days=30)).isoformat()
    try:
        date_to = date.fromisoformat(date_to_str).isoformat()
    except (ValueError, TypeError):
        date_to = date.today().isoformat()

    # Chemical analysis defects
    chem_defects = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to,
        ChemicalAnalysis.has_defect == True,
    ).all()

    # Stage defects
    stage_defects = (
        db.session.query(PipeStage)
        .join(Pipe)
        .filter(
            Pipe.production_date >= date_from,
            Pipe.production_date <= date_to,
            PipeStage.has_defect == True,
        )
        .all()
    )

    # Group by stage
    defects_by_stage = {}
    for stage in stage_defects:
        name = stage.stage_name
        defects_by_stage[name] = defects_by_stage.get(name, 0) + 1

    return render_template(
        "reports/defect_summary.html",
        chem_defects=chem_defects,
        stage_defects=stage_defects,
        defects_by_stage=defects_by_stage,
        date_from=date_from,
        date_to=date_to,
    )


@reports_bp.route("/export/daily-production-pdf")
@login_required
@requires_permission("reports", "export")
def export_daily_production_pdf():
    """Export daily production report as PDF"""
    report_date_str = request.args.get("date", date.today().isoformat())
    try:
        report_date = date.fromisoformat(report_date_str)
    except (ValueError, TypeError):
        report_date = date.today()

    pipes = (
        Pipe.query.filter_by(production_date=report_date)
        .order_by(Pipe.shift, Pipe.no_code)
        .all()
    )

    by_shift = {1: [], 2: [], 3: []}
    for pipe in pipes:
        shift = pipe.shift or 1
        if shift in by_shift:
            by_shift[shift].append(pipe)

    by_diameter = {}
    for pipe in pipes:
        dn = pipe.diameter or "Unknown"
        by_diameter[dn] = by_diameter.get(dn, 0) + 1

    from app.services.report_service import generate_daily_production_pdf

    pdf_buffer = generate_daily_production_pdf(
        pipes, report_date, by_shift, by_diameter
    )

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"daily_production_{report_date.isoformat()}.pdf",
    )


@reports_bp.route("/export/defect-summary-pdf")
@login_required
@requires_permission("reports", "export")
def export_defect_summary_pdf():
    """Export defect summary report as PDF"""
    date_from_str = request.args.get(
        "date_from", (date.today() - timedelta(days=30)).isoformat()
    )
    date_to_str = request.args.get("date_to", date.today().isoformat())

    try:
        date_from = date.fromisoformat(date_from_str).isoformat()
    except (ValueError, TypeError):
        date_from = (date.today() - timedelta(days=30)).isoformat()
    try:
        date_to = date.fromisoformat(date_to_str).isoformat()
    except (ValueError, TypeError):
        date_to = date.today().isoformat()

    chem_defects = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to,
        ChemicalAnalysis.has_defect == True,
    ).all()

    stage_defects = (
        db.session.query(PipeStage)
        .join(Pipe)
        .filter(
            Pipe.production_date >= date_from,
            Pipe.production_date <= date_to,
            PipeStage.has_defect == True,
        )
        .all()
    )

    defects_by_stage = {}
    for stage in stage_defects:
        name = stage.stage_name
        defects_by_stage[name] = defects_by_stage.get(name, 0) + 1

    from app.services.report_service import generate_defect_report_pdf

    pdf_buffer = generate_defect_report_pdf(
        chem_defects, stage_defects, defects_by_stage, date_from, date_to
    )

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"defect_summary_{date_from}_to_{date_to}.pdf",
    )


@reports_bp.route("/export/chemical-pdf")
@login_required
@requires_permission("reports", "export")
def export_chemical_pdf():
    """Export chemical analysis report as PDF"""
    date_from = request.args.get(
        "date_from", (date.today() - timedelta(days=7)).isoformat()
    )
    date_to = request.args.get("date_to", date.today().isoformat())

    analyses = (
        ChemicalAnalysis.query.filter(
            ChemicalAnalysis.test_date >= date_from,
            ChemicalAnalysis.test_date <= date_to,
        )
        .order_by(ChemicalAnalysis.test_date.desc())
        .all()
    )

    # Generate PDF using ReportLab
    from app.services.report_service import generate_chemical_pdf

    pdf_buffer = generate_chemical_pdf(analyses, date_from, date_to)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"chemical_report_{date_from}_to_{date_to}.pdf",
    )


@reports_bp.route("/export/chemical-excel")
@login_required
@requires_permission("reports", "export")
def export_chemical_excel():
    """Export chemical analysis report as Excel"""
    date_from = request.args.get(
        "date_from", (date.today() - timedelta(days=7)).isoformat()
    )
    date_to = request.args.get("date_to", date.today().isoformat())

    analyses = (
        ChemicalAnalysis.query.filter(
            ChemicalAnalysis.test_date >= date_from,
            ChemicalAnalysis.test_date <= date_to,
        )
        .order_by(ChemicalAnalysis.test_date.desc())
        .all()
    )

    # Generate Excel using xlsxwriter
    from app.services.report_service import generate_chemical_excel

    excel_buffer = generate_chemical_excel(analyses, date_from, date_to)

    return send_file(
        excel_buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"chemical_report_{date_from}_to_{date_to}.xlsx",
    )


@reports_bp.route("/mechanical-properties")
@login_required
@requires_permission("reports", "view")
def mechanical_properties():
    """Mechanical properties report with statistics"""
    filters = get_filter_values()
    date_from = (
        filters.get("date_from") or (date.today() - timedelta(days=30)).isoformat()
    )
    date_to = filters.get("date_to") or date.today().isoformat()

    query = MechanicalTest.query.filter(
        MechanicalTest.test_date >= date_from, MechanicalTest.test_date <= date_to
    )

    if filters.get("dn"):
        try:
            query = query.filter(MechanicalTest.diameter == int(filters["dn"]))
        except (ValueError, TypeError):
            pass
    if filters.get("decision"):
        query = query.filter(MechanicalTest.decision == filters["decision"])

    tests = query.order_by(MechanicalTest.test_date.desc()).all()

    # Calculate statistics
    stats = {}
    for prop in ["tensile_strength", "elongation", "nodularity_percent", "hardness"]:
        values = [getattr(t, prop) for t in tests if getattr(t, prop) is not None]
        if values:
            stats[prop] = {
                "count": len(values),
                "avg": round(sum(values) / len(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
            }

    total = len(tests)
    accepted = sum(1 for t in tests if t.decision == "ACCEPT")
    rejected = sum(1 for t in tests if t.decision == "REJECT")

    # Pre-serialize chart data (SQLAlchemy objects are not JSON serializable)
    chart_data = [
        {
            "tensile_strength": float(t.tensile_strength)
            if t.tensile_strength
            else None,
            "elongation": float(t.elongation) if t.elongation else None,
            "decision": t.decision,
        }
        for t in tests
    ]

    return render_template(
        "reports/mechanical_properties.html",
        tests=tests,
        stats=stats,
        chart_data=chart_data,
        date_from=date_from,
        date_to=date_to,
        filters=filters,
        summary={
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "rate": round(accepted / total * 100, 1) if total else 0,
        },
    )


@reports_bp.route("/api/dashboard-kpi")
@login_required
@requires_permission("reports", "view")
def api_dashboard_kpi():
    """API endpoint for dashboard KPI data (for Chart.js)"""
    days = request.args.get("days", 7, type=int)
    line = request.args.get("line", "")

    # Production trend (last N days)
    trend_data = []
    for i in range(days - 1, -1, -1):
        d = date.today() - timedelta(days=i)
        query = Pipe.query.filter(Pipe.production_date == d)
        count = query.count()
        trend_data.append({"date": d.isoformat(), "count": count})

    # Accept/Reject counts for pie chart
    week_ago = date.today() - timedelta(days=days)
    total_pipes = Pipe.query.filter(Pipe.production_date >= week_ago).count()
    accepted_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago, Pipe.final_decision_value == "ACCEPT"
    ).count()
    rejected_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago, Pipe.final_decision_value == "REJECT"
    ).count()
    hold_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago, Pipe.final_decision_value == "HOLD"
    ).count()
    other_pipes = total_pipes - accepted_pipes - rejected_pipes - hold_pipes

    # Defect pareto (by stage)
    defect_data = (
        db.session.query(PipeStage.stage_name, func.count(PipeStage.id).label("count"))
        .join(Pipe)
        .filter(Pipe.production_date >= week_ago, PipeStage.has_defect == True)
        .group_by(PipeStage.stage_name)
        .order_by(func.count(PipeStage.id).desc())
        .all()
    )

    defect_pareto = [{"stage": d.stage_name, "count": d.count} for d in defect_data]

    # RFT (Right First Time) - pipes accepted without any rework/defect
    rft_count = 0
    rft_total = 0
    finished_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago, Pipe.final_decision_value.isnot(None)
    ).all()
    for pipe in finished_pipes:
        rft_total += 1
        stages = pipe.stages.all()
        has_defect = any(s.has_defect for s in stages)
        if not has_defect and pipe.final_decision_value == "ACCEPT":
            rft_count += 1

    rft_pct = round(rft_count / rft_total * 100, 1) if rft_total else 0

    return jsonify(
        {
            "trend": trend_data,
            "pie": {
                "accepted": accepted_pipes,
                "rejected": rejected_pipes,
                "hold": hold_pipes,
                "other": other_pipes,
            },
            "defect_pareto": defect_pareto,
            "kpi": {
                "total_production": total_pipes,
                "accepted": accepted_pipes,
                "rejected": rejected_pipes,
                "hold": hold_pipes,
                "rft_pct": rft_pct,
            },
        }
    )


@reports_bp.route("/export/traceability-excel")
@login_required
@requires_permission("reports", "export")
def export_traceability_excel():
    """Full pipe traceability export - multi-sheet Excel"""
    filters = get_filter_values()
    date_from = (
        filters.get("date_from") or (date.today() - timedelta(days=7)).isoformat()
    )
    date_to = filters.get("date_to") or date.today().isoformat()

    query = Pipe.query.filter(
        Pipe.production_date >= date_from, Pipe.production_date <= date_to
    )
    if filters.get("dn"):
        try:
            query = query.filter(Pipe.diameter == int(filters["dn"]))
        except (ValueError, TypeError):
            pass
    if filters.get("final_decision"):
        query = query.filter(Pipe.final_decision_value == filters["final_decision"])

    pipes = query.order_by(Pipe.production_date.desc()).all()

    try:
        import xlsxwriter
    except ImportError:
        from flask import abort

        abort(500, "xlsxwriter not installed")

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)
    header_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#4472C4", "font_color": "white"}
    )

    # Sheet 1: Pipes
    ws = workbook.add_worksheet("Pipes")
    headers = [
        "No Code",
        "Pipe Code",
        "DN",
        "Class",
        "Ladle ID",
        "Date",
        "Shift",
        "Weight",
        "Thickness",
        "Mold",
        "Lab Decision",
        "Final Decision",
        "Order",
    ]
    for i, h in enumerate(headers):
        ws.write(0, i, h, header_fmt)
    for row, p in enumerate(pipes, 1):
        ws.write(row, 0, p.no_code)
        ws.write(row, 1, p.pipe_code)
        ws.write(row, 2, p.diameter)
        ws.write(row, 3, p.pipe_class)
        ws.write(row, 4, p.ladle_id)
        ws.write(row, 5, p.production_date.isoformat() if p.production_date else "")
        ws.write(row, 6, p.shift)
        ws.write(row, 7, float(p.actual_weight) if p.actual_weight else "")
        ws.write(row, 8, float(p.thickness) if p.thickness else "")
        ws.write(row, 9, p.mold_number)
        ws.write(row, 10, p.lab_decision)
        ws.write(row, 11, p.final_decision)
        ws.write(row, 12, p.production_order.order_number if p.production_order else "")

    # Sheet 2: Chemical Analyses
    ladle_ids = list({p.ladle_id for p in pipes if p.ladle_id})
    ws2 = workbook.add_worksheet("Chemical Analyses")
    chem_headers = [
        "Ladle ID",
        "Date",
        "Furnace",
        "C",
        "Si",
        "Mn",
        "S",
        "P",
        "Mg",
        "Cu",
        "Cr",
        "CE",
        "Decision",
    ]
    for i, h in enumerate(chem_headers):
        ws2.write(0, i, h, header_fmt)

    analyses = (
        ChemicalAnalysis.query.filter(ChemicalAnalysis.ladle_id.in_(ladle_ids)).all()
        if ladle_ids
        else []
    )
    for row, a in enumerate(analyses, 1):
        ws2.write(row, 0, a.ladle_id)
        ws2.write(row, 1, a.test_date.isoformat() if a.test_date else "")
        ws2.write(row, 2, a.furnace.furnace_code if a.furnace else "")
        ws2.write(row, 3, float(a.carbon) if a.carbon else "")
        ws2.write(row, 4, float(a.silicon) if a.silicon else "")
        ws2.write(row, 5, float(a.manganese) if a.manganese else "")
        ws2.write(row, 6, float(a.sulfur) if a.sulfur else "")
        ws2.write(row, 7, float(a.phosphorus) if a.phosphorus else "")
        ws2.write(row, 8, float(a.magnesium) if a.magnesium else "")
        ws2.write(row, 9, float(a.copper) if a.copper else "")
        ws2.write(row, 10, float(a.chromium) if a.chromium else "")
        ws2.write(row, 11, float(a.carbon_equivalent) if a.carbon_equivalent else "")
        ws2.write(row, 12, a.decision)

    # Sheet 3: Mechanical Tests
    ws3 = workbook.add_worksheet("Mechanical Tests")
    mech_headers = [
        "Date",
        "Pipe Code",
        "Ladle ID",
        "DN",
        "Tensile",
        "Tensile MPa",
        "Elongation",
        "Nodularity",
        "Hardness",
        "Decision",
        "Status",
    ]
    for i, h in enumerate(mech_headers):
        ws3.write(0, i, h, header_fmt)

    mech_tests = (
        MechanicalTest.query.filter(MechanicalTest.ladle_id.in_(ladle_ids)).all()
        if ladle_ids
        else []
    )
    for row, t in enumerate(mech_tests, 1):
        ws3.write(row, 0, t.test_date.isoformat() if t.test_date else "")
        ws3.write(row, 1, t.pipe_code)
        ws3.write(row, 2, t.ladle_id)
        ws3.write(row, 3, t.diameter)
        ws3.write(row, 4, float(t.tensile_strength) if t.tensile_strength else "")
        ws3.write(row, 5, float(t.tensile_mpa) if t.tensile_mpa else "")
        ws3.write(row, 6, float(t.elongation) if t.elongation else "")
        ws3.write(row, 7, float(t.nodularity_percent) if t.nodularity_percent else "")
        ws3.write(row, 8, float(t.hardness) if t.hardness else "")
        ws3.write(row, 9, t.decision)
        ws3.write(row, 10, t.status)

    # Sheet 4: Stages
    ws4 = workbook.add_worksheet("Stages")
    stage_headers = ["Pipe No Code", "Stage", "Date", "Decision", "Defect", "Notes"]
    for i, h in enumerate(stage_headers):
        ws4.write(0, i, h, header_fmt)

    row = 1
    for p in pipes:
        for stage_name in Pipe.STAGES:
            stage = p.get_stage(stage_name)
            if stage:
                ws4.write(row, 0, p.no_code)
                ws4.write(row, 1, stage_name)
                ws4.write(
                    row, 2, stage.stage_date.isoformat() if stage.stage_date else ""
                )
                ws4.write(row, 3, stage.decision)
                ws4.write(row, 4, stage.defect_type if stage.has_defect else "")
                ws4.write(row, 5, stage.notes)
                row += 1

    workbook.close()
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"traceability_{date_from}_to_{date_to}.xlsx",
    )


@reports_bp.route("/api/ai-summary/<report_type>")
@login_required
@requires_permission("reports", "view")
def api_ai_summary(report_type):
    """API endpoint for AI report summary"""
    date_from = request.args.get(
        "date_from", (date.today() - timedelta(days=7)).isoformat()
    )
    date_to = request.args.get("date_to", date.today().isoformat())

    if report_type == "chemical":
        analyses = ChemicalAnalysis.query.filter(
            ChemicalAnalysis.test_date >= date_from,
            ChemicalAnalysis.test_date <= date_to,
        ).all()

        total = len(analyses)
        accepted = sum(1 for a in analyses if a.decision == "ACCEPT")
        rejected = sum(1 for a in analyses if a.decision == "REJECT")
        defects = sum(1 for a in analyses if a.has_defect)

        data = {
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "defects": defects,
            "rate": round(accepted / total * 100, 1) if total > 0 else 0,
        }

    elif report_type == "defect":
        chem_defects = ChemicalAnalysis.query.filter(
            ChemicalAnalysis.test_date >= date_from,
            ChemicalAnalysis.test_date <= date_to,
            ChemicalAnalysis.has_defect == True,
        ).count()

        stage_defects = (
            db.session.query(PipeStage)
            .join(Pipe)
            .filter(
                Pipe.production_date >= date_from,
                Pipe.production_date <= date_to,
                PipeStage.has_defect == True,
            )
            .all()
        )

        defects_by_stage = {}
        for stage in stage_defects:
            name = stage.stage_name
            defects_by_stage[name] = defects_by_stage.get(name, 0) + 1

        data = {
            "date_from": date_from,
            "date_to": date_to,
            "chem_defects_count": chem_defects,
            "defects_by_stage": defects_by_stage,
        }

    else:  # production
        report_date = request.args.get("date", date.today().isoformat())
        pipes = Pipe.query.filter_by(production_date=report_date).all()

        by_diameter = {}
        for pipe in pipes:
            dn = pipe.diameter or "Unknown"
            by_diameter[dn] = by_diameter.get(dn, 0) + 1

        data = {"date": report_date, "total": len(pipes), "by_diameter": by_diameter}

    result = generate_report_summary(report_type, data)
    return jsonify(result)
