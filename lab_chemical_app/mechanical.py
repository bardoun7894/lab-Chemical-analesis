"""
Mechanical Tests Routes
"""
import os
from io import BytesIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from datetime import date, datetime
from app import db
from app.models.mechanical import MechanicalTest
from app.models.chemical import ChemicalAnalysis
from app.models.pipe import Pipe
from app.services.ai_service import generate_mechanical_analysis, generate_mechanical_stream
from app.services import mechanical_decision_service
from app.services.permission_service import requires_permission

mechanical_bp = Blueprint('mechanical', __name__)


MICROSTRUCTURE_ALLOWED = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}


def _save_microstructure_image(file_storage, test_id, slot):
    """Save an uploaded microstructure image next to static/uploads/mechanical/<test_id>/.

    Returns the DB-relative path (uploads/mechanical/<test_id>/<slot>_<name>) or None.
    """
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in MICROSTRUCTURE_ALLOWED:
        return None

    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
    rel_dir = os.path.join('uploads', 'mechanical', str(test_id))
    abs_dir = os.path.join(static_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    safe_name = secure_filename(f'{slot}_{file_storage.filename}')
    abs_path = os.path.join(abs_dir, safe_name)
    file_storage.save(abs_path)
    return os.path.join(rel_dir, safe_name).replace('\\', '/')


# Map of advanced numeric range-filter arg names -> (model column, operator)
_RANGE_FILTERS = [
    ('tensile_mpa_min', MechanicalTest.tensile_mpa, 'ge'),
    ('tensile_mpa_max', MechanicalTest.tensile_mpa, 'le'),
    ('elongation_min', MechanicalTest.elongation, 'ge'),
    ('elongation_max', MechanicalTest.elongation, 'le'),
    ('hardness_min', MechanicalTest.hardness, 'ge'),
    ('hardness_max', MechanicalTest.hardness, 'le'),
    ('nodularity_percent_min', MechanicalTest.nodularity_percent, 'ge'),
    ('nodularity_percent_max', MechanicalTest.nodularity_percent, 'le'),
]


def _filtered_query(args):
    """Build the MechanicalTest query from request args.

    Shared by list() and export_excel() so the two never drift.
    Returns (query, filters_dict) where filters_dict holds the parsed values
    for round-tripping into the template.
    """
    date_from = args.get('date_from')
    date_to = args.get('date_to')
    diameter = args.get('diameter', type=int)
    decision = args.get('decision')
    code = args.get('code')
    pipe_code = args.get('pipe_code')
    ladle_id = args.get('ladle_id')
    shift = args.get('shift', type=int)
    status = args.get('status')
    tester_name = args.get('tester_name')

    query = MechanicalTest.query

    if date_from:
        query = query.filter(MechanicalTest.test_date >= date_from)
    if date_to:
        query = query.filter(MechanicalTest.test_date <= date_to)
    if diameter:
        query = query.filter(MechanicalTest.diameter == diameter)
    if decision:
        query = query.filter(MechanicalTest.decision == decision)
    if code:
        query = query.filter(MechanicalTest.code.ilike(f"%{code}%"))
    if pipe_code:
        query = query.filter(MechanicalTest.pipe_code.ilike(f"%{pipe_code}%"))
    if ladle_id:
        query = query.filter(MechanicalTest.ladle_id.ilike(f"%{ladle_id}%"))
    if shift:
        query = query.filter(MechanicalTest.shift == shift)
    if status:
        query = query.filter(MechanicalTest.status == status)
    if tester_name:
        query = query.filter(MechanicalTest.tester_name.ilike(f"%{tester_name}%"))

    # Advanced numeric-range filters
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
        'diameter': diameter,
        'decision': decision,
        'code': code,
        'pipe_code': pipe_code,
        'ladle_id': ladle_id,
        'shift': shift,
        'status': status,
        'tester_name': tester_name,
    }
    for arg_name, _, _ in _RANGE_FILTERS:
        filters[arg_name] = args.get(arg_name)

    return query, filters


