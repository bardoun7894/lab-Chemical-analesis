"""
REST API Routes - /api/v1/
API key authentication for external integrations (Power BI, etc.)
"""

import os
from functools import wraps
from datetime import date, timedelta
from io import BytesIO
from flask import Blueprint, jsonify, request, send_file
from app import db, csrf
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis, Machine
from app.models.mechanical import MechanicalTest
from app.models.production_order import ProductionOrder
from app.models.product import Customer

api_bp = Blueprint("api", __name__)

# Exempt entire API blueprint from CSRF
csrf.exempt(api_bp)


def require_api_key(f):
    """Decorator to require API key authentication"""

    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        expected_key = os.environ.get("API_KEY", "")

        if not expected_key:
            return jsonify(
                {"error": "API not configured. Set API_KEY environment variable."}
            ), 503

        if api_key != expected_key:
            return jsonify({"error": "Invalid or missing API key"}), 401

        return f(*args, **kwargs)

    return decorated


def paginate_query(query):
    """Apply pagination to a query"""
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 500)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination


@api_bp.route("/pipes")
@require_api_key
def list_pipes():
    """List pipes with optional filters"""
    query = Pipe.query

    # Filters
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    dn = request.args.get("dn", type=int)
    pipe_class = request.args.get("pipe_class")
    ladle_id = request.args.get("ladle_id")
    final_decision = request.args.get("final_decision")

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if dn:
        query = query.filter(Pipe.diameter == dn)
    if pipe_class:
        query = query.filter(Pipe.pipe_class == pipe_class)
    if ladle_id:
        query = query.filter(Pipe.ladle_id == ladle_id)
    if final_decision:
        query = query.filter(Pipe.final_decision_value == final_decision)

    query = query.order_by(Pipe.production_date.desc(), Pipe.id.desc())
    pagination = paginate_query(query)

    pipes = []
    for p in pagination.items:
        pipes.append(
            {
                "id": p.id,
                "no_code": p.no_code,
                "pipe_code": p.pipe_code,
                "diameter": p.diameter,
                "pipe_class": p.pipe_class,
                "ladle_id": p.ladle_id,
                "production_date": p.production_date.isoformat()
                if p.production_date
                else None,
                "shift": p.shift,
                "mold_number": p.mold_number,
                "actual_weight": float(p.actual_weight) if p.actual_weight else None,
                "thickness": float(p.thickness) if p.thickness else None,
                "mechanical_test_role": p.mechanical_test_role,
                "lab_decision": p.lab_decision,
                "final_decision": p.final_decision,
                "production_order_id": p.production_order_id,
            }
        )

    return jsonify(
        {
            "data": pipes,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/pipes/<int:id>")
@require_api_key
def get_pipe(id):
    """Get pipe details with all stages"""
    pipe = Pipe.query.get_or_404(id)

    stages = []
    for stage_name in Pipe.STAGES:
        stage = pipe.get_stage(stage_name)
        if stage:
            stages.append(
                {
                    "stage_name": stage.stage_name,
                    "decision": stage.decision,
                    "has_defect": stage.has_defect,
                    "defect_type": stage.defect_type,
                    "stage_date": stage.stage_date.isoformat()
                    if stage.stage_date
                    else None,
                    "measurement_value": float(stage.measurement_value)
                    if stage.measurement_value
                    else None,
                    "notes": stage.notes,
                }
            )

    return jsonify(
        {
            "id": pipe.id,
            "no_code": pipe.no_code,
            "pipe_code": pipe.pipe_code,
            "diameter": pipe.diameter,
            "pipe_class": pipe.pipe_class,
            "ladle_id": pipe.ladle_id,
            "production_date": pipe.production_date.isoformat()
            if pipe.production_date
            else None,
            "final_decision": pipe.final_decision,
            "stages": stages,
        }
    )


@api_bp.route("/chemical-analyses")
@require_api_key
def list_chemical():
    """List chemical analyses"""
    query = ChemicalAnalysis.query

    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    decision = request.args.get("decision")

    if date_from:
        query = query.filter(ChemicalAnalysis.test_date >= date_from)
    if date_to:
        query = query.filter(ChemicalAnalysis.test_date <= date_to)
    if decision:
        query = query.filter(ChemicalAnalysis.decision == decision)

    query = query.order_by(ChemicalAnalysis.test_date.desc())
    pagination = paginate_query(query)

    items = []
    for a in pagination.items:
        items.append(
            {
                "id": a.id,
                "test_date": a.test_date.isoformat() if a.test_date else None,
                "ladle_id": a.ladle_id,
                "ladle_no": a.ladle_no,
                "furnace_id": a.furnace_id,
                "decision": a.decision,
                "reason": a.reason,
                "carbon": float(a.carbon) if a.carbon else None,
                "silicon": float(a.silicon) if a.silicon else None,
                "manganese": float(a.manganese) if a.manganese else None,
                "sulfur": float(a.sulfur) if a.sulfur else None,
                "phosphorus": float(a.phosphorus) if a.phosphorus else None,
                "magnesium": float(a.magnesium) if a.magnesium else None,
                "copper": float(a.copper) if a.copper else None,
                "chromium": float(a.chromium) if a.chromium else None,
                "carbon_equivalent": float(a.carbon_equivalent)
                if a.carbon_equivalent
                else None,
                "has_defect": a.has_defect,
            }
        )

    return jsonify(
        {
            "data": items,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/mechanical-tests")
@require_api_key
def list_mechanical():
    """List mechanical tests"""
    query = MechanicalTest.query

    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    decision = request.args.get("decision")

    if date_from:
        query = query.filter(MechanicalTest.test_date >= date_from)
    if date_to:
        query = query.filter(MechanicalTest.test_date <= date_to)
    if decision:
        query = query.filter(MechanicalTest.decision == decision)

    query = query.order_by(MechanicalTest.test_date.desc())
    pagination = paginate_query(query)

    items = []
    for t in pagination.items:
        items.append(
            {
                "id": t.id,
                "test_date": t.test_date.isoformat() if t.test_date else None,
                "pipe_code": t.pipe_code,
                "ladle_id": t.ladle_id,
                "diameter": t.diameter,
                "tensile_strength": float(t.tensile_strength)
                if t.tensile_strength
                else None,
                "tensile_mpa": float(t.tensile_mpa) if t.tensile_mpa else None,
                "elongation": float(t.elongation) if t.elongation else None,
                "nodularity_percent": float(t.nodularity_percent)
                if t.nodularity_percent
                else None,
                "hardness": float(t.hardness) if t.hardness else None,
                "decision": t.decision,
                "status": t.status,
            }
        )

    return jsonify(
        {
            "data": items,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/production-orders")
@require_api_key
def list_orders():
    """List production orders"""
    query = ProductionOrder.query

    status = request.args.get("status")
    if status:
        query = query.filter(ProductionOrder.status == status)

    query = query.order_by(ProductionOrder.order_date.desc())
    pagination = paginate_query(query)

    items = []
    for o in pagination.items:
        items.append(
            {
                "id": o.id,
                "order_number": o.order_number,
                "customer_name": o.customer_name,
                "target_quantity": o.target_quantity,
                "produced_quantity": o.produced_quantity,
                "completed_quantity": o.completed_quantity,
                "progress_percentage": o.progress_percentage,
                "diameter": o.diameter,
                "pipe_class": o.pipe_class,
                "status": o.status,
                "order_date": o.order_date.isoformat() if o.order_date else None,
            }
        )

    return jsonify(
        {
            "data": items,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/customers")
@require_api_key
def list_customers():
    """List customers for ERP integration"""
    query = Customer.query

    is_active = request.args.get("is_active")
    if is_active:
        query = query.filter_by(is_active=is_active.lower() == "true")

    query = query.order_by(Customer.name_en)
    pagination = paginate_query(query)

    items = []
    for c in pagination.items:
        items.append(
            {
                "id": c.id,
                "code": c.code,
                "name_en": c.name_en,
                "name_ar": c.name_ar,
                "is_active": c.is_active,
            }
        )

    return jsonify(
        {
            "data": items,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/production-orders/<int:id>")
@require_api_key
def get_production_order(id):
    """Get production order details with pipes"""
    order = ProductionOrder.query.get_or_404(id)

    pipes = Pipe.query.filter_by(production_order_id=order.id).all()
    ladles = ChemicalAnalysis.query.filter_by(production_order_id=order.id).all()

    return jsonify(
        {
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "target_quantity": order.target_quantity,
            "produced_quantity": order.produced_quantity,
            "completed_quantity": order.completed_quantity,
            "diameter": order.diameter,
            "pipe_class": order.pipe_class,
            "status": order.status,
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "pipes": [
                {
                    "id": p.id,
                    "no_code": p.no_code,
                    "diameter": p.diameter,
                    "final_decision": p.final_decision,
                }
                for p in pipes
            ],
            "ladles": [
                {
                    "ladle_id": l.ladle_id,
                    "decision": l.decision,
                    "test_date": l.test_date.isoformat() if l.test_date else None,
                }
                for l in ladles
            ],
        }
    )


@api_bp.route("/stages")
@require_api_key
def list_stages():
    """List all stage records with filters"""
    pipe_id = request.args.get("pipe_id", type=int)
    stage_name = request.args.get("stage_name")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    query = PipeStage.query

    if pipe_id:
        query = query.filter_by(pipe_id=pipe_id)
    if stage_name:
        query = query.filter_by(stage_name=stage_name)
    if date_from:
        query = query.filter(PipeStage.stage_date >= date_from)
    if date_to:
        query = query.filter(PipeStage.stage_date <= date_to)

    query = query.order_by(PipeStage.stage_date.desc())
    pagination = paginate_query(query)

    items = []
    for s in pagination.items:
        items.append(
            {
                "id": s.id,
                "pipe_id": s.pipe_id,
                "pipe_no_code": s.pipe.no_code if s.pipe else None,
                "stage_name": s.stage_name,
                "decision": s.decision,
                "has_defect": s.has_defect,
                "defect_type": s.defect_type,
                "stage_date": s.stage_date.isoformat() if s.stage_date else None,
                "machine_id": s.machine_id,
                "notes": s.notes,
            }
        )

    return jsonify(
        {
            "data": items,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    )


@api_bp.route("/dashboard/kpi")
@require_api_key
def dashboard_kpi():
    """Get dashboard KPI data for ERP BI"""
    days = request.args.get("days", 7, type=int)
    date_from = (date.today() - timedelta(days=days)).isoformat()
    date_to = date.today().isoformat()

    # Total pipes
    total_pipes = Pipe.query.filter(
        Pipe.production_date >= date_from, Pipe.production_date <= date_to
    ).count()

    # By decision
    accepted = Pipe.query.filter(
        Pipe.production_date >= date_from,
        Pipe.production_date <= date_to,
        Pipe.final_decision_value == "ACCEPT",
    ).count()

    rejected = Pipe.query.filter(
        Pipe.production_date >= date_from,
        Pipe.production_date <= date_to,
        Pipe.final_decision_value == "REJECT",
    ).count()

    hold = Pipe.query.filter(
        Pipe.production_date >= date_from,
        Pipe.production_date <= date_to,
        Pipe.final_decision_value == "HOLD",
    ).count()

    # By diameter
    diameter_stats = (
        db.session.query(Pipe.diameter, func.count(Pipe.id).label("count"))
        .filter(Pipe.production_date >= date_from, Pipe.production_date <= date_to)
        .group_by(Pipe.diameter)
        .all()
    )

    by_diameter = {d.diameter: d.count for d in diameter_stats if d.diameter}

    # By shift
    shift_stats = (
        db.session.query(Pipe.shift, func.count(Pipe.id).label("count"))
        .filter(Pipe.production_date >= date_from, Pipe.production_date <= date_to)
        .group_by(Pipe.shift)
        .all()
    )

    by_shift = {s.shift: s.count for s in shift_stats if s.shift}

    return jsonify(
        {
            "period": {"from": date_from, "to": date_to},
            "total_pipes": total_pipes,
            "accepted": accepted,
            "rejected": rejected,
            "hold": hold,
            "acceptance_rate": round(accepted / total_pipes * 100, 1)
            if total_pipes > 0
            else 0,
            "by_diameter": by_diameter,
            "by_shift": by_shift,
        }
    )


@api_bp.route("/export/full-traceability")
@require_api_key
def export_full_traceability():
    """Full traceability export - multi-sheet Excel for ERP/PowerBI"""
    date_from = request.args.get(
        "date_from", (date.today() - timedelta(days=30)).isoformat()
    )
    date_to = request.args.get("date_to", date.today().isoformat())
    format = request.args.get("format", "excel")

    query = Pipe.query.filter(
        Pipe.production_date >= date_from, Pipe.production_date <= date_to
    )

    pipes = query.order_by(Pipe.production_date.desc()).all()

    if format == "json":
        data = []
        for p in pipes:
            chem = (
                ChemicalAnalysis.query.filter_by(ladle_id=p.ladle_id).first()
                if p.ladle_id
                else None
            )
            mech_tests = MechanicalTest.query.filter(
                MechanicalTest.pipe_id == p.id, MechanicalTest.status == "ACTIVE"
            ).all()

            stages_data = []
            for stage_name in Pipe.STAGES:
                stage = p.get_stage(stage_name)
                if stage:
                    stages_data.append(
                        {
                            "stage": stage_name,
                            "decision": stage.decision,
                            "has_defect": stage.has_defect,
                            "date": stage.stage_date.isoformat()
                            if stage.stage_date
                            else None,
                        }
                    )

            data.append(
                {
                    "id": p.id,
                    "no_code": p.no_code,
                    "pipe_code": p.pipe_code,
                    "diameter": p.diameter,
                    "pipe_class": p.pipe_class,
                    "ladle_id": p.ladle_id,
                    "production_date": p.production_date.isoformat()
                    if p.production_date
                    else None,
                    "shift": p.shift,
                    "machine": p.machine.machine_code if p.machine else None,
                    "mold_number": p.mold_number,
                    "actual_weight": p.actual_weight,
                    "iso_weight": p.iso_weight,
                    "chemical": {
                        "decision": chem.decision if chem else None,
                        "carbon": chem.carbon if chem else None,
                        "silicon": chem.silicon if chem else None,
                        "magnesium": chem.magnesium if chem else None,
                    }
                    if chem
                    else None,
                    "mechanical": [
                        {
                            "tensile_strength": m.tensile_strength,
                            "tensile_mpa": m.tensile_mpa,
                            "elongation": m.elongation,
                            "nodularity": m.nodularity_percent,
                            "decision": m.decision,
                        }
                        for m in mech_tests
                    ]
                    if mech_tests
                    else None,
                    "lab_decision": p.lab_decision,
                    "mechanical_test_role": p.mechanical_test_role,
                    "final_decision": p.final_decision_value,
                    "production_order": p.production_order.order_number
                    if p.production_order
                    else None,
                    "stages": stages_data,
                }
            )

        return jsonify(
            {
                "data": data,
                "count": len(data),
                "date_from": date_from,
                "date_to": date_to,
            }
        )

    # Excel format
    try:
        import xlsxwriter
    except ImportError:
        return jsonify({"error": "xlsxwriter not installed"}), 500

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)
    header_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#4472C4", "font_color": "white"}
    )

    # Sheet 1: Pipes Overview
    ws = workbook.add_worksheet("Pipes")
    headers = [
        "No Code",
        "Pipe Code",
        "DN",
        "Class",
        "Ladle ID",
        "Date",
        "Shift",
        "Machine",
        "Mold",
        "Weight",
        "Lab Decision",
        "Mechanical Role",
        "Final Decision",
        "Order",
    ]
    for i, h in enumerate(headers):
        ws.write(0, i, h, header_fmt)
    for row, p in enumerate(pipes, 1):
        ws.write(row, 0, p.no_code or "")
        ws.write(row, 1, p.pipe_code or "")
        ws.write(row, 2, p.diameter or "")
        ws.write(row, 3, p.pipe_class or "")
        ws.write(row, 4, p.ladle_id or "")
        ws.write(row, 5, p.production_date.isoformat() if p.production_date else "")
        ws.write(row, 6, p.shift or "")
        ws.write(row, 7, p.machine.machine_code if p.machine else "")
        ws.write(row, 8, p.mold_number or "")
        ws.write(row, 9, float(p.actual_weight) if p.actual_weight else "")
        ws.write(row, 10, p.lab_decision or "")
        ws.write(row, 11, p.mechanical_test_role or "")
        ws.write(row, 12, p.final_decision_value or "")
        ws.write(row, 13, p.production_order.order_number if p.production_order else "")

    # Sheet 2: Chemical Analyses
    ladle_ids = list({p.ladle_id for p in pipes if p.ladle_id})
    ws2 = workbook.add_worksheet("Chemical")
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
        ws2.write(row, 0, a.ladle_id or "")
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
        ws2.write(row, 12, a.decision or "")

    # Sheet 3: Mechanical Tests
    ws3 = workbook.add_worksheet("Mechanical")
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

    pipe_ids = [p.id for p in pipes]
    mech_tests = (
        MechanicalTest.query.filter(
            MechanicalTest.pipe_id.in_(pipe_ids), MechanicalTest.status == "ACTIVE"
        ).all()
        if pipe_ids
        else []
    )
    for row, t in enumerate(mech_tests, 1):
        ws3.write(row, 0, t.test_date.isoformat() if t.test_date else "")
        ws3.write(row, 1, t.pipe_code or "")
        ws3.write(row, 2, t.ladle_id or "")
        ws3.write(row, 3, t.diameter or "")
        ws3.write(row, 4, float(t.tensile_strength) if t.tensile_strength else "")
        ws3.write(row, 5, float(t.tensile_mpa) if t.tensile_mpa else "")
        ws3.write(row, 6, float(t.elongation) if t.elongation else "")
        ws3.write(row, 7, float(t.nodularity_percent) if t.nodularity_percent else "")
        ws3.write(row, 8, float(t.hardness) if t.hardness else "")
        ws3.write(row, 9, t.decision or "")
        ws3.write(row, 10, t.status or "")

    # Sheet 4: Stages
    ws4 = workbook.add_worksheet("Stages")
    stage_headers = [
        "Pipe No Code",
        "Stage",
        "Date",
        "Decision",
        "Defect",
        "Machine",
        "Notes",
    ]
    for i, h in enumerate(stage_headers):
        ws4.write(0, i, h, header_fmt)

    row = 1
    for p in pipes:
        for stage_name in Pipe.STAGES:
            stage = p.get_stage(stage_name)
            if stage:
                ws4.write(row, 0, p.no_code or "")
                ws4.write(row, 1, stage_name)
                ws4.write(
                    row, 2, stage.stage_date.isoformat() if stage.stage_date else ""
                )
                ws4.write(row, 3, stage.decision or "")
                ws4.write(row, 4, stage.defect_type if stage.has_defect else "")
                ws4.write(row, 5, stage.machine.machine_code if stage.machine else "")
                ws4.write(row, 6, stage.notes or "")
                row += 1

    workbook.close()
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"traceability_{date_from}_to_{date_to}.xlsx",
    )
