"""
Chemical Analysis Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import date
from app import db
from app.models.chemical import ChemicalAnalysis, Furnace, ElementSpecification
from app.services.decision_service import (
    calculate_auto_decision,
    get_all_decisions,
    ELEMENT_MAP
)
from flask import Response
from app.services.ai_service import generate_analysis_notes, generate_analysis_stream
from app.services.decision_service import calculate_auto_decision as calc_decision

chemical_bp = Blueprint('chemical', __name__)


@chemical_bp.route('/')
@login_required
def list():
    """List all chemical analyses"""
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # Filters
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    furnace_id = request.args.get('furnace_id', type=int)
    decision = request.args.get('decision')

    query = ChemicalAnalysis.query

    if date_from:
        query = query.filter(ChemicalAnalysis.test_date >= date_from)
    if date_to:
        query = query.filter(ChemicalAnalysis.test_date <= date_to)
    if furnace_id:
        query = query.filter(ChemicalAnalysis.furnace_id == furnace_id)
    if decision:
        query = query.filter(ChemicalAnalysis.decision == decision)

    analyses = query.order_by(ChemicalAnalysis.test_date.desc(), ChemicalAnalysis.ladle_no.desc())\
        .paginate(page=page, per_page=per_page)

    furnaces = Furnace.query.filter_by(is_active=True).all()
    specs = {s.element_code: s for s in ElementSpecification.query.all()}

    return render_template('chemical/list.html',
                          analyses=analyses,
                          furnaces=furnaces,
                          specs=specs)


@chemical_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    """Add new chemical analysis"""
    if not current_user.can_edit:
        flash('You do not have permission to add records.', 'error')
        return redirect(url_for('chemical.list'))

    if request.method == 'POST':
        try:
            analysis = ChemicalAnalysis()

            # Basic info
            analysis.test_date = date.fromisoformat(request.form['test_date'])
            analysis.furnace_id = int(request.form['furnace_id'])
            analysis.ladle_no = int(request.form['ladle_no'])

            # Generate ladle_id
            analysis.day = analysis.test_date.day
            analysis.month = analysis.test_date.month
            analysis.year = analysis.test_date.year
            analysis.ladle_id = f"{analysis.ladle_no}{analysis.day:02d}{analysis.month:02d}{analysis.year}"

            # Chemical elements
            for element in ['carbon', 'silicon', 'magnesium', 'copper', 'chromium',
                           'sulfur', 'manganese', 'phosphorus', 'lead', 'aluminum']:
                value = request.form.get(element)
                if value:
                    setattr(analysis, element, float(value))

            # Calculate equivalents
            analysis.calculate_equivalents()

            # Quality control
            analysis.engineer_notes = request.form.get('engineer_notes')
            analysis.decision = request.form.get('decision')
            analysis.reason = request.form.get('reason')
            analysis.notes = request.form.get('notes')

            # Check for defects based on specs
            analysis.has_defect = validate_against_specs(analysis)

            # Metadata
            analysis.created_by_id = current_user.id

            db.session.add(analysis)
            db.session.commit()

            flash('Chemical analysis added successfully!', 'success')
            return redirect(url_for('chemical.detail', id=analysis.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error adding analysis: {str(e)}', 'error')

    furnaces = Furnace.query.filter_by(is_active=True).all()
    specs = ElementSpecification.query.all()

    # Get next ladle number for today
    today = date.today()
    max_ladle = db.session.query(db.func.max(ChemicalAnalysis.ladle_no))\
        .filter(ChemicalAnalysis.test_date == today).scalar()
    next_ladle_no = (max_ladle or 0) + 1

    return render_template('chemical/form.html',
                          furnaces=furnaces,
                          specs=specs,
                          next_ladle_no=next_ladle_no,
                          today=today)


@chemical_bp.route('/<int:id>')
@login_required
def detail(id):
    """View chemical analysis details"""
    analysis = ChemicalAnalysis.query.get_or_404(id)
    specs = {s.element_code: s for s in ElementSpecification.query.all()}

    # Get related pipes
    pipes = analysis.pipes.all()

    # Get related mechanical tests
    mech_tests = analysis.mechanical_tests.all()

    return render_template('chemical/detail.html',
                          analysis=analysis,
                          specs=specs,
                          pipes=pipes,
                          mech_tests=mech_tests)


@chemical_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    """Edit chemical analysis"""
    if not current_user.can_edit:
        flash('You do not have permission to edit records.', 'error')
        return redirect(url_for('chemical.detail', id=id))

    analysis = ChemicalAnalysis.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Update fields
            analysis.furnace_id = int(request.form['furnace_id'])

            # Chemical elements
            for element in ['carbon', 'silicon', 'magnesium', 'copper', 'chromium',
                           'sulfur', 'manganese', 'phosphorus', 'lead', 'aluminum']:
                value = request.form.get(element)
                if value:
                    setattr(analysis, element, float(value))
                else:
                    setattr(analysis, element, None)

            # Recalculate equivalents
            analysis.calculate_equivalents()

            # Quality control
            analysis.engineer_notes = request.form.get('engineer_notes')
            analysis.decision = request.form.get('decision')
            analysis.reason = request.form.get('reason')
            analysis.notes = request.form.get('notes')

            # Check for defects
            analysis.has_defect = validate_against_specs(analysis)

            db.session.commit()
            flash('Chemical analysis updated successfully!', 'success')
            return redirect(url_for('chemical.detail', id=analysis.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating analysis: {str(e)}', 'error')

    furnaces = Furnace.query.filter_by(is_active=True).all()
    specs = ElementSpecification.query.all()

    return render_template('chemical/form.html',
                          analysis=analysis,
                          furnaces=furnaces,
                          specs=specs,
                          edit_mode=True)


@chemical_bp.route('/api/validate', methods=['POST'])
@login_required
def api_validate():
    """API endpoint to validate element values"""
    data = request.get_json()
    specs = {s.element_code: s for s in ElementSpecification.query.all()}

    results = {}
    for code, value in data.items():
        if code in specs and value is not None:
            spec = specs[code]
            is_valid, message = spec.check_value(float(value))
            results[code] = {
                'valid': is_valid,
                'message': message,
                'min': spec.min_value,
                'max': spec.max_value
            }

    return jsonify(results)


@chemical_bp.route('/api/auto-decision', methods=['POST'])
@login_required
def api_auto_decision():
    """
    API endpoint to calculate automatic decision based on element values.
    Returns the worst-case decision across all provided elements.
    """
    data = request.get_json()

    # Extract element values from the request
    element_values = {}
    for field_name in ELEMENT_MAP.keys():
        if field_name in data and data[field_name] is not None and data[field_name] != '':
            try:
                element_values[field_name] = float(data[field_name])
            except (TypeError, ValueError):
                pass

    # Calculate auto decision
    result = calculate_auto_decision(element_values)

    return jsonify(result)


@chemical_bp.route('/api/ai-analysis', methods=['POST'])
@login_required
def api_ai_analysis():
    """
    API endpoint to generate AI-powered analysis notes using GLM.
    Returns reason, has_defect, and notes based on element values.
    """
    data = request.get_json()

    # Extract element values from the request
    element_values = {}
    for field_name in ELEMENT_MAP.keys():
        if field_name in data and data[field_name] is not None and data[field_name] != '':
            try:
                element_values[field_name] = float(data[field_name])
            except (TypeError, ValueError):
                pass

    # Also include equivalents if provided
    for equiv in ['carbon_equivalent', 'manganese_equivalent', 'magnesium_equivalent']:
        if equiv in data and data[equiv]:
            try:
                element_values[equiv] = float(data[equiv])
            except (TypeError, ValueError):
                pass

    # First calculate auto decision
    auto_decision = calculate_auto_decision(element_values)

    # Then generate AI analysis
    ai_result = generate_analysis_notes(element_values, auto_decision)

    return jsonify(ai_result)


@chemical_bp.route('/api/ai-analysis-stream', methods=['POST'])
@login_required
def api_ai_analysis_stream():
    """
    Streaming API endpoint for AI analysis - returns Server-Sent Events.
    Text appears progressively like ChatGPT.
    """
    data = request.get_json()

    # Extract element values from the request
    element_values = {}
    for field_name in ELEMENT_MAP.keys():
        if field_name in data and data[field_name] is not None and data[field_name] != '':
            try:
                element_values[field_name] = float(data[field_name])
            except (TypeError, ValueError):
                pass

    # Also include equivalents if provided
    for equiv in ['carbon_equivalent', 'manganese_equivalent', 'magnesium_equivalent']:
        if equiv in data and data[equiv]:
            try:
                element_values[equiv] = float(data[equiv])
            except (TypeError, ValueError):
                pass

    # Calculate auto decision
    auto_decision = calc_decision(element_values)

    def generate():
        for chunk in generate_analysis_stream(element_values, auto_decision):
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


def validate_against_specs(analysis):
    """Check if analysis has any out-of-spec values"""
    specs = {s.element_code: s for s in ElementSpecification.query.all()}
    values = analysis.get_element_values()

    for code, value in values.items():
        if code in specs and value is not None:
            is_valid, _ = specs[code].check_value(value)
            if not is_valid:
                return True  # Has defect

    return False
