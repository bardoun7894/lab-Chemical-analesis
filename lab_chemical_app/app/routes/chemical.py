"""
Chemical Analysis Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, current_app
from flask_login import login_required, current_user
from datetime import date
from io import BytesIO
from app import db
from app.models.chemical import ChemicalAnalysis, Furnace, ElementSpecification
from app.models.production_order import ProductionOrder
from app.models.product import Product
from app.services.decision_service import (
    calculate_auto_decision,
    get_all_decisions,
    get_decision_color,
    ELEMENT_MAP
)
from flask import Response
from app.services.ai_service import generate_analysis_notes, generate_analysis_stream
from app.services.decision_service import calculate_auto_decision as calc_decision
from app.services.permission_service import requires_permission

chemical_bp = Blueprint('chemical', __name__)


# Map of advanced range-filter arg names -> (model column, operator)
_RANGE_FILTERS = [
    ('c_min', ChemicalAnalysis.carbon, 'ge'),
    ('c_max', ChemicalAnalysis.carbon, 'le'),
    ('si_min', ChemicalAnalysis.silicon, 'ge'),
    ('si_max', ChemicalAnalysis.silicon, 'le'),
    ('mg_min', ChemicalAnalysis.magnesium, 'ge'),
    ('mg_max', ChemicalAnalysis.magnesium, 'le'),
    ('s_min', ChemicalAnalysis.sulfur, 'ge'),
    ('s_max', ChemicalAnalysis.sulfur, 'le'),
    ('ce_min', ChemicalAnalysis.carbon_equivalent, 'ge'),
    ('ce_max', ChemicalAnalysis.carbon_equivalent, 'le'),
]


def _filtered_query(args):
    """Build the ChemicalAnalysis query from request args.

    Shared by list() and export_excel() so the two never drift.
    Returns (query, filters_dict) where filters_dict holds the parsed values
    for round-tripping into the template.
    """
    date_from = args.get('date_from')
    date_to = args.get('date_to')
    furnace_id = args.get('furnace_id', type=int)
    decision = args.get('decision')
    ladle_id = args.get('ladle_id')

    # Production-order-derived filters
    production_order_id = args.get('production_order_id', type=int)
    po_customer = args.get('customer')
    po_sales = args.get('sales_number')
    po_dn = args.get('dn', type=int)
    po_class = args.get('pipe_class')
    po_product_id = args.get('product_id', type=int)

    query = ChemicalAnalysis.query

    if date_from:
        query = query.filter(ChemicalAnalysis.test_date >= date_from)
    if date_to:
        query = query.filter(ChemicalAnalysis.test_date <= date_to)
    if furnace_id:
        query = query.filter(ChemicalAnalysis.furnace_id == furnace_id)
    if decision:
        query = query.filter(ChemicalAnalysis.decision == decision)
    if ladle_id:
        query = query.filter(ChemicalAnalysis.ladle_id.ilike(f"%{ladle_id}%"))

    # Direct FK filter — no join needed.
    if production_order_id:
        query = query.filter(ChemicalAnalysis.production_order_id == production_order_id)

    # ProductionOrder-derived filters — add the join at most once, only when
    # any of these are set, to avoid duplicate-join errors.
    if any(v not in (None, '') for v in (po_customer, po_sales, po_dn, po_class, po_product_id)):
        query = query.join(
            ProductionOrder,
            ChemicalAnalysis.production_order_id == ProductionOrder.id,
        )
        if po_customer:
            query = query.filter(ProductionOrder.customer_name.ilike(f"%{po_customer}%"))
        if po_sales:
            query = query.filter(ProductionOrder.sales_number.ilike(f"%{po_sales}%"))
        if po_dn:
            query = query.filter(ProductionOrder.diameter == po_dn)
        if po_class:
            query = query.filter(ProductionOrder.pipe_class == po_class)
        if po_product_id:
            query = query.filter(ProductionOrder.product_id == po_product_id)

    # Advanced element-range filters
    for arg_name, column, op in _RANGE_FILTERS:
        raw = args.get(arg_name)
        if raw is None or raw == '':
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        query = query.filter(column >= val if op == 'ge' else column <= val)

    filters = {
        'date_from': date_from,
        'date_to': date_to,
        'furnace_id': furnace_id,
        'decision': decision,
        'ladle_id': ladle_id,
        'production_order_id': production_order_id,
        'customer': po_customer,
        'sales_number': po_sales,
        'dn': po_dn,
        'pipe_class': po_class,
        'product_id': po_product_id,
    }
    for arg_name, _, _ in _RANGE_FILTERS:
        filters[arg_name] = args.get(arg_name)

    return query, filters


@chemical_bp.route('/')
@login_required
@requires_permission('chemical', 'list')
def list():
    """List all chemical analyses"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    group_by = request.args.get('group_by')

    query, filters = _filtered_query(request.args)

    furnaces = Furnace.query.filter_by(is_active=True).all()
    specs = {s.element_code: s for s in ElementSpecification.query.all()}
    production_orders = ProductionOrder.query.order_by(ProductionOrder.id.desc()).limit(200).all()
    products = Product.query.order_by(Product.product_code).all()

    base_order = (ChemicalAnalysis.test_date.desc(), ChemicalAnalysis.ladle_no.desc())

    analyses = None
    grouped = None

    if group_by in ('furnace', 'decision', 'test_date'):
        # Grouping: fetch all matching rows ordered by the group column then base order.
        group_col_map = {
            'furnace': ChemicalAnalysis.furnace_id,
            'decision': ChemicalAnalysis.decision,
            'test_date': ChemicalAnalysis.test_date,
        }
        group_col = group_col_map[group_by]
        rows = query.order_by(group_col, *base_order).all()

        furnace_codes = {f.id: f.furnace_code for f in furnaces}
        grouped = []
        current_key = object()  # sentinel that won't match any real key
        for row in rows:
            if group_by == 'furnace':
                key = row.furnace_id
                label = furnace_codes.get(row.furnace_id) or (row.furnace.furnace_code if row.furnace else '-')
            elif group_by == 'decision':
                key = row.decision
                label = row.decision or '-'
            else:  # test_date
                key = row.test_date
                label = str(row.test_date) if row.test_date else '-'
            if key != current_key or not grouped:
                grouped.append((label, []))
                current_key = key
            grouped[-1][1].append(row)
        total = len(rows)
    else:
        analyses = query.order_by(*base_order).paginate(page=page, per_page=per_page)
        total = analyses.total

    from app.services import export_service, table_registry
    return render_template('chemical/list.html',
                          picker_columns=export_service.picker_meta(table_registry.chemical_columns()),
                          analyses=analyses,
                          grouped=grouped,
                          total=total,
                          group_by=group_by or '',
                          per_page=per_page,
                          per_page_options=(10, 20, 50, 100),
                          furnaces=furnaces,
                          specs=specs,
                          production_orders=production_orders,
                          products=products,
                          decisions=get_all_decisions(),
                          get_decision_color=get_decision_color,
                          date_from=filters['date_from'],
                          date_to=filters['date_to'],
                          selected_furnace=filters['furnace_id'],
                          selected_decision=filters['decision'],
                          filters=filters)


