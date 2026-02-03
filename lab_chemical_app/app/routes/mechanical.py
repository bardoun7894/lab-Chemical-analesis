"""
Mechanical Tests Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import date
from app import db
from app.models.mechanical import MechanicalTest
from app.models.chemical import ChemicalAnalysis

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

    return render_template('mechanical/form.html',
                          recent_ladles=recent_ladles,
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

            db.session.commit()
            flash('Mechanical test updated successfully!', 'success')
            return redirect(url_for('mechanical.detail', id=test.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating test: {str(e)}', 'error')

    recent_ladles = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.test_date.desc()
    ).limit(20).all()

    return render_template('mechanical/form.html',
                          test=test,
                          recent_ladles=recent_ladles,
                          edit_mode=True)
