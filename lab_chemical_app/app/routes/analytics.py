"""
Analytics routes — Pivot, KPI designer, Dashboard designer, Query Manager,
Power BI export, and Excel import.

These live under /analytics. Heavy lifting is intentionally kept server-side to
avoid shipping a full JS pivot library; the pivot engine uses itertools + the
existing analytics_service helpers.
"""

import csv
import io
import os
import json
from datetime import datetime, date

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    flash,
    redirect,
    url_for,
)
from flask_login import login_required, current_user
from sqlalchemy import text, inspect

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.production_order import ProductionOrder
from app.services.permission_service import requires_permission

analytics_bp = Blueprint("analytics", __name__)


# ---------------------------------------------------------------------------
# Pivot table
# ---------------------------------------------------------------------------

PIVOT_SOURCES = {
    "pipes": {
        "label": "Pipes",
        "model": Pipe,
        "row_fields": {
            "diameter": lambda p: f"DN{p.diameter}" if p.diameter else "Unknown",
            "pipe_class": lambda p: p.pipe_class or "Unknown",
            "machine": lambda p: p.machine.machine_code if p.machine else "Unknown",
            "mold_number": lambda p: p.mold_number or "Unknown",
            "shift": lambda p: f"Shift {p.shift}" if p.shift else "Unknown",
            "month": lambda p: (
                p.production_date.strftime("%Y-%m") if p.production_date else "Unknown"
            ),
            "customer": lambda p: (
                p.production_order.customer_name if p.production_order else "Unknown"
            ),
            "final_decision": lambda p: p.final_decision_value or "PENDING",
        },
        "value_fields": {
            "count": lambda p: 1,
            "iso_weight": lambda p: float(p.iso_weight or 0),
            "actual_weight": lambda p: float(p.actual_weight or 0),
            "saving_weight": lambda p: float(
                (p.iso_weight or 0) - (p.actual_weight or 0)
            ),
        },
    },
    "chemical": {
        "label": "Chemical Analysis",
        "model": ChemicalAnalysis,
        "row_fields": {
            "furnace": lambda c: c.furnace.furnace_code if c.furnace else "Unknown",
            "decision": lambda c: c.decision or "Unknown",
            "month": lambda c: (
                c.test_date.strftime("%Y-%m") if c.test_date else "Unknown"
            ),
        },
        "value_fields": {
            "count": lambda c: 1,
            "avg_carbon": lambda c: float(c.carbon or 0),
            "avg_silicon": lambda c: float(c.silicon or 0),
            "avg_magnesium": lambda c: float(c.magnesium or 0),
        },
    },
    "mechanical": {
        "label": "Mechanical Tests",
        "model": MechanicalTest,
        "row_fields": {
            "diameter": lambda m: f"DN{m.diameter}" if m.diameter else "Unknown",
            "decision": lambda m: m.decision or "Unknown",
            "month": lambda m: (
                m.test_date.strftime("%Y-%m") if m.test_date else "Unknown"
            ),
        },
        "value_fields": {
            "count": lambda m: 1,
            "avg_tensile_mpa": lambda m: float(m.tensile_mpa or 0),
            "avg_elongation": lambda m: float(m.elongation or 0),
            "avg_hardness": lambda m: float(m.hardness or 0),
        },
    },
}