@chemical_bp.route('/export.xlsx')
@login_required
@requires_permission('chemical', 'list')
def export_excel():
    """Export/print filtered chemical analyses, respecting current filters.

    Honours ``cols=`` (which fields), ``ids=`` (which rows) and ``format=print``
    (printable HTML instead of .xlsx) from the Table Tools picker. The field
    registry (all elements, equivalents, reasons …) lives in table_registry.
    """
    from app.services import export_service, table_registry

    query, _ = _filtered_query(request.args)
    query = query.order_by(ChemicalAnalysis.test_date.desc(),
                           ChemicalAnalysis.ladle_no.desc())
    query = export_service.filter_ids(query, ChemicalAnalysis.id, request.args.get("ids"))
    rows = query.all()

    selected = export_service.resolve_columns(
        table_registry.chemical_columns(), request.args.get("cols"))
    if request.args.get("format") == "print":
        return export_service.build_print("Chemical Analyses", selected, rows)
    return export_service.build_xlsx("Chemical", selected, rows, "chemical_analyses.xlsx")


@chemical_bp.route('/add', methods=['GET', 'POST'])
@login_required
@requires_permission('chemical', 'add')
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

            # Ladle melt weight (kg of the pour) — optional
            weight = request.form.get('weight')
            analysis.weight = float(weight) if weight else None

            # Chemical elements
            for element in ['carbon', 'silicon', 'magnesium', 'copper', 'chromium',
                           'sulfur', 'manganese', 'phosphorus', 'lead', 'aluminum']:
                value = request.form.get(element)
                if value:
                    setattr(analysis, element, float(value))

            # Calculate equivalents
            analysis.calculate_equivalents()

            # Auto-calculate decision from element rules
            element_values = {}
            for element in ['carbon', 'silicon', 'magnesium', 'copper', 'chromium',
                           'sulfur', 'manganese', 'phosphorus', 'lead', 'aluminum']:
                val = getattr(analysis, element)
                if val is not None:
                    element_values[element] = val
            if analysis.carbon_equivalent is not None:
                element_values['carbon_equivalent'] = analysis.carbon_equivalent
            if analysis.manganese_equivalent is not None:
                element_values['manganese_equivalent'] = analysis.manganese_equivalent
            if analysis.magnesium_equivalent is not None:
                element_values['magnesium_equivalent'] = analysis.magnesium_equivalent

            auto_result = calculate_auto_decision(element_values)
            user_decision = request.form.get('decision')

            if auto_result.get('recommended_decision'):
                analysis.decision = auto_result['recommended_decision']
                worst = ', '.join(auto_result.get('worst_elements', []))
                analysis.reason = request.form.get('reason') or f"Auto-decision based on: {worst}"
                # Allow manual override only if user explicitly chose different
                if user_decision and user_decision != auto_result['recommended_decision']:
                    analysis.decision = user_decision
                    analysis.reason = request.form.get('reason') or f"Manual override (auto was: {auto_result['recommended_decision']})"
            else:
                analysis.decision = user_decision
                analysis.reason = request.form.get('reason')

            # Quality control
            analysis.engineer_notes = request.form.get('engineer_notes')
            analysis.notes = request.form.get('notes')

            # Check for defects based on specs
            analysis.has_defect = validate_against_specs(analysis)

            # Metadata
            analysis.created_by_id = current_user.id

            # Link to a production order when added from the order progress page
            # (the "Add Analysis" button passes ?order_id=). Falls back to args for GET->POST.
            order_id = request.form.get('order_id') or request.args.get('order_id')
            if order_id:
                try:
                    analysis.production_order_id = int(order_id)
                except (TypeError, ValueError):
                    pass

            db.session.add(analysis)
            db.session.commit()

            # Trigger decision engine: assign mechanical roles
            if analysis.decision and analysis.ladle_id:
                try:
                    from app.services.pipe_decision_service import assign_mechanical_roles
                    assign_mechanical_roles(analysis.ladle_id)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception(
                        "assign_mechanical_roles failed for ladle %s", analysis.ladle_id
                    )

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
@requires_permission('chemical', 'list')
def detail(id):
    """View chemical analysis details"""
    analysis = ChemicalAnalysis.query.get_or_404(id)
    specs = {s.element_code: s for s in ElementSpecification.query.all()}

    # Get related pipes
    pipes = analysis.pipes.all()

    # Get related mechanical tests
    mech_tests = analysis.mechanical_tests.all()

    # Get attachments
    from app.models.attachment import Attachment
    attachments = Attachment.get_for_record('chemical_analyses', analysis.id)

    # Change history
    from app.models.audit import AuditLog
    audit_entries = (
        AuditLog.query
        .filter_by(table_name='chemical_analyses', record_id=analysis.id)
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
        .all()
    )

    return render_template('chemical/detail.html',
                          analysis=analysis,
                          specs=specs,
                          pipes=pipes,
                          mech_tests=mech_tests,
                          attachments=attachments,
                          audit_entries=audit_entries,
                          table_name='chemical_analyses',
                          record_id=analysis.id,
                          redirect_url=url_for('chemical.detail', id=analysis.id))


