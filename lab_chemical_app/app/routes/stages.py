"""
Production Stages Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import date, datetime
from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis, Machine, DefectType, DecisionType
from app.models.production_order import ProductionOrder
from app.models.stage_defect_type import StageDefectType
from app.models.stage_decision_type import StageDecisionType
from app.models.stage_history import PipeStageHistory

stages_bp = Blueprint('stages', __name__)


def get_stage_defects_from_db():
    """Get stage defects from database, grouped by stage"""
    defects = StageDefectType.query.filter_by(is_active=True).order_by(
        StageDefectType.stage_name,
        StageDefectType.sort_order
    ).all()

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
    decisions = StageDecisionType.query.filter_by(is_active=True).order_by(
        StageDecisionType.stage_name,
        StageDecisionType.sort_order
    ).all()

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
    machines = Machine.query.filter_by(is_active=True).order_by(
        Machine.stage,
        Machine.machine_code
    ).all()

    stage_machines = {}
    for machine in machines:
        if machine.stage and machine.stage not in stage_machines:
            stage_machines[machine.stage] = []
        if machine.stage:
            stage_machines[machine.stage].append({
                'id': machine.id,
                'code': machine.machine_code,
                'name': machine.machine_name or machine.machine_code
            })

    return stage_machines


@stages_bp.route('/')
@login_required
def list():
    """List all pipes with stage status"""
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # Filters
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    diameter = request.args.get('diameter', type=int)
    pipe_class = request.args.get('pipe_class')

    query = Pipe.query

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if diameter:
        query = query.filter(Pipe.diameter == diameter)
    if pipe_class:
        query = query.filter(Pipe.pipe_class == pipe_class)

    pipes = query.order_by(Pipe.production_date.desc(), Pipe.no_code.desc())\
        .paginate(page=page, per_page=per_page)

    return render_template('stages/list.html', pipes=pipes)


@stages_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    """Add new pipe"""
    if not current_user.can_edit:
        flash('You do not have permission to add records.', 'error')
        return redirect(url_for('stages.list'))

    if request.method == 'POST':
        try:
            pipe = Pipe()

            # Production info
            pipe.production_date = date.fromisoformat(request.form['production_date'])
            pipe.shift = int(request.form.get('shift') or 1)
            pipe.shift_engineer = request.form.get('shift_engineer')
            pipe.manufacturing_order = request.form.get('manufacturing_order')

            # Link to production order
            production_order_id = request.form.get('production_order_id')
            if production_order_id:
                pipe.production_order_id = int(production_order_id)

            # Pipe identification
            pipe.pipe_code = request.form.get('pipe_code')
            pipe.diameter = int(request.form.get('diameter') or 0)
            pipe.pipe_class = request.form.get('pipe_class')
            pipe.machine_id = int(request.form.get('machine_id')) if request.form.get('machine_id') else None
            pipe.mold_number = request.form.get('mold_number')
            pipe.iso_weight = float(request.form.get('iso_weight') or 0)
            pipe.no_code = request.form['no_code']
            pipe.arrange_pipe = int(request.form.get('arrange_pipe') or 1)

            # Link to chemical analysis
            pipe.ladle_id = request.form.get('ladle_id')

            # Measurements
            pipe.thickness = float(request.form.get('thickness') or 0) if request.form.get('thickness') else None
            pipe.actual_weight = float(request.form.get('actual_weight') or 0) if request.form.get('actual_weight') else None

            # Metadata
            pipe.created_by_id = current_user.id

            db.session.add(pipe)
            db.session.commit()

            # Process stage data from form
            stage_names = ['Melting Ladle', 'CCM', 'Annealing', 'Lab', 'Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating', 'Finish']
            for stage_name in stage_names:
                decision = request.form.get(f'stage_{stage_name}_decision')
                machine_id = request.form.get(f'stage_{stage_name}_machine_id')
                reason = request.form.get(f'stage_{stage_name}_reason')
                defect_type = request.form.get(f'stage_{stage_name}_defect_type')
                defect_reason = request.form.get(f'stage_{stage_name}_defect_reason')
                notes = request.form.get(f'stage_{stage_name}_notes')

                # Get Annealing-specific fields (date and time)
                stage_date_str = request.form.get(f'stage_{stage_name}_date')
                stage_time_str = request.form.get(f'stage_{stage_name}_time')

                # Get Finish-specific field (length)
                length_value = request.form.get(f'stage_{stage_name}_length')

                # Only create stage record if there's any data
                if decision or machine_id or reason or defect_type or defect_reason or notes or stage_date_str or length_value:
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
                        stage_date=date.fromisoformat(stage_date_str) if stage_date_str else date.today(),
                        updated_by_id=current_user.id
                    )

                    # Set time for Annealing
                    if stage_name == 'Annealing' and stage_time_str:
                        from datetime import datetime as dt
                        stage.stage_time = dt.strptime(stage_time_str, '%H:%M').time()

                    # Set length for Finish
                    if stage_name == 'Finish' and length_value:
                        stage.measurement_value = float(length_value)
                        stage.measurement_type = 'Length'

                    db.session.add(stage)

            db.session.commit()

            flash('Pipe added successfully!', 'success')
            return redirect(url_for('stages.view', id=pipe.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error adding pipe: {str(e)}', 'error')

    machines = Machine.query.filter_by(is_active=True).all()

    # Get the 4 latest ladles from chemical analysis
    latest_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.id.desc()
    ).limit(4).all()

    # Get production orders for dropdown
    production_orders = ProductionOrder.query.filter(
        ProductionOrder.status.in_(['pending', 'in_progress'])
    ).order_by(ProductionOrder.order_date.desc()).all()

    # Check if order_id is passed from production order page
    selected_order_id = request.args.get('order_id', type=int)

    return render_template('stages/form.html',
                          machines=machines,
                          latest_ladles=latest_ladles,
                          production_orders=production_orders,
                          selected_order_id=selected_order_id,
                          stage_decisions=get_stage_decisions_from_db(),
                          stage_defects=get_stage_defects_from_db(),
                          stage_machines=get_stage_machines_from_db(),
                          today=date.today())


@stages_bp.route('/<int:id>')
@login_required
def view(id):
    """View pipe tracking through stages"""
    pipe = Pipe.query.get_or_404(id)
    stages_status = pipe.get_all_stages_status()
    defect_types = DefectType.query.filter_by(is_active=True).all()
    decision_types = DecisionType.query.all()

    # Get chemical analysis for this pipe
    chemical_analysis = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first() if pipe.ladle_id else None

    # Get stage-specific decisions and defects
    stage_decisions = get_stage_decisions_from_db()
    stage_defects = get_stage_defects_from_db()
    stage_machines = get_stage_machines_from_db()

    return render_template('stages/detail.html',
                          pipe=pipe,
                          chemical_analysis=chemical_analysis,
                          stages_status=stages_status,
                          defect_types=defect_types,
                          decision_types=decision_types,
                          stage_decisions=stage_decisions,
                          stage_defects=stage_defects,
                          stage_machines=stage_machines,
                          all_stages=Pipe.STAGES)


# Alias for backward compatibility
@stages_bp.route('/tracking/<int:id>')
@login_required
def tracking(id):
    """Alias for view - backward compatibility"""
    return redirect(url_for('stages.view', id=id))


@stages_bp.route('/<int:id>/stage/<stage_name>', methods=['POST'])
@login_required
def update_stage(id, stage_name):
    """Update a specific stage for a pipe"""
    if not current_user.can_edit:
        return jsonify({'success': False, 'error': 'No permission'}), 403

    if stage_name not in Pipe.STAGES:
        return jsonify({'success': False, 'error': 'Invalid stage'}), 400

    pipe = Pipe.query.get_or_404(id)

    try:
        # Get or create stage
        stage = PipeStage.query.filter_by(pipe_id=pipe.id, stage_name=stage_name).first()
        is_new = stage is None

        if not stage:
            stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
            db.session.add(stage)
            db.session.flush()  # Get ID for history
        else:
            # Save history before making changes (only for existing stages)
            history = PipeStageHistory.create_from_stage(stage, action='update', user_id=current_user.id)
            db.session.add(history)

        # Update stage data
        data = request.get_json() or request.form

        stage.stage_date = date.fromisoformat(data.get('stage_date')) if data.get('stage_date') else date.today()
        if data.get('stage_time'):
            stage.stage_time = datetime.strptime(data['stage_time'], '%H:%M').time()

        stage.decision = data.get('decision')
        stage.reason = data.get('reason')
        stage.has_defect = data.get('has_defect') == 'true' or data.get('has_defect') == True
        stage.defect_type_id = int(data.get('defect_type_id')) if data.get('defect_type_id') else None
        stage.defect_type = data.get('defect_type')  # Stage-specific defect
        stage.defect_reason = data.get('defect_reason')
        stage.notes = data.get('notes')

        # Machine used for this stage
        stage.machine_id = int(data.get('machine_id')) if data.get('machine_id') else None

        # Stage-specific measurements
        if data.get('measurement_value'):
            stage.measurement_value = float(data['measurement_value'])
            stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(stage_name)

        stage.updated_by_id = current_user.id

        # If new stage, save history after creation with initial data
        if is_new:
            db.session.flush()
            history = PipeStageHistory.create_from_stage(stage, action='create', user_id=current_user.id)
            db.session.add(history)

        db.session.commit()

        return jsonify({'success': True, 'message': 'Stage updated successfully'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@stages_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_pipe(id):
    """Edit pipe"""
    if not current_user.can_edit:
        flash('ليس لديك صلاحية التعديل', 'error')
        return redirect(url_for('stages.list'))

    pipe = Pipe.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Production info
            pipe.production_date = date.fromisoformat(request.form['production_date'])
            pipe.shift = int(request.form.get('shift') or 1)
            pipe.shift_engineer = request.form.get('shift_engineer')
            pipe.manufacturing_order = request.form.get('manufacturing_order')

            # Link to production order
            production_order_id = request.form.get('production_order_id')
            if production_order_id:
                pipe.production_order_id = int(production_order_id)
            else:
                pipe.production_order_id = None

            # Pipe identification
            pipe.pipe_code = request.form.get('pipe_code')
            pipe.diameter = int(request.form.get('diameter') or 0)
            pipe.pipe_class = request.form.get('pipe_class')
            pipe.machine_id = int(request.form.get('machine_id')) if request.form.get('machine_id') else None
            pipe.mold_number = request.form.get('mold_number')
            pipe.iso_weight = float(request.form.get('iso_weight') or 0)
            pipe.no_code = request.form['no_code']
            pipe.arrange_pipe = int(request.form.get('arrange_pipe') or 1)

            # Link to chemical analysis
            pipe.ladle_id = request.form.get('ladle_id')

            # Measurements
            pipe.thickness = float(request.form.get('thickness') or 0) if request.form.get('thickness') else None
            pipe.actual_weight = float(request.form.get('actual_weight') or 0) if request.form.get('actual_weight') else None

            # Process stage data from form
            stage_names = ['Melting Ladle', 'CCM', 'Annealing', 'Lab', 'Zinc', 'Cutting', 'Hydrotest', 'Cement', 'Coating', 'Finish']
            for stage_name in stage_names:
                decision = request.form.get(f'stage_{stage_name}_decision')
                machine_id = request.form.get(f'stage_{stage_name}_machine_id')
                reason = request.form.get(f'stage_{stage_name}_reason')
                defect_type = request.form.get(f'stage_{stage_name}_defect_type')
                defect_reason = request.form.get(f'stage_{stage_name}_defect_reason')
                notes = request.form.get(f'stage_{stage_name}_notes')

                # Get or create stage
                stage = PipeStage.query.filter_by(pipe_id=pipe.id, stage_name=stage_name).first()
                is_new = stage is None

                # Only create/update stage record if there's any data
                if decision or machine_id or reason or defect_type or defect_reason or notes:
                    if not stage:
                        stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
                        db.session.add(stage)
                        db.session.flush()
                    else:
                        # Save history before making changes
                        history = PipeStageHistory.create_from_stage(stage, action='update', user_id=current_user.id)
                        db.session.add(history)

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
                        history = PipeStageHistory.create_from_stage(stage, action='create', user_id=current_user.id)
                        db.session.add(history)

            db.session.commit()

            flash('تم تحديث الأنبوب بنجاح', 'success')
            return redirect(url_for('stages.view', id=pipe.id))

        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'error')

    machines = Machine.query.filter_by(is_active=True).all()

    # Get the 4 latest ladles from chemical analysis
    latest_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.id.desc()
    ).limit(4).all()

    # Get production orders for dropdown
    production_orders = ProductionOrder.query.filter(
        ProductionOrder.status.in_(['pending', 'in_progress'])
    ).order_by(ProductionOrder.order_date.desc()).all()

    return render_template('stages/form.html',
                          pipe=pipe,
                          machines=machines,
                          latest_ladles=latest_ladles,
                          production_orders=production_orders,
                          selected_order_id=pipe.production_order_id,
                          stage_decisions=get_stage_decisions_from_db(),
                          stage_defects=get_stage_defects_from_db(),
                          stage_machines=get_stage_machines_from_db(),
                          today=date.today())


@stages_bp.route('/api/ladle/<ladle_id>')
@login_required
def api_get_ladle(ladle_id):
    """API to get ladle info for pipe creation"""
    analysis = ChemicalAnalysis.query.filter_by(ladle_id=ladle_id).first()
    if analysis:
        return jsonify({
            'found': True,
            'test_date': analysis.test_date.isoformat(),
            'furnace': analysis.furnace.furnace_code if analysis.furnace else None,
            'decision': analysis.decision
        })
    return jsonify({'found': False})


@stages_bp.route('/<int:id>/history')
@login_required
def stage_history(id):
    """Get history for all stages of a pipe"""
    pipe = Pipe.query.get_or_404(id)

    history = PipeStageHistory.query.filter_by(pipe_id=pipe.id)\
        .order_by(PipeStageHistory.changed_at.desc()).all()

    history_data = []
    for h in history:
        history_data.append({
            'id': h.id,
            'stage_name': h.stage_name,
            'action': h.action,
            'decision': h.decision,
            'reason': h.reason,
            'machine_code': h.machine_code,
            'has_defect': h.has_defect,
            'defect_type': h.defect_type,
            'defect_reason': h.defect_reason,
            'notes': h.notes,
            'measurement_value': h.measurement_value,
            'stage_date': h.stage_date.isoformat() if h.stage_date else None,
            'changed_at': h.changed_at.strftime('%Y-%m-%d %H:%M:%S'),
            'changed_by': h.changed_by.full_name or h.changed_by.username if h.changed_by else 'Unknown'
        })

    return jsonify({'history': history_data})


@stages_bp.route('/<int:pipe_id>/stage/<stage_name>/history')
@login_required
def single_stage_history(pipe_id, stage_name):
    """Get history for a specific stage of a pipe"""
    pipe = Pipe.query.get_or_404(pipe_id)

    history = PipeStageHistory.query.filter_by(pipe_id=pipe.id, stage_name=stage_name)\
        .order_by(PipeStageHistory.changed_at.desc()).all()

    history_data = []
    for h in history:
        history_data.append({
            'id': h.id,
            'stage_name': h.stage_name,
            'action': h.action,
            'decision': h.decision,
            'reason': h.reason,
            'machine_code': h.machine_code,
            'has_defect': h.has_defect,
            'defect_type': h.defect_type,
            'defect_reason': h.defect_reason,
            'notes': h.notes,
            'measurement_value': h.measurement_value,
            'stage_date': h.stage_date.isoformat() if h.stage_date else None,
            'changed_at': h.changed_at.strftime('%Y-%m-%d %H:%M:%S'),
            'changed_by': h.changed_by.full_name or h.changed_by.username if h.changed_by else 'Unknown'
        })

    return jsonify({'history': history_data})
