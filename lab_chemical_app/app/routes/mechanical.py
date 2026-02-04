"""
Mechanical Tests Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import login_required, current_user
from datetime import date
from app import db
from app.models.mechanical import MechanicalTest
from app.models.chemical import ChemicalAnalysis
from app.models.pipe import Pipe
from app.services.ai_service import generate_mechanical_analysis, generate_mechanical_stream
from app.services import mechanical_decision_service

mechanical_bp = Blueprint('mechanical', __name__)


@mechanical_bp.route('/')
@login_required
def list():
    """List all mechanical tests"""
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # Filters
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    diameter = request.args.get('diameter', type=int)

    query = MechanicalTest.query

    if date_from:
        query = query.filter(MechanicalTest.test_date >= date_from)
    if date_to:
        query = query.filter(MechanicalTest.test_date <= date_to)
    if diameter:
        query = query.filter(MechanicalTest.diameter == diameter)

    tests = query.order_by(MechanicalTest.test_date.desc())\
        .paginate(page=page, per_page=per_page)

    return render_template('mechanical/list.html', tests=tests)


@mechanical_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    """Add new mechanical test"""
    if not current_user.can_edit:
        flash('You do not have permission to add records.', 'error')
        return redirect(url_for('mechanical.list'))

    if request.method == 'POST':
        try:
            test = MechanicalTest()

            # Identification
            test.test_date = date.fromisoformat(request.form['test_date'])
            test.test_number = int(request.form.get('test_number') or 0)
            test.diameter = int(request.form.get('diameter') or 0)
            test.code = request.form.get('code')
            test.pipe_no = int(request.form.get('pipe_no') or 0) if request.form.get('pipe_no') else None
            test.ladle_id = request.form.get('ladle_id')

            test.day = test.test_date.day
            test.month = test.test_date.month
            test.year = test.test_date.year

            # Sample measurements
            for field in ['sample_thickness', 'd1', 'd2', 'd3', 'original_length', 'final_length', 'area_d_squared']:
                value = request.form.get(field)
                if value:
                    setattr(test, field, float(value))

            # Test results
            for field in ['force_kgf']:
                value = request.form.get(field)
                if value:
                    setattr(test, field, float(value))

            # Calculate derived values
            test.calculate_derived_values()

            # Microstructure
            test.microstructure = request.form.get('microstructure')
            for field in ['percent_85', 'percent_70', 'percent_40', 'percent_1',
                         'nodularity_percent', 'hardness', 'carbides']:
                value = request.form.get(field)
                if value:
                    setattr(test, field, float(value))

            if request.form.get('nodule_count'):
                test.nodule_count = int(request.form['nodule_count'])

            # Quality control
            test.shift = int(request.form.get('shift') or 1)
            test.tester_name = request.form.get('tester_name')
            test.decision = request.form.get('decision')
            test.reason = request.form.get('reason')
            test.comments = request.form.get('comments')

            # Link to pipe (required now with pipe_code)
            pipe_id = request.form.get('pipe_id')
            if pipe_id:
                test.pipe_id = int(pipe_id)
                # Get pipe and set pipe_code
                pipe = Pipe.query.get(int(pipe_id))
                if pipe:
                    test.pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.arrange_pipe or 1}-{pipe.ladle_id or ''}"

            # Metadata
            test.created_by_id = current_user.id

            db.session.add(test)
            db.session.commit()

            flash('Mechanical test added successfully!', 'success')
            return redirect(url_for('mechanical.detail', id=test.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error adding test: {str(e)}', 'error')

    recent_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.test_date.desc()
    ).limit(20).all()

    pipes = Pipe.query.order_by(Pipe.production_date.desc()).limit(50).all()

    return render_template('mechanical/form.html',
                          recent_ladles=recent_ladles,
                          pipes=pipes,
                          today=date.today())


@mechanical_bp.route('/<int:id>')
@login_required
def detail(id):
    """View mechanical test details"""
    test = MechanicalTest.query.get_or_404(id)
    return render_template('mechanical/detail.html', test=test)


@mechanical_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    """Edit mechanical test"""
    if not current_user.can_edit:
        flash('You do not have permission to edit records.', 'error')
        return redirect(url_for('mechanical.detail', id=id))

    test = MechanicalTest.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Update all fields similar to add
            test.diameter = int(request.form.get('diameter') or 0)
            test.code = request.form.get('code')
            test.pipe_no = int(request.form.get('pipe_no') or 0) if request.form.get('pipe_no') else None
            test.ladle_id = request.form.get('ladle_id')

            # Sample measurements
            for field in ['sample_thickness', 'd1', 'd2', 'd3', 'original_length', 'final_length', 'area_d_squared']:
                value = request.form.get(field)
                setattr(test, field, float(value) if value else None)

            # Test results
            for field in ['force_kgf']:
                value = request.form.get(field)
                setattr(test, field, float(value) if value else None)

            # Calculate derived values
            test.calculate_derived_values()

            # Microstructure
            test.microstructure = request.form.get('microstructure')
            for field in ['percent_85', 'percent_70', 'percent_40', 'percent_1',
                         'nodularity_percent', 'hardness', 'carbides']:
                value = request.form.get(field)
                setattr(test, field, float(value) if value else None)

            test.nodule_count = int(request.form['nodule_count']) if request.form.get('nodule_count') else None

            # Quality control
            test.shift = int(request.form.get('shift') or 1)
            test.tester_name = request.form.get('tester_name')
            test.decision = request.form.get('decision')
            test.reason = request.form.get('reason')
            test.comments = request.form.get('comments')

            # Update pipe link with pipe_code
            pipe_id = request.form.get('pipe_id')
            if pipe_id:
                test.pipe_id = int(pipe_id)
                # Get pipe and set pipe_code
                pipe = Pipe.query.get(int(pipe_id))
                if pipe:
                    test.pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.arrange_pipe or 1}-{pipe.ladle_id or ''}"
            else:
                test.pipe_id = None
                test.pipe_code = None

            db.session.commit()
            flash('Mechanical test updated successfully!', 'success')
            return redirect(url_for('mechanical.detail', id=test.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating test: {str(e)}', 'error')

    recent_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.test_date.desc()
    ).limit(20).all()

    pipes = Pipe.query.order_by(Pipe.production_date.desc()).limit(50).all()

    return render_template('mechanical/form.html',
                          test=test,
                          recent_ladles=recent_ladles,
                          pipes=pipes,
                          edit_mode=True)


@mechanical_bp.route('/api/ai-analysis', methods=['POST'])
@login_required
def api_ai_analysis():
    """API endpoint to generate AI analysis for mechanical test"""
    data = request.get_json()

    # Extract test values
    test_values = {}
    fields = ['tensile_strength', 'elongation', 'hardness', 'nodularity_percent',
              'carbides', 'nodule_count', 'd1', 'd2', 'd3', 'force_kgf']

    for field in fields:
        if field in data and data[field] is not None and data[field] != '':
            try:
                test_values[field] = float(data[field])
            except (TypeError, ValueError):
                pass

    result = generate_mechanical_analysis(test_values)
    return jsonify(result)


@mechanical_bp.route('/api/ai-analysis-stream', methods=['POST'])
@login_required
def api_ai_analysis_stream():
    """Streaming API endpoint for mechanical test AI analysis"""
    data = request.get_json()

    # Extract test values
    test_values = {}
    fields = ['tensile_strength', 'elongation', 'hardness', 'nodularity_percent',
              'carbides', 'nodule_count', 'd1', 'd2', 'd3', 'force_kgf']

    for field in fields:
        if field in data and data[field] is not None and data[field] != '':
            try:
                test_values[field] = float(data[field])
            except (TypeError, ValueError):
                pass

    def generate():
        for chunk in generate_mechanical_stream(test_values):
            yield chunk

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@mechanical_bp.route('/api/validate', methods=['POST'])
@login_required
def api_validate():
    """API endpoint to validate mechanical property values"""
    data = request.get_json()

    results = {}
    for property_code, value in data.items():
        if value is not None and value != '':
            validation = mechanical_decision_service.validate_property(property_code, value)
            results[property_code] = validation

    return jsonify(results)


@mechanical_bp.route('/api/auto-decision', methods=['POST'])
@login_required
def api_auto_decision():
    """API endpoint to calculate auto-decision based on mechanical properties"""
    data = request.get_json()

    # Extract property values
    property_values = {}
    for field_name, value in data.items():
        if value is not None and value != '':
            try:
                property_values[field_name] = float(value)
            except (TypeError, ValueError):
                pass

    # Calculate decision
    result = mechanical_decision_service.calculate_auto_decision(property_values)

    return jsonify(result)