@chemical_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@requires_permission('chemical', 'edit')
def edit(id):
    """Edit chemical analysis"""
    if not current_user.can_edit:
        flash('You do not have permission to edit records.', 'error')
        return redirect(url_for('chemical.detail', id=id))

    analysis = ChemicalAnalysis.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Decision immutability (§15/§44): remember the saved decision so a
            # change can be gated on supervisor authority below.
            original_decision = analysis.decision

            # Update fields
            analysis.furnace_id = int(request.form['furnace_id'])

            # Ladle melt weight (kg of the pour) — optional
            weight = request.form.get('weight')
            analysis.weight = float(weight) if weight else None

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

            # Auto-calculate decision from element rules
            element_values = {}
            for element in ['carbon', 'silicon', 'magnesium', 'copper', 'chromium',
                           'sulfur', 'manganese', 'phosphorus', 'lead', 'aluminum']:
                val = getattr(analysis, element)
                if val is not None:
                    element_values[element] = val
            if analysis.carbon_equivalent is not None:
                element_values['carbon_equivalent'] = analysis.carbon_equivalent
            if analysis.manganese_equivalent is not None:
                element_values['manganese_equivalent'] = analysis.manganese_equivalent
            if analysis.magnesium_equivalent is not None:
                element_values['magnesium_equivalent'] = analysis.magnesium_equivalent

            auto_result = calculate_auto_decision(element_values)
            user_decision = request.form.get('decision')

            if auto_result.get('recommended_decision'):
                analysis.decision = auto_result['recommended_decision']
                worst = ', '.join(auto_result.get('worst_elements', []))
                analysis.reason = request.form.get('reason') or f"Auto-decision based on: {worst}"
                if user_decision and user_decision != auto_result['recommended_decision']:
                    analysis.decision = user_decision
                    analysis.reason = request.form.get('reason') or f"Manual override (auto was: {auto_result['recommended_decision']})"
            else:
                analysis.decision = user_decision
                analysis.reason = request.form.get('reason')

            # Decision locking (§15/§44): once a decision is saved, changing it
            # requires supervisor/admin authority and a written reason.
            if (
                original_decision
                and analysis.decision != original_decision
            ):
                if not current_user.is_supervisor:
                    db.session.rollback()
                    flash(
                        'قرار المعمل مسجل ولا يمكن تعديله — تغيير القرار يتطلب صلاحية مشرف/مدير.'
                        ' Decision is locked; changing it requires a supervisor/manager.',
                        'error',
                    )
                    return redirect(url_for('chemical.edit', id=id))
                if not (request.form.get('reason') or '').strip():
                    db.session.rollback()
                    flash(
                        'يجب كتابة سبب لتغيير القرار. A reason is required to change a saved decision.',
                        'error',
                    )
                    return redirect(url_for('chemical.edit', id=id))

            # Quality control
            analysis.engineer_notes = request.form.get('engineer_notes')
            analysis.notes = request.form.get('notes')

            # Check for defects
            analysis.has_defect = validate_against_specs(analysis)

            db.session.commit()

            # Re-trigger decision engine on decision change
            if analysis.decision and analysis.ladle_id:
                try:
                    from app.services.pipe_decision_service import assign_mechanical_roles
                    assign_mechanical_roles(analysis.ladle_id)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception(
                        "assign_mechanical_roles failed for ladle %s", analysis.ladle_id
                    )

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