@analytics_bp.route("/pivot")
@login_required
@requires_permission('analytics', 'pivot')
def pivot():
    """Server-side pivot table. User picks source, rows, cols, value, aggregation."""
    source = request.args.get("source", "pipes")
    row_key = request.args.get("rows", "diameter")
    col_key = request.args.get("cols", "final_decision")
    value_key = request.args.get("value", "count")
    aggregation = request.args.get("agg", "sum")  # sum | avg | count

    cfg = PIVOT_SOURCES.get(source)
    if not cfg:
        return "Unknown source", 400

    records = cfg["model"].query.limit(5000).all()

    row_fn = cfg["row_fields"].get(
        row_key, cfg["row_fields"][list(cfg["row_fields"].keys())[0]]
    )
    col_fn = cfg["row_fields"].get(
        col_key, cfg["row_fields"][list(cfg["row_fields"].keys())[0]]
    )
    val_fn = cfg["value_fields"].get(value_key, cfg["value_fields"]["count"])

    # Build pivot grid: {row: {col: [values]}}
    grid = {}
    row_totals = {}
    col_totals = {}
    for rec in records:
        r_key = row_fn(rec)
        c_key = col_fn(rec)
        val = val_fn(rec)
        grid.setdefault(r_key, {}).setdefault(c_key, []).append(val)
        row_totals.setdefault(r_key, []).append(val)
        col_totals.setdefault(c_key, []).append(val)

    def aggregate(values):
        if not values:
            return 0
        if aggregation == "sum":
            return round(sum(values), 2)
        if aggregation == "avg":
            return round(sum(values) / len(values), 2)
        return len(values)  # count

    # Materialise into sorted rows/cols
    sorted_rows = sorted(grid.keys())
    sorted_cols = sorted({c for row in grid.values() for c in row.keys()})

    table = {
        "row_labels": sorted_rows,
        "col_labels": sorted_cols,
        "cells": {
            r: {c: aggregate(grid.get(r, {}).get(c, [])) for c in sorted_cols}
            for r in sorted_rows
        },
        "row_totals": {r: aggregate(row_totals.get(r, [])) for r in sorted_rows},
        "col_totals": {c: aggregate(col_totals.get(c, [])) for c in sorted_cols},
        "grand_total": aggregate(
            [v for r in grid.values() for vs in r.values() for v in vs]
        ),
    }

    return render_template(
        "analytics/pivot.html",
        sources=PIVOT_SOURCES,
        source=source,
        row_key=row_key,
        col_key=col_key,
        value_key=value_key,
        aggregation=aggregation,
        table=table,
        record_count=len(records),
    )


# ---------------------------------------------------------------------------
# KPI designer
# ---------------------------------------------------------------------------

KPI_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "kpis.json"
)