@mechanical_bp.route('/')
@login_required
@requires_permission('mechanical', 'list')
def list():
    """List all mechanical tests"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    group_by = request.args.get('group_by')

    query, filters = _filtered_query(request.args)

    base_order = (MechanicalTest.test_date.desc(),)

    tests = None
    grouped = None

    if group_by in ('diameter', 'decision', 'shift', 'status', 'test_date'):
        group_col_map = {
            'diameter': MechanicalTest.diameter,
            'decision': MechanicalTest.decision,
            'shift': MechanicalTest.shift,
            'status': MechanicalTest.status,
            'test_date': MechanicalTest.test_date,
        }
        group_col = group_col_map[group_by]
        rows = query.order_by(group_col, *base_order).all()

        grouped = []
        current_key = object()  # sentinel that won't match any real key
        for row in rows:
            if group_by == 'diameter':
                key = row.diameter
                label = f"DN{row.diameter}" if row.diameter else '-'
            elif group_by == 'decision':
                key = row.decision
                label = row.decision or '-'
            elif group_by == 'shift':
                key = row.shift
                label = (f"Shift {row.shift}" if row.shift else '-')
            elif group_by == 'status':
                key = row.status
                label = row.status or '-'
            else:  # test_date
                key = row.test_date
                label = str(row.test_date) if row.test_date else '-'
            if key != current_key or not grouped:
                grouped.append((label, []))
                current_key = key
            grouped[-1][1].append(row)
        total = len(rows)
    else:
        tests = query.order_by(*base_order).paginate(page=page, per_page=per_page)
        total = tests.total

    return render_template('mechanical/list.html',
                          tests=tests,
                          grouped=grouped,
                          total=total,
                          group_by=group_by or '',
                          per_page=per_page,
                          per_page_options=(10, 20, 50, 100),
                          date_from=filters['date_from'],
                          date_to=filters['date_to'],
                          selected_diameter=filters['diameter'],
                          selected_decision=filters['decision'],
                          filters=filters)


@mechanical_bp.route('/export.xlsx')
@login_required
@requires_permission('mechanical', 'list')
def export_excel():
    """Export filtered mechanical tests to xlsx, respecting current filters."""
    import xlsxwriter

    query, _ = _filtered_query(request.args)
    rows = query.order_by(MechanicalTest.test_date.desc()).all()

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Mechanical")
    bold = wb.add_format({"bold": True, "bg_color": "#D3D3D3"})

    headers = ["Date", "Pipe Code", "DN", "Ladle ID", "Tensile (MPa)",
               "Elongation", "Hardness", "Nodularity", "Shift", "Tester",
               "Status", "Decision"]
    for c, h in enumerate(headers):
        ws.write(0, c, h, bold)

    for r, t in enumerate(rows, start=1):
        tensile = t.tensile_mpa
        if tensile is None and t.tensile_strength is not None:
            tensile = t.tensile_strength * 9.8
        ws.write(r, 0, str(t.test_date) if t.test_date else "")
        ws.write(r, 1, t.pipe_code or t.code or "")
        ws.write(r, 2, t.diameter)
        ws.write(r, 3, t.ladle_id or "")
        ws.write(r, 4, tensile)
        ws.write(r, 5, t.elongation)
        ws.write(r, 6, t.hardness)
        ws.write(r, 7, t.nodularity_percent)
        ws.write(r, 8, t.shift)
        ws.write(r, 9, t.tester_name or "")
        ws.write(r, 10, t.status or "")
        ws.write(r, 11, t.decision or "")

    wb.close()
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="mechanical_tests.xlsx",
    )


@mechanical_bp.route('/add', methods=['GET', 'POST'])
@login_required
@requires_permission('mechanical', 'add')
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

            # Auto-calculate decision from mechanical rules
            property_values = {}
            for prop in ['tensile_strength', 'elongation', 'nodularity_percent',
                        'hardness', 'carbides', 'nodule_count']:
                val = getattr(test, prop, None)
                if val is not None:
                    property_values[prop] = val
            # Also check ferrite
            ferrite_val = getattr(test, 'percent_70', None)
            if ferrite_val is not None:
                property_values['ferrite'] = ferrite_val

            auto_result = mechanical_decision_service.calculate_auto_decision(property_values)
            user_decision = request.form.get('decision')

            if auto_result.get('recommended_decision'):
                test.decision = auto_result['recommended_decision']
                test.reason = auto_result.get('summary', '')
                # Allow manual override
                if user_decision and user_decision != auto_result['recommended_decision']:
                    test.decision = user_decision
                    test.reason = request.form.get('reason') or f"Manual override (auto was: {auto_result['recommended_decision']})"
            else:
                test.decision = user_decision
                test.reason = request.form.get('reason')

            # Quality control
            test.shift = int(request.form.get('shift') or 1)
            test.tester_name = request.form.get('tester_name')
            test.comments = request.form.get('comments')

            # Link to pipe (required now with pipe_code)
            pipe_id = request.form.get('pipe_id')
            if pipe_id:
                test.pipe_id = int(pipe_id)
                # Get pipe and set pipe_code
                pipe = db.session.get(Pipe, int(pipe_id))
                if pipe:
                    test.pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.ladle_id or ''}-{pipe.arrange_pipe or 1}"
                    # The selected pipe is the authority on the ladle. The form's
                    # ladle_id is a readonly mirror filled by JS and arrives empty
                    # whenever that JS doesn't run — which left the test unlinked
                    # and blocked the decision cascade, stranding pipes at WAITING.
                    if pipe.ladle_id:
                        test.ladle_id = pipe.ladle_id

            # Metadata
            test.created_by_id = current_user.id

            db.session.add(test)
            db.session.commit()

            # Handle microstructure images (needs test.id after commit)
            polished_path = _save_microstructure_image(
                request.files.get('image_as_polished'), test.id, 'polished'
            )
            etched_path = _save_microstructure_image(
                request.files.get('image_etched'), test.id, 'etched'
            )
            if polished_path:
                test.image_as_polished = polished_path
            if etched_path:
                test.image_etched = etched_path
            if polished_path or etched_path:
                db.session.commit()

            # Trigger decision engine: propagate mechanical result
            if test.decision and test.ladle_id:
                try:
                    from app.services.pipe_decision_service import propagate_mechanical_result
                    propagate_mechanical_result(test)
                except Exception:
                    pass  # Non-critical

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
                          orders=_orders_with_pipes(),
                          prefill_ladle=request.args.get('ladle_id', ''),
                          today=date.today())


def _orders_with_pipes():
    """Production orders that actually have at least one pipe.

    The picker's Order dropdown is keyed on the SAME column the search endpoint
    filters (Pipe.production_order_id), so every option matches >=1 pipe. This
    avoids the 'dropdown renders but matches nothing' trap seen with linkage that
    was never populated.
    """
    from app.models.production_order import ProductionOrder
    linked_ids = [
        row[0]
        for row in db.session.query(Pipe.production_order_id)
        .filter(Pipe.production_order_id.isnot(None))
        .distinct()
        .all()
    ]
    if not linked_ids:
        return []
    return (
        ProductionOrder.query.filter(ProductionOrder.id.in_(linked_ids))
        .order_by(ProductionOrder.order_number.desc())
        .all()
    )


@mechanical_bp.route('/api/pipes-search')
@login_required
@requires_permission('mechanical', 'list')
def api_pipes_search():
    """Server-side pipe picker search for the mechanical-test form.

    Replaces the old client-side filtering of a top-50-by-date render, which
    buried real pipes behind newer demo pipes (and no JS filter could surface a
    pipe that was never rendered). Filters the full pipes table by:
      q     - substring of pipe_code / no_code / ladle_id
      dn    - exact diameter (DN)
      ladle - substring of ladle_id
      order - exact production_order_id
      date  - exact production_date (YYYY-MM-DD)
      annealing - exact Annealing-stage date (YYYY-MM-DD)
    """
    from app.models.pipe import PipeStage

    q = (request.args.get('q') or '').strip()
    dn = (request.args.get('dn') or '').strip()
    ladle = (request.args.get('ladle') or '').strip()
    order_id = (request.args.get('order') or '').strip()
    pdate = (request.args.get('date') or '').strip()
    annealing = (request.args.get('annealing') or '').strip()

    query = Pipe.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Pipe.pipe_code.ilike(like),
            Pipe.no_code.ilike(like),
            Pipe.ladle_id.ilike(like),
        ))
    if dn:
        try:
            query = query.filter(Pipe.diameter == int(dn))
        except ValueError:
            pass
    if ladle:
        query = query.filter(Pipe.ladle_id.ilike(f"%{ladle}%"))
    if order_id:
        try:
            query = query.filter(Pipe.production_order_id == int(order_id))
        except ValueError:
            pass
    if pdate:
        try:
            d = datetime.strptime(pdate, '%Y-%m-%d').date()
            query = query.filter(Pipe.production_date == d)
        except ValueError:
            pass
    if annealing:
        try:
            ad = datetime.strptime(annealing, '%Y-%m-%d').date()
            # Pipes that have an Annealing stage on that exact date
            annealed_ids = (db.session.query(PipeStage.pipe_id)
                            .filter(PipeStage.stage_name == 'Annealing',
                                    PipeStage.stage_date == ad)
                            .distinct())
            query = query.filter(Pipe.id.in_(annealed_ids))
        except ValueError:
            pass

    pipes = query.order_by(Pipe.production_date.desc()).limit(200).all()

    # Annealing date per pipe (one grouped query, no N+1)
    pipe_ids = [p.id for p in pipes]
    annealing_map = {}
    if pipe_ids:
        for pid, sdate in (db.session.query(PipeStage.pipe_id, PipeStage.stage_date)
                           .filter(PipeStage.pipe_id.in_(pipe_ids),
                                   PipeStage.stage_name == 'Annealing',
                                   PipeStage.stage_date.isnot(None))
                           .all()):
            s = sdate.isoformat()
            if pid not in annealing_map or s > annealing_map[pid]:
                annealing_map[pid] = s

    # Order numbers per production_order_id (one query, no N+1)
    order_numbers = {}
    order_ids = {p.production_order_id for p in pipes if p.production_order_id}
    if order_ids:
        from app.models.production_order import ProductionOrder
        for oid, onum in (db.session.query(ProductionOrder.id, ProductionOrder.order_number)
                          .filter(ProductionOrder.id.in_(order_ids)).all()):
            order_numbers[oid] = onum

    results = [{
        'id': p.id,
        'code': p.pipe_code or f"{p.no_code}-{p.ladle_id or ''}-{p.arrange_pipe or 1}",
        'ladle': p.ladle_id or '',
        'diameter': p.diameter if p.diameter is not None else '',
        'klass': p.pipe_class or '',
        'order_id': p.production_order_id or '',
        'order_no': order_numbers.get(p.production_order_id, ''),
        'arrange': p.arrange_pipe if p.arrange_pipe is not None else '',
        'role': p.mechanical_test_role or '',
        'date': p.production_date.isoformat() if p.production_date else '',
        'annealing': annealing_map.get(p.id, ''),
    } for p in pipes]
    return jsonify({'pipes': results, 'count': len(results)})


@mechanical_bp.route('/<int:id>')
@login_required
@requires_permission('mechanical', 'list')
def detail(id):
    """View mechanical test details"""
    test = MechanicalTest.query.get_or_404(id)
    from app.models.attachment import Attachment
    from app.models.audit import AuditLog
    attachments = Attachment.get_for_record('mechanical_tests', test.id)
    audit_entries = (
        AuditLog.query
        .filter_by(table_name='mechanical_tests', record_id=test.id)
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
        .all()
    )
    # Retest history (active + any superseded predecessors for this pipe)
    retest_history = []
    if test.pipe_id or test.pipe_code:
        q = MechanicalTest.query.filter(
            db.or_(
                MechanicalTest.pipe_id == test.pipe_id,
                MechanicalTest.pipe_code == test.pipe_code,
            )
        ).order_by(MechanicalTest.test_date.desc(), MechanicalTest.id.desc())
        retest_history = q.all()
    return render_template('mechanical/detail.html', test=test,
                          attachments=attachments,
                          audit_entries=audit_entries,
                          retest_history=retest_history,
                          table_name='mechanical_tests',
                          record_id=test.id,
                          redirect_url=url_for('mechanical.detail', id=test.id))


@mechanical_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@requires_permission('mechanical', 'edit')
def edit(id):
    """Edit mechanical test"""
    if not current_user.can_edit:
        flash('You do not have permission to edit records.', 'error')
        return redirect(url_for('mechanical.detail', id=id))

    test = MechanicalTest.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # §4.4 immutability: snapshot the persisted state BEFORE any edit so
            # we can detect a decision change and, if so, preserve the prior
            # result as a SUPERSEDED record instead of overwriting it in place.
            _orig_decision = test.decision
            _orig_snapshot = {
                c.name: getattr(test, c.name)
                for c in MechanicalTest.__table__.columns
            }
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

            # Auto-calculate decision from mechanical rules
            property_values = {}
            for prop in ['tensile_strength', 'elongation', 'nodularity_percent',
                        'hardness', 'carbides', 'nodule_count']:
                val = getattr(test, prop, None)
                if val is not None:
                    property_values[prop] = val
            ferrite_val = getattr(test, 'percent_70', None)
            if ferrite_val is not None:
                property_values['ferrite'] = ferrite_val

            auto_result = mechanical_decision_service.calculate_auto_decision(property_values)
            user_decision = request.form.get('decision')

            if auto_result.get('recommended_decision'):
                test.decision = auto_result['recommended_decision']
                test.reason = auto_result.get('summary', '')
                if user_decision and user_decision != auto_result['recommended_decision']:
                    test.decision = user_decision
                    test.reason = request.form.get('reason') or f"Manual override (auto was: {auto_result['recommended_decision']})"
            else:
                test.decision = user_decision
                test.reason = request.form.get('reason')

            # Quality control
            test.shift = int(request.form.get('shift') or 1)
            test.tester_name = request.form.get('tester_name')
            test.comments = request.form.get('comments')

            # Update pipe link with pipe_code
            pipe_id = request.form.get('pipe_id')
            if pipe_id:
                test.pipe_id = int(pipe_id)
                pipe = db.session.get(Pipe, int(pipe_id))
                if pipe:
                    test.pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.ladle_id or ''}-{pipe.arrange_pipe or 1}"
                    if pipe.ladle_id:
                        test.ladle_id = pipe.ladle_id
            else:
                test.pipe_id = None
                test.pipe_code = None

            test.modified_by_id = current_user.id

            # Microstructure image handling
            if request.form.get('remove_image_as_polished'):
                test.image_as_polished = None
            if request.form.get('remove_image_etched'):
                test.image_etched = None
            polished_path = _save_microstructure_image(
                request.files.get('image_as_polished'), test.id, 'polished'
            )
            etched_path = _save_microstructure_image(
                request.files.get('image_etched'), test.id, 'etched'
            )
            if polished_path:
                test.image_as_polished = polished_path
            if etched_path:
                test.image_etched = etched_path

            # §4.4: if this test already had a decision and the edit CHANGES it,
            # do not overwrite the prior result. Preserve it as SUPERSEDED and
            # create a new ACTIVE record carrying the edited values + new decision.
            decision_changed = (
                _orig_decision is not None and test.decision != _orig_decision
            )

            if decision_changed:
                # §44: changing a recorded decision requires higher authority.
                if not current_user.is_supervisor:
                    db.session.rollback()
                    flash('Changing a recorded decision requires a supervisor/manager. '
                          'تغيير القرار المسجل يتطلب صلاحية مشرف/مدير.', 'error')
                    return redirect(url_for('mechanical.edit', id=test.id))
                reason = (request.form.get('retest_reason')
                          or request.form.get('reason') or '').strip()
                if not reason:
                    db.session.rollback()
                    flash('A reason is required when changing a recorded decision '
                          '(the prior result is preserved as superseded).', 'error')
                    return redirect(url_for('mechanical.edit', id=test.id))

                # Capture the edited values (the user's intended new record).
                edited = {
                    c.name: getattr(test, c.name)
                    for c in MechanicalTest.__table__.columns
                }

                # Revert the original record to its pre-edit snapshot and mark
                # it SUPERSEDED (immutable history).
                for k, v in _orig_snapshot.items():
                    setattr(test, k, v)
                test.status = 'SUPERSEDED'

                # Build the new ACTIVE record from the edited values.
                new_test = MechanicalTest()
                for k, v in edited.items():
                    if k in ('id', 'status', 'original_test_id',
                             'superseded_by_id', 'created_at', 'updated_at'):
                        continue
                    setattr(new_test, k, v)
                new_test.status = 'ACTIVE'
                new_test.original_test_id = test.id
                new_test.retest_reason = reason
                new_test.created_by_id = current_user.id

                db.session.add(new_test)
                db.session.flush()
                test.superseded_by_id = new_test.id
                db.session.commit()

                if new_test.decision and new_test.ladle_id:
                    try:
                        from app.services.pipe_decision_service import propagate_mechanical_result
                        propagate_mechanical_result(new_test)
                    except Exception:
                        pass

                flash('Decision changed — prior result preserved as superseded, '
                      'new active record created.', 'success')
                return redirect(url_for('mechanical.detail', id=new_test.id))

            # No decision change (or first decision): in-place edit is fine.
            db.session.commit()

            if test.decision and test.ladle_id:
                try:
                    from app.services.pipe_decision_service import propagate_mechanical_result
                    propagate_mechanical_result(test)
                except Exception:
                    pass

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
                          orders=_orders_with_pipes(),
                          edit_mode=True)


@mechanical_bp.route('/<int:id>/retest', methods=['POST'])
@login_required
@requires_permission('mechanical', 'retest')
def retest(id):
    """Create a retest for a mechanical test, marking the old one as SUPERSEDED.

    Only supervisors and admins can trigger a retest — operators can create initial tests
    but must escalate to a supervisor to invalidate existing results.
    """
    if not current_user.is_supervisor:
        flash('Only supervisors or admins can request a retest.', 'error')
        return redirect(url_for('mechanical.detail', id=id))

    # Retest reason is required — enforce it server-side
    retest_reason = (request.form.get('retest_reason') or '').strip()
    if not retest_reason:
        flash('Retest reason is required.', 'error')
        return redirect(url_for('mechanical.detail', id=id))

    original = MechanicalTest.query.get_or_404(id)

    # Create new test based on original
    new_test = MechanicalTest()
    new_test.test_date = date.today()
    new_test.test_number = original.test_number
    new_test.diameter = original.diameter
    new_test.code = original.code
    new_test.pipe_no = original.pipe_no
    new_test.pipe_code = original.pipe_code
    new_test.ladle_id = original.ladle_id
    new_test.pipe_id = original.pipe_id
    new_test.day = new_test.test_date.day
    new_test.month = new_test.test_date.month
    new_test.year = new_test.test_date.year
    new_test.shift = original.shift
    new_test.tester_name = original.tester_name
    new_test.original_test_id = original.id
    new_test.retest_reason = retest_reason
    new_test.status = 'ACTIVE'
    new_test.created_by_id = current_user.id

    # Mark original as superseded
    original.status = 'SUPERSEDED'
    original.superseded_by_id = None  # Will update after commit

    db.session.add(new_test)
    db.session.flush()
    original.superseded_by_id = new_test.id
    db.session.commit()

    # Re-run propagation so sibling pipes pick up the new state.
    # CRITICAL: a fresh retest has new_test.decision is None. Calling propagate
    # unconditionally would send None into the FAIL path and scrap the whole
    # ladle. Guard the call site (mirrors add:~185 / edit:~348). The propagate
    # service also early-returns on None (belt and suspenders).
    if new_test.decision and new_test.ladle_id:
        try:
            from app.services.pipe_decision_service import propagate_mechanical_result
            propagate_mechanical_result(new_test)
        except Exception:
            pass

    flash('Retest created. Please fill in the new test measurements.', 'info')
    return redirect(url_for('mechanical.edit', id=new_test.id))


@mechanical_bp.route('/api/ai-analysis', methods=['POST'])
@login_required
@requires_permission('ai', 'mechanical_analysis')
def api_ai_analysis():
    """API endpoint to generate AI analysis for mechanical test"""
    data = request.get_json()

    # Extract test values
    test_values = {}
    fields = ['tensile_strength', 'tensile_mpa', 'elongation', 'hardness',
              'nodularity_percent', 'percent_70', 'carbides', 'nodule_count',
              'd1', 'd2', 'd3', 'force_kgf']

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
@requires_permission('ai', 'mechanical_analysis')
def api_ai_analysis_stream():
    """Streaming API endpoint for mechanical test AI analysis"""
    data = request.get_json()

    # Extract test values
    test_values = {}
    fields = ['tensile_strength', 'tensile_mpa', 'elongation', 'hardness',
              'nodularity_percent', 'percent_70', 'carbides', 'nodule_count',
              'd1', 'd2', 'd3', 'force_kgf']

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


@mechanical_bp.route('/api/ocr-extract', methods=['POST'])
@login_required
@requires_permission('mechanical', 'add')
def api_ocr_extract():
    """Extract mechanical test values from a test certificate image via Gemini Vision."""
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image file uploaded'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    try:
        image_bytes = file.read()
        from app.services.mechanical_ocr_service import extract_mechanical_from_image
        values = extract_mechanical_from_image(image_bytes, file.filename)
        extracted_count = sum(1 for v in values.values() if v is not None)

        return jsonify({
            'success': True,
            'values': values,
            'extracted': extracted_count,
            'message': f'Extracted {extracted_count} values. Please verify.',
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'OCR failed: {str(e)}'}), 500


@mechanical_bp.route('/api/validate', methods=['POST'])
@login_required
@requires_permission('mechanical', 'add')
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
@requires_permission('mechanical', 'retest')
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