@chemical_bp.route('/api/ocr-extract', methods=['POST'])
@login_required
@requires_permission('chemical', 'add')
def api_ocr_extract():
    """Extract element values from a spectrometer printout image via Gemini Vision."""
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image file uploaded'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    try:
        image_bytes = file.read()
        if len(image_bytes) < 1000:
            return jsonify({'success': False, 'error': 'Image file is too small'}), 400

        from app.services.ocr_service import extract_elements_from_image
        elements = extract_elements_from_image(image_bytes, file.filename)

        # Count how many values were successfully extracted
        extracted_count = sum(1 for v in elements.values() if v is not None)
        total_count = len(elements)

        return jsonify({
            'success': True,
            'elements': elements,
            'extracted': extracted_count,
            'total': total_count,
            'message': f'Extracted {extracted_count}/{total_count} elements. Please verify the values.',
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'OCR extraction failed: {str(e)}'}), 500


@chemical_bp.route('/api/validate', methods=['POST'])
@login_required
@requires_permission('chemical', 'add')
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
@requires_permission('chemical', 'auto_decide')
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
@requires_permission('ai', 'chemical_analysis')
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
@requires_permission('ai', 'chemical_analysis')
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


@chemical_bp.route('/auto-decide/<int:id>', methods=['POST'])
@login_required
@requires_permission('chemical', 'auto_decide')
def auto_decide(id):
    """Apply automatic decision to a chemical analysis based on element rules"""
    if not current_user.can_edit:
        flash('You do not have permission to perform this action.', 'error')
        return redirect(url_for('chemical.detail', id=id))

    analysis = ChemicalAnalysis.query.get_or_404(id)

    # Get element values
    element_values = analysis.get_element_values()

    # Calculate auto decision
    result = calculate_auto_decision(element_values)

    if result.get('decision'):
        analysis.decision = result['decision']
        analysis.reason = result.get('reason', '')
        db.session.commit()
        flash(f'Auto-decision applied: {result["decision"]}', 'success')
    else:
        flash('Could not calculate auto-decision. Check element values.', 'warning')

    return redirect(url_for('chemical.detail', id=id))


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