def _load_kpis():
    if not os.path.exists(KPI_STORE_PATH):
        return []
    try:
        with open(KPI_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_kpis(kpis):
    os.makedirs(os.path.dirname(KPI_STORE_PATH), exist_ok=True)
    with open(KPI_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(kpis, f, indent=2, ensure_ascii=False)


KPI_FORMULAS = {
    "total_pipes": {
        "label": "Total Pipes Produced",
        "compute": lambda: Pipe.query.count(),
        "unit": "pipes",
    },
    "accepted_pipes": {
        "label": "Accepted Pipes",
        "compute": lambda: Pipe.query.filter(
            Pipe.final_decision_value == "ACCEPT"
        ).count(),
        "unit": "pipes",
    },
    "rft_percent": {
        "label": "RFT %",
        "compute": lambda: round(
            Pipe.query.filter(Pipe.final_decision_value == "ACCEPT").count()
            / max(Pipe.query.count(), 1)
            * 100,
            1,
        ),
        "unit": "%",
    },
    "total_weight_saving": {
        "label": "Total Weight Saving",
        "compute": lambda: round(
            db.session.query(db.func.sum(Pipe.iso_weight - Pipe.actual_weight)).scalar()
            or 0,
            2,
        ),
        "unit": "kg",
    },
    "total_ladles": {
        "label": "Total Ladles",
        "compute": lambda: ChemicalAnalysis.query.count(),
        "unit": "ladles",
    },
    "active_mech_tests": {
        "label": "Active Mechanical Tests",
        "compute": lambda: MechanicalTest.query.filter(
            MechanicalTest.status == "ACTIVE"
        ).count(),
        "unit": "tests",
    },
}


# A saved crosstab carries the selection that produced it, not a number, so it
# recomputes against current data every time it is shown. The six KPI_FORMULAS
# above are whole-database lambdas that take no arguments and therefore cannot
# express "this pivot, under these filters" at all — hence a second kind rather
# than another formula.
CROSSTAB_KIND = "crosstab"


def _crosstab_selection_from_args(args):
    """The parts of a crosstab query string worth persisting."""
    from app.services import crosstab_service

    measures = [m for m in args.getlist("xt_m") if m in crosstab_service.MEASURES]
    return {
        "pivot": [d for d in args.getlist("xt_pivot")
                  if d in crosstab_service.DIMENSIONS],
        "rows": [d for d in args.getlist("xt_rows")
                 if d in crosstab_service.DIMENSIONS],
        "measures": measures or list(crosstab_service.DEFAULT_MEASURES),
        "measure": (args.get("xt_kpi_measure")
                    or (measures[0] if measures else "rej_pct")),
        "sort": args.get("xt_sort", "total_desc"),
        "row_limit": args.get("xt_limit", 20, type=int),
        # Only meaningful for a saved chart, harmless on the other kinds.
        "chart_type": (args.get("xt_chart_type")
                       if args.get("xt_chart_type") in ("bar", "line", "doughnut")
                       else "bar"),
        # Only the filters that actually narrow pipes; the rest of the query
        # string is UI state and would make the saved view brittle.
        "filters": {
            k: v for k, v in args.items()
            if k in ("date_from", "date_to", "diameter", "pipe_class",
                     "production_order_id", "machine_id", "mold_number",
                     "shift", "customer", "line")
        },
    }


def _compute_crosstab_kpi(kpi):
    """Recompute a saved crosstab. Returns (value, crosstab_or_None).

    Never raises: a saved view whose dimension was renamed or whose data has
    gone must show as unavailable on the dashboard, not take the page down.
    """
    from app.services import analytics_service, crosstab_service

    sel = kpi.get("selection") or {}
    try:
        filters = analytics_service.parse_filters(
            _ArgsShim(sel.get("filters") or {}))
        ct = crosstab_service.crosstab(
            filters,
            pivot_dims=sel.get("pivot") or [],
            row_dims=sel.get("rows") or [],
            sort=sel.get("sort", "total_desc"),
            row_limit=sel.get("row_limit", 20),
            measures=sel.get("measures"),
        )
    except Exception:
        return None, None
    return ct["grand_total"].get(sel.get("measure")), ct


class _ArgsShim(dict):
    """parse_filters expects a request-args object: .get(key, type=...)."""

    def get(self, key, default=None, type=None):  # noqa: A002 - mirrors werkzeug
        value = dict.get(self, key, default)
        if value in (None, "") or type is None:
            return value if value not in ("",) else default
        try:
            return type(value)
        except (TypeError, ValueError):
            return default


def _kpi_value(kpi):
    """Live value for a KPI of either kind, or None when unavailable."""
    if kpi.get("kind") == CROSSTAB_KIND:
        return _compute_crosstab_kpi(kpi)[0]
    formula = KPI_FORMULAS.get(kpi.get("formula"))
    if not formula:
        return None
    try:
        return formula["compute"]()
    except Exception:
        return None


@analytics_bp.route("/kpis/save-crosstab", methods=["POST"])
@login_required
@requires_permission('analytics', 'kpis_manage')
def save_crosstab_kpi():
    """Pin the current crosstab selection to "لوحتي" as a tile or a table."""
    kpis = _load_kpis()
    render = request.form.get("render")
    render = render if render in ("table", "chart") else "tile"
    name = (request.form.get("name") or "").strip()
    selection = _crosstab_selection_from_args(request.args)
    kpis.append({
        "id": max([k["id"] for k in kpis], default=0) + 1,
        "name": name or {"table": "جدول محوري محفوظ",
                         "chart": "رسم محوري محفوظ"}.get(
                             render, "مؤشر محوري محفوظ"),
        "kind": CROSSTAB_KIND,
        "render": render,
        "selection": selection,
        "formula": None,
        "target": float(request.form.get("target") or 0),
        "green_threshold": 0.0,
        "red_threshold": 0.0,
        "created_by": current_user.username,
        "created_at": datetime.utcnow().isoformat(),
    })
    _save_kpis(kpis)
    flash("تم الحفظ في لوحتي", "success")
    return redirect(url_for("analytics.kpis"))


@analytics_bp.route("/kpis")
@login_required
@requires_permission('analytics', 'kpis')
def kpis():
    """KPI designer — list all KPIs, compute values for dashboard tiles."""
    kpis = _load_kpis()
    # Compute live values. A saved table needs its whole crosstab, not just the
    # headline number, so the page can render the grid.
    for k in kpis:
        if k.get("kind") == CROSSTAB_KIND:
            k["current_value"], ct = _compute_crosstab_kpi(k)
            k["crosstab"] = (ct if k.get("render") in ("table", "chart")
                             else None)
        else:
            k["current_value"] = _kpi_value(k)
    return render_template(
        "analytics/kpis.html",
        kpis=kpis,
        formulas=KPI_FORMULAS,
    )


@analytics_bp.route("/kpis/add", methods=["POST"])
@login_required
@requires_permission('analytics', 'kpis_manage')
def add_kpi():
    if not current_user.is_admin:
        flash("Only admins can add KPIs", "error")
        return redirect(url_for("analytics.kpis"))
    kpis = _load_kpis()
    kpis.append(
        {
            "id": max([k["id"] for k in kpis], default=0) + 1,
            "name": request.form.get("name", "").strip(),
            "formula": request.form.get("formula", ""),
            "target": float(request.form.get("target") or 0),
            "green_threshold": float(request.form.get("green_threshold") or 0),
            "red_threshold": float(request.form.get("red_threshold") or 0),
            "created_by": current_user.username,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    _save_kpis(kpis)
    flash("KPI added", "success")
    return redirect(url_for("analytics.kpis"))


@analytics_bp.route("/kpis/<int:kpi_id>/delete", methods=["POST"])
@login_required
@requires_permission('analytics', 'kpis_manage')
def delete_kpi(kpi_id):
    if not current_user.is_admin:
        flash("Only admins can delete KPIs", "error")
        return redirect(url_for("analytics.kpis"))
    kpis = _load_kpis()
    kpis = [k for k in kpis if k.get("id") != kpi_id]
    _save_kpis(kpis)
    flash("KPI deleted", "success")
    return redirect(url_for("analytics.kpis"))


# ---------------------------------------------------------------------------
# Dashboard designer
# ---------------------------------------------------------------------------

DASHBOARD_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "dashboards.json"
)


def _load_dashboards():
    if not os.path.exists(DASHBOARD_STORE_PATH):
        return []
    try:
        with open(DASHBOARD_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_dashboards(d):
    os.makedirs(os.path.dirname(DASHBOARD_STORE_PATH), exist_ok=True)
    with open(DASHBOARD_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


@analytics_bp.route("/dashboards")
@login_required
@requires_permission('analytics', 'dashboards')
def dashboards():
    dashes = _load_dashboards()
    # Compute live KPI values for each dashboard's tiles
    all_kpis = {k["id"]: k for k in _load_kpis()}
    for d in dashes:
        d["tiles"] = []
        for kpi_id in d.get("kpi_ids", []):
            kpi = all_kpis.get(kpi_id)
            if not kpi:
                continue
            if kpi.get("kind") == CROSSTAB_KIND:
                value, ct = _compute_crosstab_kpi(kpi)
                # A saved table carries its whole crosstab so the dashboard can
                # render the grid, not just one number.
                kpi = {**kpi, "current_value": value,
                       "crosstab": ct if kpi.get("render") in ("table", "chart")
                       else None}
            else:
                kpi = {**kpi, "current_value": _kpi_value(kpi)}
            d["tiles"].append(kpi)
    return render_template(
        "analytics/dashboards.html",
        dashboards=dashes,
        available_kpis=_load_kpis(),
    )


@analytics_bp.route("/dashboards/add", methods=["POST"])
@login_required
@requires_permission('analytics', 'dashboards_manage')
def add_dashboard():
    if not current_user.is_admin:
        flash("Only admins can create dashboards", "error")
        return redirect(url_for("analytics.dashboards"))
    dashes = _load_dashboards()
    kpi_ids = [int(k) for k in request.form.getlist("kpi_ids")]
    dashes.append(
        {
            "id": max([d["id"] for d in dashes], default=0) + 1,
            "name": request.form.get("name", "").strip(),
            "description": request.form.get("description", ""),
            "kpi_ids": kpi_ids,
            "created_by": current_user.username,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    _save_dashboards(dashes)
    flash("Dashboard created", "success")
    return redirect(url_for("analytics.dashboards"))


# ---------------------------------------------------------------------------
# Query Manager (admin-only, read-only SELECT)
# ---------------------------------------------------------------------------

# INNER JOIN presets across the main tables. SQL is generated with every
# column of every joined table, prefixed (<table>_<column>) so same-named
# columns (id, created_at, …) never collide.
_JOIN_PRESET_DEFS = [
    (
        "Pipes + Chemical",
        "pipes INNER JOIN chemical_analyses ON pipes.ladle_id = chemical_analyses.ladle_id",
        ["pipes", "chemical_analyses"],
    ),
    (
        "Pipes + Stages",
        "pipes INNER JOIN pipe_stages ON pipe_stages.pipe_id = pipes.id",
        ["pipes", "pipe_stages"],
    ),
    (
        "Pipes + Orders",
        "pipes INNER JOIN production_orders ON pipes.production_order_id = production_orders.id",
        ["pipes", "production_orders"],
    ),
    (
        "Pipes + Mechanical",
        "pipes INNER JOIN mechanical_tests ON mechanical_tests.pipe_id = pipes.id",
        ["pipes", "mechanical_tests"],
    ),
    (
        "Chemical + Pipes + Mechanical",
        "chemical_analyses INNER JOIN pipes ON pipes.ladle_id = chemical_analyses.ladle_id "
        "INNER JOIN mechanical_tests ON mechanical_tests.ladle_id = chemical_analyses.ladle_id",
        ["chemical_analyses", "pipes", "mechanical_tests"],
    ),
    (
        "Everything (Orders + Pipes + Chemical + Stages)",
        "production_orders INNER JOIN pipes ON pipes.production_order_id = production_orders.id "
        "INNER JOIN chemical_analyses ON chemical_analyses.ladle_id = pipes.ladle_id "
        "INNER JOIN pipe_stages ON pipe_stages.pipe_id = pipes.id",
        ["production_orders", "pipes", "chemical_analyses", "pipe_stages"],
    ),
]


def _join_presets():
    """Build preset SELECTs with every column of every joined table,
    prefixed as <table>.<col> AS <table>_<col> to avoid name collisions."""
    insp = inspect(db.engine)
    presets = []
    for name, from_clause, tables in _JOIN_PRESET_DEFS:
        try:
            select_parts = []
            for tname in tables:
                for c in insp.get_columns(tname):
                    select_parts.append(f'{tname}.{c["name"]} AS {tname}_{c["name"]}')
            presets.append(
                {
                    "name": name,
                    "sql": "SELECT\n  " + ",\n  ".join(select_parts)
                    + f"\nFROM {from_clause}\nLIMIT 100;",
                }
            )
        except Exception:
            continue
    return presets


@analytics_bp.route("/query-manager", methods=["GET", "POST"])
@login_required
@requires_permission('analytics', 'query_manager')
def query_manager():
    if not current_user.is_admin:
        flash("Only admins can access the Query Manager", "error")
        return redirect(url_for("analytics.pivot"))

    result_rows = None
    columns = None
    error = None
    check_ok = None
    query_text = ""

    # List all tables with row counts
    insp = inspect(db.engine)
    tables = []
    for tname in insp.get_table_names():
        try:
            count = db.session.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()
        except Exception:
            count = 0
        tables.append(
            {
                "name": tname,
                "row_count": count,
                "columns": [c["name"] for c in insp.get_columns(tname)],
            }
        )
    tables.sort(key=lambda t: t["name"])

    if request.method == "POST":
        query_text = request.form.get("query", "").strip()
        action = request.form.get("action", "run")
        # SAFETY: only allow SELECT statements
        normalized = query_text.lower().lstrip()
        if not normalized.startswith("select"):
            error = "Only SELECT queries are permitted."
        elif action == "check":
            # Validate without running: EXPLAIN parses + plans the statement.
            try:
                db.session.execute(text(f"EXPLAIN {query_text}"))
                check_ok = "Query is valid."
            except Exception as e:
                error = f"Query check failed: {e}"
        else:
            try:
                result = db.session.execute(text(query_text))
                columns = list(result.keys())
                result_rows = [list(row) for row in result.fetchmany(500)]
            except Exception as e:
                error = f"Query error: {e}"

    return render_template(
        "analytics/query_manager.html",
        tables=tables,
        presets=_join_presets(),
        query=query_text,
        columns=columns,
        rows=result_rows,
        error=error,
        check_ok=check_ok,
    )


@analytics_bp.route("/query-manager/export", methods=["POST"])
@login_required
@requires_permission('analytics', 'query_manager')
def query_manager_export():
    if not current_user.is_admin:
        return "Forbidden", 403
    query_text = request.form.get("query", "").strip()
    if not query_text.lower().lstrip().startswith("select"):
        return "Only SELECT queries are permitted", 400
    try:
        result = db.session.execute(text(query_text))
        columns = list(result.keys())
        rows = result.fetchall()
    except Exception as e:
        return f"Query error: {e}", 400

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(list(row))
    output = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="query_result.csv",
    )


# ---------------------------------------------------------------------------
# Historical data import (admin-only)
# ---------------------------------------------------------------------------
#
# Generic importer: every main table, every column. The old handler supported
# only pipes + chemical with a hardcoded ~10-column subset. Columns are mapped
# dynamically from the model (CSV/XLSX header == column name), with type
# coercion from the column type and FK-resolution virtual columns
# (pipe_no_code -> pipe_id, furnace_code -> furnace_id).


def _import_skip_columns():
    return {
        "id", "created_at", "updated_at",
        "created_by_id", "modified_by_id", "updated_by_id",
    }


def _import_columns(model):
    """All importable columns for a model (everything but PK/timestamps)."""
    skip = _import_skip_columns()
    return [c.name for c in inspect(model).columns if c.name not in skip]


def _coerce_value(raw, col_type):
    """Coerce a CSV string / XLSX cell value to the column's Python type."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return None
    if isinstance(col_type, db.Boolean):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y")
    if isinstance(col_type, db.Integer):
        return int(float(raw))
    if isinstance(col_type, (db.Float, db.Numeric)):
        return float(raw)
    if isinstance(col_type, db.DateTime):
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, date):
            return datetime(raw.year, raw.month, raw.day)
        return datetime.fromisoformat(str(raw).replace("Z", "").replace(" ", "T"))
    if isinstance(col_type, db.Date):
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        return date.fromisoformat(str(raw)[:10])
    if isinstance(col_type, db.Time):
        if isinstance(raw, datetime):
            return raw.time()
        if not isinstance(raw, str) and hasattr(raw, "hour"):
            return raw  # datetime.time already
        s = str(raw)
        return datetime.strptime(s, "%H:%M:%S" if s.count(":") == 2 else "%H:%M").time()
    if isinstance(col_type, db.JSON):
        if isinstance(raw, (dict, list)):
            return raw
        return json.loads(raw)
    return str(raw)


# FK-resolution virtual columns: virtual_name -> (Model, match_column, fk_column)
_IMPORT_FK = {
    "pipe_no_code": (Pipe, "no_code", "pipe_id"),
    "furnace_code": (Furnace, "furnace_code", "furnace_id"),
}

# Legacy header aliases: alias -> real column
_IMPORT_ALIASES = {
    "final_decision": "final_decision_value",
}

IMPORT_TARGETS = {
    "pipes": {
        "label": "Pipes",
        "model": Pipe,
        "unique": ["no_code"],
        "required": [],
        "defaults": {"production_date": date.today},
    },
    "stages": {
        "label": "Pipe Stages",
        "model": PipeStage,
        "unique": ["pipe_id", "stage_name"],
        "required": ["stage_name"],
    },
    "chemical": {
        "label": "Chemical Analysis",
        "model": ChemicalAnalysis,
        "unique": ["ladle_id"],
        "required": ["ladle_id"],
        "defaults": {"test_date": date.today, "ladle_no": 1},
    },
    "mechanical": {
        "label": "Mechanical Tests",
        "model": MechanicalTest,
        "unique": ["pipe_code", "test_date"],
        "required": [],
        "defaults": {"test_date": date.today},
    },
    "orders": {
        "label": "Production Orders",
        "model": ProductionOrder,
        "unique": ["order_number"],
        "required": ["order_number", "target_quantity"],
        "defaults": {"order_date": date.today},
    },
}


def _read_import_rows(file):
    """Yield (row_number, dict) from the uploaded CSV or XLSX file."""
    filename = (file.filename or "").lower()
    if filename.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(file.stream, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows, [])]
        for i, values in enumerate(rows, start=2):
            row = {headers[j]: v for j, v in enumerate(values) if j < len(headers) and headers[j]}
            if any(v is not None and str(v).strip() != "" for v in row.values()):
                yield i, row
    else:
        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        for i, row in enumerate(reader, start=2):
            if any((v or "").strip() for v in row.values() if v):
                yield i, row


def _import_one_row(cfg, row, col_types, unknown_warned):
    """Build a model instance from one row. Returns (instance, warning)."""
    model = cfg["model"]
    columns = set(col_types)
    kwargs = {}
    unknown = []

    for key, raw in row.items():
        key = (key or "").strip()
        if not key:
            continue
        if key in _IMPORT_FK:
            Model, match_col, fk_col = _IMPORT_FK[key]
            if raw is None or str(raw).strip() == "":
                continue
            hit = Model.query.filter_by(**{match_col: str(raw).strip()}).first()
            if not hit:
                raise ValueError(f"{key} '{raw}' not found")
            kwargs[fk_col] = hit.id
            continue
        real = _IMPORT_ALIASES.get(key, key)
        if real not in columns:
            unknown.append(key)
            continue
        kwargs[real] = _coerce_value(raw, col_types[real])

    for col, default in (cfg.get("defaults") or {}).items():
        if kwargs.get(col) is None:
            kwargs[col] = default() if callable(default) else default

    for col in cfg.get("required", []):
        if kwargs.get(col) in (None, ""):
            raise ValueError(f"missing required column: {col}")

    # Dedupe only when every unique field resolved to a value
    unique = cfg.get("unique") or []
    if unique and all(kwargs.get(c) not in (None, "") for c in unique):
        existing = model.query.filter_by(**{c: kwargs[c] for c in unique}).first()
        if existing:
            return None, None  # duplicate — caller counts as skipped

    if "created_by_id" in columns or "created_by_id" in {c.name for c in inspect(model).columns}:
        kwargs["created_by_id"] = current_user.id
    warning = f"unknown columns ignored: {', '.join(unknown)}" if unknown and not unknown_warned else None
    return model(**kwargs), warning


@analytics_bp.route("/import", methods=["GET", "POST"])
@login_required
@requires_permission('analytics', 'import')
def import_data():
    if not current_user.is_admin:
        flash("Only admins can import data", "error")
        return redirect(url_for("main.index"))

    # Column listings for the template's per-target cheat sheet
    target_meta = {
        key: {
            "label": cfg["label"],
            "columns": _import_columns(cfg["model"]),
            "virtuals": [v for v, (m, _, fk) in _IMPORT_FK.items()
                         if fk in _import_columns(cfg["model"])],
            "required": cfg.get("required", []),
        }
        for key, cfg in IMPORT_TARGETS.items()
    }

    messages = []
    if request.method == "POST":
        target = request.form.get("target", "pipes")
        file = request.files.get("file")
        if target not in IMPORT_TARGETS:
            flash("Unknown import target", "error")
            return redirect(url_for("analytics.import_data"))
        if not file or not file.filename:
            flash("No file selected", "error")
            return redirect(url_for("analytics.import_data"))

        cfg = IMPORT_TARGETS[target]
        col_types = {c.name: c.type for c in inspect(cfg["model"]).columns}
        imported = skipped = errors = 0
        try:
            for row_no, row in _read_import_rows(file):
                try:
                    instance, warning = _import_one_row(cfg, row, col_types, bool(messages))
                    if warning:
                        messages.append(f"Row {row_no}: {warning}")
                    if instance is None:
                        skipped += 1
                        key_bits = [f"{c}={row.get(c) or row.get('pipe_no_code')}" for c in cfg.get("unique", [])]
                        messages.append(f"Row {row_no}: duplicate skipped ({', '.join(key_bits)})")
                        continue
                    db.session.add(instance)
                    imported += 1
                except Exception as e:
                    errors += 1
                    if len(messages) < 50:
                        messages.append(f"Row {row_no} skipped: {e}")

            db.session.commit()
            flash(
                f"Imported {imported} rows, {skipped} duplicates skipped, {errors} errors",
                "success" if errors == 0 else "warning",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Import failed: {e}", "error")

    return render_template("analytics/import.html", messages=messages, targets=target_meta)


# ---------------------------------------------------------------------------
# Power BI-ready JSON endpoints
# ---------------------------------------------------------------------------


@analytics_bp.route("/powerbi/pipes.json")
@login_required
@requires_permission('analytics', 'dashboards')
def powerbi_pipes():
    limit = request.args.get("limit", 1000, type=int)
    pipes = Pipe.query.order_by(Pipe.production_date.desc()).limit(limit).all()
    return jsonify(
        [
            {
                "id": p.id,
                "pipe_code": p.pipe_code,
                "no_code": p.no_code,
                "ladle_id": p.ladle_id,
                "diameter": p.diameter,
                "pipe_class": p.pipe_class,
                "production_date": p.production_date.isoformat()
                if p.production_date
                else None,
                "shift": p.shift,
                "iso_weight": p.iso_weight,
                "actual_weight": p.actual_weight,
                "lab_decision": p.lab_decision,
                "final_decision": p.final_decision_value,
                "machine_code": p.machine.machine_code if p.machine else None,
                "mold_number": p.mold_number,
                "production_order": p.production_order.order_number
                if p.production_order
                else None,
                "customer": p.production_order.customer_name
                if p.production_order
                else None,
            }
            for p in pipes
        ]
    )


@analytics_bp.route("/powerbi/chemical.json")
@login_required
@requires_permission('analytics', 'dashboards')
def powerbi_chemical():
    limit = request.args.get("limit", 1000, type=int)
    analyses = (
        ChemicalAnalysis.query.order_by(ChemicalAnalysis.test_date.desc())
        .limit(limit)
        .all()
    )
    return jsonify(
        [
            {
                "id": a.id,
                "ladle_id": a.ladle_id,
                "test_date": a.test_date.isoformat() if a.test_date else None,
                "furnace": a.furnace.furnace_code if a.furnace else None,
                "carbon": a.carbon,
                "silicon": a.silicon,
                "magnesium": a.magnesium,
                "sulfur": a.sulfur,
                "decision": a.decision,
                "carbon_equivalent": a.carbon_equivalent,
                "manganese_equivalent": a.manganese_equivalent,
                "magnesium_equivalent": a.magnesium_equivalent,
            }
            for a in analyses
        ]
    )


@analytics_bp.route("/powerbi/mechanical.json")
@login_required
@requires_permission('analytics', 'dashboards')
def powerbi_mechanical():
    limit = request.args.get("limit", 1000, type=int)
    tests = (
        MechanicalTest.query.filter(MechanicalTest.status == "ACTIVE")
        .order_by(MechanicalTest.test_date.desc())
        .limit(limit)
        .all()
    )
    return jsonify(
        [
            {
                "id": t.id,
                "test_date": t.test_date.isoformat() if t.test_date else None,
                "pipe_code": t.pipe_code,
                "ladle_id": t.ladle_id,
                "diameter": t.diameter,
                "tensile_kgf_mm2": t.tensile_strength,
                "tensile_mpa": t.tensile_mpa,
                "elongation": t.elongation,
                "hardness": t.hardness,
                "nodularity": t.nodularity_percent,
                "carbides": t.carbides,
                "decision": t.decision,
            }
            for t in tests
        ]
    )
