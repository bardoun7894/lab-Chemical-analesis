"""
Production Stages Routes
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import date, datetime
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis, Machine, DefectType, DecisionType
from app.models.production_order import ProductionOrder
from app.models.stage_defect_type import StageDefectType
from app.models.stage_decision_type import StageDecisionType
from app.models.stage_history import PipeStageHistory
from app.models.product import Mold
from app.models.user import User


# Standard DN sizes used across the plant
DN_SIZES = [
    80,
    100,
    150,
    200,
    250,
    300,
    350,
    400,
    450,
    500,
    600,
    700,
    800,
    900,
    1000,
    1200,
]

# Available pipe classes
PIPE_CLASSES = ["K9", "C25", "C40", "C50", "C64", "Fittings"]


def _get_shift_engineers():
    """Return users that can be selected as Shift Engineer on the pipe form."""
    return (
        User.query.filter(
            User.is_active.is_(True),
            User.role.in_(["operator", "supervisor", "admin"]),
        )
        .order_by(User.full_name, User.username)
        .all()
    )


def _get_form_context(**extra):
    """Shared context for pipe form render — centralises dropdown data."""
    from app.models.product import Product
    ctx = dict(
        products=Product.query.filter_by(is_active=True).order_by(Product.product_code).all(),
        machines=Machine.query.filter_by(is_active=True).all(),
        molds=Mold.query.filter_by(is_active=True)
        .order_by(Mold.diameter, Mold.mold_number)
        .all(),
        latest_ladles=ChemicalAnalysis.query.order_by(ChemicalAnalysis.id.desc())
        .limit(10)
        .all(),
        production_orders=ProductionOrder.query.filter(
            ProductionOrder.status.in_(["pending", "in_progress"])
        )
        .order_by(ProductionOrder.order_date.desc())
        .all(),
        stage_decisions=get_stage_decisions_from_db(),
        stage_defects=get_stage_defects_from_db(),
        stage_machines=get_stage_machines_from_db(),
        dn_sizes=DN_SIZES,
        pipe_classes=PIPE_CLASSES,
        shift_engineers=_get_shift_engineers(),
        today=date.today(),
    )
    ctx.update(extra)
    return ctx


stages_bp = Blueprint("stages", __name__)


def get_stage_defects_from_db():
    """Get stage defects from database, grouped by stage"""
    defects = (
        StageDefectType.query.filter_by(is_active=True)
        .order_by(StageDefectType.stage_name, StageDefectType.sort_order)
        .all()
    )

    stage_defects = {}
    for defect in defects:
        if defect.stage_name not in stage_defects:
            stage_defects[defect.stage_name] = []
        stage_defects[defect.stage_name].append(
            (defect.defect_name_en, defect.defect_name_ar)
        )

    return stage_defects


def get_stage_decisions_from_db():
    """Get stage decisions from database, grouped by stage"""
    decisions = (
        StageDecisionType.query.filter_by(is_active=True)
        .order_by(StageDecisionType.stage_name, StageDecisionType.sort_order)
        .all()
    )

    stage_decisions = {}
    for decision in decisions:
        if decision.stage_name not in stage_decisions:
            stage_decisions[decision.stage_name] = []
        stage_decisions[decision.stage_name].append(
            (decision.decision_name_en, decision.decision_name_ar)
        )

    return stage_decisions


def get_stage_machines_from_db():
    """Get machines from database, grouped by stage"""
    machines = (
        Machine.query.filter_by(is_active=True)
        .order_by(Machine.stage, Machine.machine_code)
        .all()
    )

    stage_machines = {}
    for machine in machines:
        if machine.stage and machine.stage not in stage_machines:
            stage_machines[machine.stage] = []
        if machine.stage:
            stage_machines[machine.stage].append(
                {
                    "id": machine.id,
                    "code": machine.machine_code,
                    "name": machine.machine_name or machine.machine_code,
                }
            )

    return stage_machines


@stages_bp.route("/")
@login_required
def list():
    """List all pipes with stage status"""
    page = request.args.get("page", 1, type=int)
    per_page = 20

    # Filters
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    diameter = request.args.get("diameter", type=int)
    pipe_class = request.args.get("pipe_class")
    production_order_id = request.args.get("production_order_id", type=int)
    ladle_id = request.args.get("ladle_id")
    shift = request.args.get("shift", type=int)
    final_decision = request.args.get("final_decision")
    machine_id = request.args.get("machine_id", type=int)
    mold_number = request.args.get("mold_number")
    no_code = request.args.get("no_code")
    customer = request.args.get("customer")
    sales_order = request.args.get("sales_order")

    query = Pipe.query

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if diameter:
        query = query.filter(Pipe.diameter == diameter)
    if pipe_class:
        query = query.filter(Pipe.pipe_class == pipe_class)
    if production_order_id:
        query = query.filter(Pipe.production_order_id == production_order_id)
    if ladle_id:
        query = query.filter(Pipe.ladle_id == ladle_id)
    if shift:
        query = query.filter(Pipe.shift == shift)
    if final_decision:
        query = query.filter(Pipe.final_decision_value == final_decision)
    if machine_id:
        query = query.filter(Pipe.machine_id == machine_id)
    if mold_number:
        query = query.filter(Pipe.mold_number == mold_number)
    if no_code:
        query = query.filter(Pipe.no_code.ilike(f"%{no_code}%"))
    if customer or sales_order:
        query = query.join(
            ProductionOrder, Pipe.production_order_id == ProductionOrder.id
        )
        if customer:
            query = query.filter(ProductionOrder.customer_name.ilike(f"%{customer}%"))
        if sales_order:
            query = query.filter(ProductionOrder.sales_number.ilike(f"%{sales_order}%"))

    pipes = query.order_by(Pipe.production_date.desc(), Pipe.no_code.desc()).paginate(
        page=page, per_page=per_page
    )

    # Get filter options for dropdowns
    from app.models.production_order import ProductionOrder
    from app.models.chemical import Machine

    production_orders = (
        ProductionOrder.query.filter(
            ProductionOrder.status.in_(["pending", "in_progress", "completed"])
        )
        .order_by(ProductionOrder.order_number.desc())
        .limit(50)
        .all()
    )

    machines = Machine.query.filter_by(is_active=True).all()

    # Get unique diameters and classes
    diameters = (
        db.session.query(Pipe.diameter)
        .distinct()
        .filter(Pipe.diameter.isnot(None))
        .order_by(Pipe.diameter)
        .all()
    )
    diameters = [d[0] for d in diameters]

    classes = (
        db.session.query(Pipe.pipe_class)
        .distinct()
        .filter(Pipe.pipe_class.isnot(None))
        .order_by(Pipe.pipe_class)
        .all()
    )
    classes = [c[0] for c in classes]

    # Fetch chemical analysis decisions for ladles
    ladle_decisions = {}
    ladle_ids = [p.ladle_id for p in pipes.items if p.ladle_id]
    if ladle_ids:
        analyses = ChemicalAnalysis.query.filter(
            ChemicalAnalysis.ladle_id.in_(ladle_ids)
        ).all()
        ladle_decisions = {a.ladle_id: a.decision for a in analyses}

    return render_template(
        "stages/list.html",
        pipes=pipes,
        production_orders=production_orders,
        machines=machines,
        diameters=diameters,
        classes=classes,
        ladle_decisions=ladle_decisions,
    )


@stages_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    """Add new pipe"""
    if not current_user.can_edit:
        flash("You do not have permission to add records.", "error")
        return redirect(url_for("stages.list"))

    if request.method == "POST":
        try:
            pipe = Pipe()

            no_code = request.form["no_code"].strip()
            if not no_code:
                flash("No. Code is required.", "error")
                return redirect(url_for("stages.add"))

            # Check for duplicate pipe - STRICT: no_code must be globally unique
            existing = Pipe.query.filter_by(no_code=no_code).first()
            if existing:
                flash(
                    f"Pipe with No. Code '{no_code}' already exists (ID: {existing.id}, Ladle: {existing.ladle_id or 'N/A'}). Each pipe code must be unique across all production orders.",
                    "error",
                )
                return redirect(url_for("stages.add"))

            # Production info
            pipe.production_date = date.fromisoformat(request.form["production_date"])

            # Auto-determine shift based on time if not provided
            shift_value = request.form.get("shift")
            if shift_value:
                pipe.shift = int(shift_value)
            else:
                # Auto-calculate shift based on current hour
                from datetime import datetime

                hour = datetime.now().hour
                if 8 <= hour < 16:
                    pipe.shift = 1
                elif 16 <= hour < 24:
                    pipe.shift = 2
                else:
                    pipe.shift = 3

            pipe.shift_engineer = request.form.get("shift_engineer")
            pipe.manufacturing_order = request.form.get("manufacturing_order")

            # Link to production order
            production_order_id = request.form.get("production_order_id")
            if production_order_id:
                pipe.production_order_id = int(production_order_id)

            # Link to product
            product_id = request.form.get("product_id")
            if product_id:
                pipe.product_id = int(product_id)

            # Pipe identification
            pipe.pipe_code = request.form.get("pipe_code")
            pipe.diameter = int(request.form.get("diameter") or 0)
            pipe.pipe_class = request.form.get("pipe_class")
            pipe.machine_id = (
                int(request.form.get("machine_id"))
                if request.form.get("machine_id")
                else None
            )
            pipe.mold_number = request.form.get("mold_number")
            pipe.iso_weight = float(request.form.get("iso_weight") or 0)
            pipe.no_code = no_code
            pipe.arrange_pipe = int(request.form.get("arrange_pipe") or 1)

            # Link to chemical analysis
            pipe.ladle_id = request.form.get("ladle_id")

            # Measurements
            pipe.thickness = (
                float(request.form.get("thickness") or 0)
                if request.form.get("thickness")
                else None
            )
            pipe.thickness_socket_2 = (
                float(request.form.get("thickness_socket_2") or 0)
                if request.form.get("thickness_socket_2")
                else None
            )
            pipe.thickness_spigot = (
                float(request.form.get("thickness_spigot") or 0)
                if request.form.get("thickness_spigot")
                else None
            )
            pipe.thickness_spigot_2 = (
                float(request.form.get("thickness_spigot_2") or 0)
                if request.form.get("thickness_spigot_2")
                else None
            )
            pipe.actual_weight = (
                float(request.form.get("actual_weight") or 0)
                if request.form.get("actual_weight")
                else None
            )

            # Metadata
            pipe.created_by_id = current_user.id
            pipe.modified_by_id = current_user.id

            db.session.add(pipe)
            db.session.commit()

            # Process stage data from form
            stage_names = [
                "Melting Ladle",
                "CCM",
                "Annealing",
                "Lab",
                "Zinc",
                "Cutting",
                "Hydrotest",
                "Cement",
                "Coating",
                "Finish",
                "Delivery",
            ]
            for stage_name in stage_names:
                decision = request.form.get(f"stage_{stage_name}_decision")
                machine_id = request.form.get(f"stage_{stage_name}_machine_id")
                reason = request.form.get(f"stage_{stage_name}_reason")
                defect_type = request.form.get(f"stage_{stage_name}_defect_type")
                defect_reason = request.form.get(f"stage_{stage_name}_defect_reason")
                notes = request.form.get(f"stage_{stage_name}_notes")

                # Get Annealing-specific fields (date and time)
                stage_date_str = request.form.get(f"stage_{stage_name}_date")
                stage_time_str = request.form.get(f"stage_{stage_name}_time")

                # Get Finish-specific field (length)
                length_value = request.form.get(f"stage_{stage_name}_length")

                # Only create stage record if there's any data
                if (
                    decision
                    or machine_id
                    or reason
                    or defect_type
                    or defect_reason
                    or notes
                    or stage_date_str
                    or length_value
                ):
                    stage = PipeStage(
                        pipe_id=pipe.id,
                        stage_name=stage_name,
                        decision=decision,
                        machine_id=int(machine_id) if machine_id else None,
                        reason=reason,
                        defect_type=defect_type,
                        defect_reason=defect_reason,
                        notes=notes,
                        has_defect=bool(defect_type),
                        stage_date=date.fromisoformat(stage_date_str)
                        if stage_date_str
                        else date.today(),
                        updated_by_id=current_user.id,
                        approved_by_id=current_user.id if decision else None,
                    )

                    # Set time for Annealing
                    if stage_name == "Annealing" and stage_time_str:
                        from datetime import datetime as dt

                        stage.stage_time = dt.strptime(stage_time_str, "%H:%M").time()

                    # Set length for Finish
                    if stage_name == "Finish" and length_value:
                        stage.measurement_value = float(length_value)
                        stage.measurement_type = "Length"

                    db.session.add(stage)

            db.session.commit()

            # Auto-run decision engine if ladle has chemical decision
            if pipe.ladle_id:
                try:
                    from app.services.pipe_decision_service import (
                        assign_mechanical_roles,
                        update_lab_stage_decision,
                        update_final_decision,
                    )
                    from app.models.chemical import ChemicalAnalysis as CA

                    analysis = CA.query.filter_by(ladle_id=pipe.ladle_id).first()
                    if analysis and analysis.decision:
                        # Auto-create Melting Ladle stage with chemical decision
                        melting_stage = PipeStage.query.filter_by(
                            pipe_id=pipe.id, stage_name="Melting Ladle"
                        ).first()
                        if not melting_stage:
                            melting_stage = PipeStage(
                                pipe_id=pipe.id,
                                stage_name="Melting Ladle",
                                stage_date=analysis.test_date or date.today(),
                                decision=analysis.decision,
                                reason=analysis.reason,
                                updated_by_id=current_user.id,
                            )
                            db.session.add(melting_stage)
                            db.session.commit()

                        # Re-run role assignment for all pipes in this ladle
                        assign_mechanical_roles(pipe.ladle_id)

                        # Refresh pipe after role assignment
                        db.session.refresh(pipe)

                        # Auto-set Lab stage if decision is already determined
                        if pipe.lab_decision and pipe.lab_decision != "WAITING":
                            update_lab_stage_decision(pipe)

                        update_final_decision(pipe)
                except Exception:
                    pass
            else:
                try:
                    from app.services.pipe_decision_service import update_final_decision

                    update_final_decision(pipe)
                except Exception:
                    pass

            flash("Pipe added successfully!", "success")
            return redirect(url_for("stages.view", id=pipe.id))

        except IntegrityError as e:
            db.session.rollback()
            flash(
                "Database error: a duplicate value was detected. Please check your input.",
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding pipe: {str(e)}", "error")

    # Check if order_id is passed from production order page
    selected_order_id = request.args.get("order_id", type=int)

    return render_template(
        "stages/form.html",
        **_get_form_context(selected_order_id=selected_order_id),
    )


@stages_bp.route("/<int:id>")
@login_required
def view(id):
    """View pipe tracking through stages"""
    pipe = Pipe.query.get_or_404(id)
    stages_status = pipe.get_all_stages_status()
    defect_types = DefectType.query.filter_by(is_active=True).all()
    decision_types = DecisionType.query.all()

    # Get chemical analysis for this pipe
    chemical_analysis = (
        ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        if pipe.ladle_id
        else None
    )

    # Get stage-specific decisions and defects
    stage_decisions = get_stage_decisions_from_db()
    stage_defects = get_stage_defects_from_db()
    stage_machines = get_stage_machines_from_db()

    # Get attachments
    from app.models.attachment import Attachment

    attachments = Attachment.get_for_record("pipes", pipe.id)

    # Change history for this pipe
    from app.models.audit import AuditLog

    audit_entries = (
        AuditLog.query.filter_by(table_name="pipes", record_id=pipe.id)
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
        .all()
    )

    # Compute prev/next pipe for navigation
    prev_pipe = None
    next_pipe = None
    if pipe.production_order_id:
        siblings = (
            Pipe.query.filter_by(production_order_id=pipe.production_order_id)
            .order_by(Pipe.no_code)
            .all()
        )
        for i, p in enumerate(siblings):
            if p.id == pipe.id:
                if i > 0:
                    prev_pipe = siblings[i - 1]
                if i < len(siblings) - 1:
                    next_pipe = siblings[i + 1]
                break

    return render_template(
        "stages/detail.html",
        pipe=pipe,
        chemical_analysis=chemical_analysis,
        stages_status=stages_status,
        defect_types=defect_types,
        decision_types=decision_types,
        stage_decisions=stage_decisions,
        stage_defects=stage_defects,
        stage_machines=stage_machines,
        all_stages=Pipe.STAGES,
        prev_pipe=prev_pipe,
        next_pipe=next_pipe,
        attachments=attachments,
        audit_entries=audit_entries,
        table_name="pipes",
        record_id=pipe.id,
        redirect_url=url_for("stages.view", id=pipe.id),
    )


# Alias for backward compatibility
@stages_bp.route("/tracking/<int:id>")
@login_required
def tracking(id):
    """Alias for view - backward compatibility"""
    return redirect(url_for("stages.view", id=id))


@stages_bp.route("/<int:id>/stage/<stage_name>", methods=["POST"])
@login_required
def update_stage(id, stage_name):
    """Update a specific stage for a pipe"""
    if not current_user.can_edit:
        return jsonify({"success": False, "error": "No permission"}), 403

    if stage_name not in Pipe.STAGES:
        return jsonify({"success": False, "error": "Invalid stage"}), 400

    pipe = Pipe.query.get_or_404(id)

    # Block stage updates for REJECTED/BLOCKED pipes
    if pipe.final_decision_value == "REJECT":
        return jsonify(
            {"success": False, "error": "Cannot update stages on a REJECTED pipe"}
        ), 400

    if pipe.lab_decision == "BLOCKED":
        return jsonify(
            {
                "success": False,
                "error": "Cannot update stages - chemical analysis rejected (BLOCKED)",
            }
        ), 400

    # Post-Lab stages: block only on hard REJECT (lab failed or chemical rejected)
    # WAITING is allowed — physical production stages continue while lab results are pending
    post_lab_stages = [
        "Zinc",
        "Cutting",
        "Hydrotest",
        "Cement",
        "Coating",
        "Finish",
        "Delivery",
    ]
    if stage_name in post_lab_stages:
        if pipe.lab_decision == "REJECT":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot update post-lab stages — lab decision is REJECT",
                }
            ), 400

    # Delivery is the last stage: only allow it once everything passes
    if stage_name == "Delivery":
        if pipe.lab_decision == "HOLD":
            return jsonify(
                {"success": False, "error": "Cannot deliver — pipe is on HOLD"}
            ), 400
        if pipe.final_decision_value != "ACCEPT":
            return jsonify(
                {
                    "success": False,
                    "error": "Cannot deliver — pipe final decision must be ACCEPT (currently: "
                    + (pipe.final_decision_value or "pending")
                    + ")",
                }
            ), 400

    try:
        # Get or create stage
        stage = PipeStage.query.filter_by(
            pipe_id=pipe.id, stage_name=stage_name
        ).first()
        is_new = stage is None

        if not stage:
            stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
            db.session.add(stage)
            db.session.flush()  # Get ID for history
        else:
            # Decision immutability: once a stage decision is set and approved,
            # only supervisors/admins can change it.
            if (
                stage.decision
                and stage.approved_by_id
                and not current_user.is_supervisor
            ):
                return jsonify(
                    {
                        "success": False,
                        "error": "This stage decision is locked. Only supervisors can modify an approved decision.",
                    }
                ), 403

            # Save history before making changes (only for existing stages)
            history = PipeStageHistory.create_from_stage(
                stage, action="update", user_id=current_user.id
            )
            db.session.add(history)

        # Update stage data
        data = request.get_json() or request.form

        stage.stage_date = (
            date.fromisoformat(data.get("stage_date"))
            if data.get("stage_date")
            else date.today()
        )
        if data.get("stage_time"):
            stage.stage_time = datetime.strptime(data["stage_time"], "%H:%M").time()

        new_decision = data.get("decision")
        # Track who approved this decision the moment it changes
        if new_decision and new_decision != stage.decision:
            stage.approved_by_id = current_user.id
        stage.decision = new_decision
        stage.reason = data.get("reason")
        stage.has_defect = (
            data.get("has_defect") == "true" or data.get("has_defect") == True
        )
        stage.defect_type_id = (
            int(data.get("defect_type_id")) if data.get("defect_type_id") else None
        )
        stage.defect_type = data.get("defect_type")  # Stage-specific defect
        stage.defect_reason = data.get("defect_reason")
        stage.notes = data.get("notes")

        # Machine used for this stage
        stage.machine_id = (
            int(data.get("machine_id")) if data.get("machine_id") else None
        )

        # Stage-specific measurements
        if data.get("measurement_value"):
            stage.measurement_value = float(data["measurement_value"])
            stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(
                stage_name, stage_name
            )

        # Bundle # for Finish and Delivery stages
        if stage_name in ("Finish", "Delivery"):
            stage.bundle_number = data.get("bundle_number") or None

        # Delivery-specific fields
        if stage_name == "Delivery":
            stage.sales_order = data.get("sales_order")
            stage.delivery_customer = data.get("delivery_customer")
            stage.delivery_receipt = data.get("delivery_receipt")
            if data.get("delivery_date"):
                stage.delivery_date = date.fromisoformat(data["delivery_date"])

        stage.updated_by_id = current_user.id

        # If new stage, save history after creation with initial data
        if is_new:
            db.session.flush()
            history = PipeStageHistory.create_from_stage(
                stage, action="create", user_id=current_user.id
            )
            db.session.add(history)

        db.session.commit()

        # Recalculate final decision after stage update
        try:
            from app.services.pipe_decision_service import update_final_decision

            update_final_decision(pipe)
        except Exception:
            pass

        return jsonify({"success": True, "message": "Stage updated successfully"})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@stages_bp.route("/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_pipe(id):
    """Edit pipe"""
    if not current_user.can_edit:
        flash("ليس لديك صلاحية التعديل", "error")
        return redirect(url_for("stages.list"))

    pipe = Pipe.query.get_or_404(id)

    if request.method == "POST":
        try:
            no_code = request.form["no_code"].strip()
            if not no_code:
                flash("No. Code is required.", "error")
                return redirect(url_for("stages.edit_pipe", id=id))

            # Production info
            pipe.production_date = date.fromisoformat(request.form["production_date"])

            # Auto-determine shift based on time if not provided
            shift_value = request.form.get("shift")
            if shift_value:
                pipe.shift = int(shift_value)
            else:
                # Auto-calculate shift based on current hour
                from datetime import datetime

                hour = datetime.now().hour
                if 8 <= hour < 16:
                    pipe.shift = 1
                elif 16 <= hour < 24:
                    pipe.shift = 2
                else:
                    pipe.shift = 3

            pipe.shift_engineer = request.form.get("shift_engineer")
            pipe.manufacturing_order = request.form.get("manufacturing_order")

            # Link to production order
            production_order_id = request.form.get("production_order_id")
            if production_order_id:
                pipe.production_order_id = int(production_order_id)
            else:
                pipe.production_order_id = None

            # Link to product
            product_id = request.form.get("product_id")
            if product_id:
                pipe.product_id = int(product_id)
            else:
                pipe.product_id = None

            # Pipe identification
            pipe.pipe_code = request.form.get("pipe_code")
            pipe.diameter = int(request.form.get("diameter") or 0)
            pipe.pipe_class = request.form.get("pipe_class")
            pipe.machine_id = (
                int(request.form.get("machine_id"))
                if request.form.get("machine_id")
                else None
            )
            pipe.mold_number = request.form.get("mold_number")
            pipe.iso_weight = float(request.form.get("iso_weight") or 0)
            pipe.no_code = no_code
            pipe.arrange_pipe = int(request.form.get("arrange_pipe") or 1)

            # Link to chemical analysis
            pipe.ladle_id = request.form.get("ladle_id")

            # Measurements
            pipe.thickness = (
                float(request.form.get("thickness") or 0)
                if request.form.get("thickness")
                else None
            )
            pipe.thickness_socket_2 = (
                float(request.form.get("thickness_socket_2") or 0)
                if request.form.get("thickness_socket_2")
                else None
            )
            pipe.thickness_spigot = (
                float(request.form.get("thickness_spigot") or 0)
                if request.form.get("thickness_spigot")
                else None
            )
            pipe.thickness_spigot_2 = (
                float(request.form.get("thickness_spigot_2") or 0)
                if request.form.get("thickness_spigot_2")
                else None
            )
            pipe.actual_weight = (
                float(request.form.get("actual_weight") or 0)
                if request.form.get("actual_weight")
                else None
            )

            pipe.modified_by_id = current_user.id

            # Process stage data from form
            stage_names = [
                "Melting Ladle",
                "CCM",
                "Annealing",
                "Lab",
                "Zinc",
                "Cutting",
                "Hydrotest",
                "Cement",
                "Coating",
                "Finish",
                "Delivery",
            ]
            for stage_name in stage_names:
                decision = request.form.get(f"stage_{stage_name}_decision")
                machine_id = request.form.get(f"stage_{stage_name}_machine_id")
                reason = request.form.get(f"stage_{stage_name}_reason")
                defect_type = request.form.get(f"stage_{stage_name}_defect_type")
                defect_reason = request.form.get(f"stage_{stage_name}_defect_reason")
                notes = request.form.get(f"stage_{stage_name}_notes")

                # Get or create stage
                stage = PipeStage.query.filter_by(
                    pipe_id=pipe.id, stage_name=stage_name
                ).first()
                is_new = stage is None

                # Only create/update stage record if there's any data
                if (
                    decision
                    or machine_id
                    or reason
                    or defect_type
                    or defect_reason
                    or notes
                ):
                    if not stage:
                        stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
                        db.session.add(stage)
                        db.session.flush()
                    else:
                        # Save history before making changes
                        history = PipeStageHistory.create_from_stage(
                            stage, action="update", user_id=current_user.id
                        )
                        db.session.add(history)

                    # Record approver the moment the decision changes
                    if decision and decision != stage.decision:
                        stage.approved_by_id = current_user.id
                    stage.decision = decision
                    stage.machine_id = int(machine_id) if machine_id else None
                    stage.reason = reason
                    stage.defect_type = defect_type
                    stage.defect_reason = defect_reason
                    stage.notes = notes
                    stage.has_defect = bool(defect_type)
                    stage.stage_date = stage.stage_date or date.today()
                    stage.updated_by_id = current_user.id

                    # If new stage, save history after creation
                    if is_new:
                        db.session.flush()
                        history = PipeStageHistory.create_from_stage(
                            stage, action="create", user_id=current_user.id
                        )
                        db.session.add(history)

            db.session.commit()

            # Auto-run decision engine if ladle has chemical decision
            if pipe.ladle_id:
                try:
                    from app.services.pipe_decision_service import (
                        assign_mechanical_roles,
                        update_lab_stage_decision,
                        update_final_decision,
                    )
                    from app.models.chemical import ChemicalAnalysis as CA

                    analysis = CA.query.filter_by(ladle_id=pipe.ladle_id).first()
                    if analysis and analysis.decision:
                        # Ensure Melting Ladle stage exists with chemical decision
                        melting_stage = PipeStage.query.filter_by(
                            pipe_id=pipe.id, stage_name="Melting Ladle"
                        ).first()
                        if not melting_stage:
                            melting_stage = PipeStage(
                                pipe_id=pipe.id,
                                stage_name="Melting Ladle",
                                stage_date=analysis.test_date or date.today(),
                                decision=analysis.decision,
                                reason=analysis.reason,
                                updated_by_id=current_user.id,
                            )
                            db.session.add(melting_stage)
                            db.session.commit()

                        assign_mechanical_roles(pipe.ladle_id)
                        db.session.refresh(pipe)

                        if pipe.lab_decision and pipe.lab_decision != "WAITING":
                            update_lab_stage_decision(pipe)

                        update_final_decision(pipe)
                except Exception:
                    pass
            else:
                try:
                    from app.services.pipe_decision_service import update_final_decision

                    update_final_decision(pipe)
                except Exception:
                    pass

            flash("تم تحديث الأنبوب بنجاح", "success")
            return redirect(url_for("stages.view", id=pipe.id))

        except IntegrityError as e:
            db.session.rollback()
            flash(
                "Database error: a duplicate value was detected. Please check your input.",
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "error")

    return render_template(
        "stages/form.html",
        pipe=pipe,
        **_get_form_context(selected_order_id=pipe.production_order_id),
    )


@stages_bp.route("/api/ocr-extract", methods=["POST"])
@login_required
def api_ocr_extract():
    """Extract pipe data from a pipe label/marking image via Gemini Vision."""
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No image file"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400
    try:
        image_bytes = file.read()
        from app.services.pipe_ocr_service import extract_pipe_from_image

        values = extract_pipe_from_image(image_bytes, file.filename)
        extracted = sum(1 for v in values.values() if v is not None)
        return jsonify(
            {
                "success": True,
                "values": values,
                "extracted": extracted,
                "message": f"Extracted {extracted} fields. Please verify.",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@stages_bp.route("/shift-dashboard")
@login_required
def shift_dashboard():
    """Shift Engineer dashboard — overview of current shift pipes, pending decisions, defects."""
    from datetime import datetime as dt

    now = dt.now()
    hour = now.hour
    if 8 <= hour < 16:
        current_shift = 1
    elif 16 <= hour < 24:
        current_shift = 2
    else:
        current_shift = 3

    today = date.today()

    # Pipes produced today in this shift
    pipes = (
        Pipe.query.filter(
            Pipe.production_date == today,
            Pipe.shift == current_shift,
        )
        .order_by(Pipe.no_code)
        .all()
    )

    total = len(pipes)
    accepted = sum(1 for p in pipes if (p.final_decision_value or "") == "ACCEPT")
    rejected = sum(1 for p in pipes if (p.final_decision_value or "") == "REJECT")
    pending = total - accepted - rejected

    # Stages with defects today
    defect_stages = (
        db.session.query(PipeStage)
        .join(Pipe)
        .filter(
            Pipe.production_date == today,
            Pipe.shift == current_shift,
            PipeStage.has_defect.is_(True),
        )
        .all()
    )

    # Pipes waiting for decision
    waiting = [p for p in pipes if p.lab_decision == "WAITING"]

    return render_template(
        "stages/shift_dashboard.html",
        current_shift=current_shift,
        today=today,
        pipes=pipes,
        stats={
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "pending": pending,
        },
        defect_stages=defect_stages,
        waiting_pipes=waiting,
    )


@stages_bp.route("/deliveries")
@login_required
def delivery_list():
    """List of delivered pipes / delivery stage records, with filters."""
    sales_order = request.args.get("sales_order")
    customer = request.args.get("customer")
    bundle = request.args.get("bundle")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    query = PipeStage.query.filter_by(stage_name="Delivery")
    if sales_order:
        query = query.filter(PipeStage.sales_order == sales_order)
    if customer:
        query = query.filter(PipeStage.delivery_customer.ilike(f"%{customer}%"))
    if bundle:
        query = query.filter(PipeStage.bundle_number == bundle)
    if date_from:
        query = query.filter(PipeStage.delivery_date >= date_from)
    if date_to:
        query = query.filter(PipeStage.delivery_date <= date_to)

    deliveries = (
        query.order_by(PipeStage.delivery_date.desc().nullslast(), PipeStage.id.desc())
        .limit(500)
        .all()
    )
    return render_template(
        "stages/deliveries.html",
        deliveries=deliveries,
        filters={
            "sales_order": sales_order,
            "customer": customer,
            "bundle": bundle,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@stages_bp.route("/api/ladle/<ladle_id>")
@login_required
def api_get_ladle(ladle_id):
    """API to get ladle info for pipe creation"""
    analysis = ChemicalAnalysis.query.filter_by(ladle_id=ladle_id).first()
    if analysis:
        # Auto-calculate next arrange_pipe number
        max_arrange = (
            db.session.query(db.func.max(Pipe.arrange_pipe))
            .filter(Pipe.ladle_id == ladle_id)
            .scalar()
        )
        next_arrange = (max_arrange or 0) + 1

        return jsonify(
            {
                "found": True,
                "test_date": analysis.test_date.isoformat(),
                "furnace": analysis.furnace.furnace_code if analysis.furnace else None,
                "decision": analysis.decision,
                "next_arrange_pipe": next_arrange,
                "pipe_count": Pipe.query.filter_by(ladle_id=ladle_id).count(),
            }
        )
    return jsonify({"found": False})


@stages_bp.route("/<int:id>/history")
@login_required
def stage_history(id):
    """Get history for all stages of a pipe"""
    pipe = Pipe.query.get_or_404(id)

    history = (
        PipeStageHistory.query.filter_by(pipe_id=pipe.id)
        .order_by(PipeStageHistory.changed_at.desc())
        .all()
    )

    history_data = []
    for h in history:
        history_data.append(
            {
                "id": h.id,
                "stage_name": h.stage_name,
                "action": h.action,
                "decision": h.decision,
                "reason": h.reason,
                "machine_code": h.machine_code,
                "has_defect": h.has_defect,
                "defect_type": h.defect_type,
                "defect_reason": h.defect_reason,
                "notes": h.notes,
                "measurement_value": h.measurement_value,
                "stage_date": h.stage_date.isoformat() if h.stage_date else None,
                "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M:%S"),
                "changed_by": h.changed_by.full_name or h.changed_by.username
                if h.changed_by
                else "Unknown",
            }
        )

    return jsonify({"history": history_data})


@stages_bp.route("/<int:pipe_id>/stage/<stage_name>/history")
@login_required
def single_stage_history(pipe_id, stage_name):
    """Get history for a specific stage of a pipe"""
    pipe = Pipe.query.get_or_404(pipe_id)

    history = (
        PipeStageHistory.query.filter_by(pipe_id=pipe.id, stage_name=stage_name)
        .order_by(PipeStageHistory.changed_at.desc())
        .all()
    )

    history_data = []
    for h in history:
        history_data.append(
            {
                "id": h.id,
                "stage_name": h.stage_name,
                "action": h.action,
                "decision": h.decision,
                "reason": h.reason,
                "machine_code": h.machine_code,
                "has_defect": h.has_defect,
                "defect_type": h.defect_type,
                "defect_reason": h.defect_reason,
                "notes": h.notes,
                "measurement_value": h.measurement_value,
                "stage_date": h.stage_date.isoformat() if h.stage_date else None,
                "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M:%S"),
                "changed_by": h.changed_by.full_name or h.changed_by.username
                if h.changed_by
                else "Unknown",
            }
        )

    return jsonify({"history": history_data})
