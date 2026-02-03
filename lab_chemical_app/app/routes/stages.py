"""
Production Stages Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import date, datetime
from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis, Machine, DefectType, DecisionType

stages_bp = Blueprint('stages', __name__)


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
    pipe_type = request.args.get('pipe_type')

    query = Pipe.query

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if diameter:
        query = query.filter(Pipe.diameter == diameter)
    if pipe_type:
        query = query.filter(Pipe.pipe_type == pipe_type)

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

            # Pipe identification
            pipe.pipe_code = request.form.get('pipe_code')
            pipe.diameter = int(request.form.get('diameter') or 0)
            pipe.pipe_type = request.form.get('pipe_type')
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

            flash('Pipe added successfully!', 'success')
            return redirect(url_for('stages.tracking', id=pipe.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error adding pipe: {str(e)}', 'error')

    machines = Machine.query.filter_by(is_active=True).all()
    recent_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.test_date.desc()
    ).limit(20).all()

    return render_template('stages/form.html',
                          machines=machines,
                          recent_ladles=recent_ladles,
                          today=date.today())


@stages_bp.route('/<int:id>')
@login_required
def tracking(id):
    """View pipe tracking through stages"""
    pipe = Pipe.query.get_or_404(id)
    stages_status = pipe.get_all_stages_status()
    defect_types = DefectType.query.filter_by(is_active=True).all()
    decision_types = DecisionType.query.all()

    return render_template('stages/tracking.html',
                          pipe=pipe,
                          stages_status=stages_status,
                          defect_types=defect_types,
                          decision_types=decision_types,
                          all_stages=Pipe.STAGES)


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
        if not stage:
            stage = PipeStage(pipe_id=pipe.id, stage_name=stage_name)
            db.session.add(stage)

        # Update stage data
        data = request.get_json() or request.form

        stage.stage_date = date.fromisoformat(data.get('stage_date')) if data.get('stage_date') else date.today()
        if data.get('stage_time'):
            stage.stage_time = datetime.strptime(data['stage_time'], '%H:%M').time()

        stage.decision = data.get('decision')
        stage.reason = data.get('reason')
        stage.has_defect = data.get('has_defect') == 'true' or data.get('has_defect') == True
        stage.defect_type_id = int(data.get('defect_type_id')) if data.get('defect_type_id') else None
        stage.defect_reason = data.get('defect_reason')
        stage.notes = data.get('notes')

        # Stage-specific measurements
        if data.get('measurement_value'):
            stage.measurement_value = float(data['measurement_value'])
            stage.measurement_type = PipeStage.MEASUREMENT_TYPES.get(stage_name)

        stage.updated_by_id = current_user.id

        db.session.commit()

        return jsonify({'success': True, 'message': 'Stage updated successfully'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


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
