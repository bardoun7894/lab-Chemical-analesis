"""
Customer certificates: warranty (QC-01-F-28) and work test (QC-01-F-26).

Pick an order, tick the pipes it covers, fill in the references that only exist
on the customer's paperwork, and print. The certificate number is issued once at
save so a reprint carries the same serial.
"""
import math
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import db
from app.models.certificate import Certificate, CertificatePipeClaim
from app.models.pipe import Pipe
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.services.certificate_service import (
    MTC_ELEMENTS, MTC_MECHANICAL_STANDARDS, MTC_CHEMICAL_STANDARD_SOURCE,
    batch_numbers, chemical_limits, customer_name, dn_range_label, group_pipes,
    item_description, mechanical_sample, mtc_chemical_summary, mtc_heats,
    pipe_lengths, certificate_standards,
    external_coating_statement, WORK_TEST_STANDARD_FALLBACK)
from app.services.certificate_issuance_service import (
    compatibility_conflicts, fallback_orders)
from app.services.permission_service import has_permission, requires_permission
from app.services.pipe_selection_service import (
    dedupe_pipe_ids, lookup_pipes, parse_order_ids, pipes_for_orders)

certificates_bp = Blueprint('certificates', __name__)

# The multi-order "From the system" panel names each loaded order's customer;
# exposed to form.html rather than duplicating the linked-vs-legacy-name logic
# in the template.
certificates_bp.add_app_template_global(customer_name, name='customer_name')


@certificates_bp.context_processor
def _form_defaults():
    """Today's date and the warranty periods, for the builder form."""
    return {
        'today': datetime.utcnow().date().isoformat(),
        'period_options': sorted(Certificate.PERIOD_WORDS.items()),
        'cert_types': Certificate.TYPES,
        'value_modes': Certificate.VALUE_MODES,
    }


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _cert_type(raw):
    return raw if raw in Certificate.TYPES else Certificate.WARRANTY


def _order_ids_from_request(values):
    """The repeatable ``order_ids`` plus the legacy single ``order_id``.

    Old links and bookmarks only ever sent one ``order_id`` — that still has
    to work, so it is folded into the same set rather than replaced by it.
    """
    raw_ids = []
    # The primary order leads: the picker posts it on its own select, and it
    # is loaded whether or not it was also ticked among the additional ones.
    primary = values.get('primary_order_id')
    if primary:
        raw_ids.append(primary)
    raw_ids.extend(values.getlist('order_ids'))
    legacy = values.get('order_id')
    if legacy:
        raw_ids.append(legacy)
    return parse_order_ids(raw_ids)


def _load_orders(order_ids):
    if not order_ids:
        return []
    rows = {o.id: o for o in
            ProductionOrder.query.filter(ProductionOrder.id.in_(order_ids)).all()}
    # Preserve the order the ids were picked in rather than a query's own order.
    return [rows[i] for i in order_ids if i in rows]


def _primary_order_id(values, orders):
    """The order the certificate is issued under.

    Chosen explicitly on the form (``primary_order_id``); it has to be one of
    the loaded orders, else the first loaded order stands in — which is what a
    legacy single-order link means anyway.
    """
    loaded = [o.id for o in orders]
    if not loaded:
        return None
    chosen = values.get('primary_order_id', type=int)
    return chosen if chosen in loaded else loaded[0]


def _parse_total_length(raw):
    """The confirmed metres, or None when the user left the computed sum alone."""
    try:
        value = float((raw or '').strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return round(value, 2)


def _mixed_dn_class(pipes):
    """``"DN600 K9, DN800 K9"`` when the pipes span more than one DN / class,
    else None. An MTC states one material, so it never mixes them.

    A pipe's DN / class falls back to its order's, the same way the printed
    rows resolve them, so a pipe with the field left blank is not flagged as
    a different material from its siblings.
    """
    combos = []
    for p in pipes:
        order = p.production_order
        combo = (p.diameter or (order.diameter if order else None),
                 p.pipe_class or (order.pipe_class if order else None))
        if combo not in combos:
            combos.append(combo)
    if len(combos) < 2:
        return None
    return ', '.join(
        ' '.join(part for part in ((f'DN{dn}' if dn else 'DN?'), (cls or '?')))
        for dn, cls in sorted(combos, key=lambda c: (c[0] or 0, c[1] or '')))


def _selectable_pipes(orders):
    """All pipes on these orders, ordered for display and eligibility marking."""
    if not orders:
        return []
    return pipes_for_orders([order.id for order in orders])


def _validate_posted_pipes(form, orders):
    """Atomically reload and validate the exact posted selection."""
    try:
        pipe_ids = dedupe_pipe_ids(form.getlist('pipe_ids'))
    except ValueError:
        return [], 'The submitted pipe selection contains an invalid ID.'
    if not pipe_ids:
        return [], 'Select at least one pipe for the certificate.'

    # Every issuer takes the same ascending lock order, preventing two
    # overlapping selections from deadlocking on PostgreSQL. SQLite ignores
    # FOR UPDATE, so POST starts with BEGIN IMMEDIATE (below) instead.
    locked_pipes = (Pipe.query
                    .filter(Pipe.id.in_(pipe_ids))
                    .order_by(Pipe.id)
                    .populate_existing()
                    .with_for_update()
                    .all())
    rows = {pipe.id: pipe for pipe in locked_pipes}
    missing = [pipe_id for pipe_id in pipe_ids if pipe_id not in rows]
    if missing:
        return [], ('Pipe ID(s) no longer exist: '
                    + ', '.join(str(pipe_id) for pipe_id in missing))

    # A certificate's claims also depend on order/product specification rows.
    # Lock and refresh those in deterministic order using Core statements so
    # an Application or Product edit cannot race validation and commit.
    order_ids = sorted({pipe.production_order_id for pipe in locked_pipes})
    if order_ids:
        locked_orders = db.session.execute(
            db.select(ProductionOrder)
            .where(ProductionOrder.id.in_(order_ids))
            .order_by(ProductionOrder.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars().all()
        product_ids = sorted({order.product_id for order in locked_orders
                              if order.product_id is not None})
        if product_ids:
            db.session.execute(
                db.select(Product)
                .where(Product.id.in_(product_ids))
                .order_by(Product.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalars().all()

    unavailable = [rows[pipe_id] for pipe_id in pipe_ids
                   if (rows[pipe_id].final_decision_value or '').upper()
                   != 'ACCEPT']
    if unavailable:
        details = []
        for pipe in unavailable:
            code = pipe.no_code or pipe.pipe_code or str(pipe.id)
            status = (pipe.final_decision_value or 'PENDING').upper()
            reason = (pipe.final_decision_reason or status).strip()
            details.append(f'{code} [{status}]: {reason}')
        return [], ('Only ACCEPT pipes can be certified; current status: '
                    + '; '.join(details))

    return [rows[pipe_id] for pipe_id in pipe_ids], None


def _begin_issuance_write():
    """Start the issuance transaction before any mutable certificate reads."""
    # Permission decorators may have opened a read-only ORM transaction. End
    # it without committing application state, then take SQLite's write lock
    # up front. PostgreSQL obtains the row locks in _validate_posted_pipes.
    db.session.rollback()
    if db.session.get_bind().dialect.name == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))


def _lock_number_namespace(cert_type, issue_date):
    """Serialize year/type certificate-number allocation on PostgreSQL."""
    if db.session.get_bind().dialect.name == 'postgresql':
        key = f'certificate-number:{cert_type}:{issue_date.year}'
        db.session.execute(
            text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': key})


def _replace_ordinary_claims(cert, pipes):
    """Stage the exclusive ordinary claims in the current transaction."""
    if cert.id is not None:
        CertificatePipeClaim.query.filter_by(
            certificate_id=cert.id).delete(synchronize_session=False)
        db.session.flush()
    if cert.reissue_of_id is not None:
        return
    cert.pipe_claims = [
        CertificatePipeClaim(pipe_id=pipe.id, cert_type=cert.cert_type)
        for pipe in pipes
    ]


def _apply_form(cert, form, orders, primary_id=None, selected_pipes=None):
    cert.warranty_start_date = _parse_date(form.get('warranty_start_date'))
    cert.customer_order_no = (form.get('customer_order_no') or '').strip() or None
    cert.project_name = (form.get('project_name') or '').strip() or None
    cert.notes = (form.get('notes') or '').strip() or None
    cert.total_length_m = _parse_total_length(form.get('total_length_m'))
    cert.show_pipe_numbers = form.get('show_pipe_numbers') in (
        '1', 'true', 'on', 'yes')

    try:
        years = int(form.get('warranty_years') or 3)
    except ValueError:
        years = 3
    cert.warranty_years = years if years in Certificate.PERIOD_WORDS else 3

    mode = form.get('values_mode')
    cert.values_mode = (mode if mode in Certificate.VALUE_MODES
                        else Certificate.MODE_STANDARD)

    issue = _parse_date(form.get('issue_date'))
    if issue:
        cert.issue_date = issue

    # Ordered the same way the picker showed them, so "the first ticked pipe"
    # below means the same thing a person reading the table would mean by it.
    if selected_pipes is None:
        selectable = _selectable_pipes(orders)
        selected = set(dedupe_pipe_ids(form.getlist('pipe_ids')))
        pipes = [p for p in selectable if p.id in selected]
    else:
        pipes = list(selected_pipes)
    cert.pipes = pipes

    if pipes:
        # Coverage is derived from what got ticked, not stored separately:
        # the distinct orders behind the selected pipes. The primary order —
        # whose customer the certificate is issued to — is the one chosen on
        # the form, provided at least one of its pipes was ticked; a primary
        # with nothing on the certificate would name a customer for pipes
        # that are all somebody else's.
        derived = []
        seen_ids = set()
        for p in pipes:
            if p.production_order_id not in seen_ids:
                seen_ids.add(p.production_order_id)
                derived.append(p.production_order)
        if primary_id in seen_ids:
            derived.sort(key=lambda o: o.id != primary_id)
        elif primary_id is not None:
            flash('The primary order has no ticked pipe; the certificate is '
                  f'issued under {derived[0].order_number} instead.', 'warning')
        cert.orders = derived
        cert.production_order_id = derived[0].id

    return pipes


def _customer_names(orders):
    return {customer_name(o) for o in orders if customer_name(o)}


def _same_type_certificates(pipes, cert_type, exclude_id=None):
    """Ordinary certificates of this type that claim these pipes.

    Reissues deliberately do not own an ordinary claim and therefore cannot
    block edits of their original or later authorized reissues.
    """
    pipe_ids = [pipe.id for pipe in pipes]
    if not pipe_ids:
        return []
    query = (Certificate.query
             .filter(Certificate.cert_type == cert_type)
             .filter(Certificate.reissue_of_id.is_(None))
             .filter(Certificate.pipes.any(Pipe.id.in_(pipe_ids))))
    if exclude_id is not None:
        query = query.filter(Certificate.id != exclude_id)
    return query.order_by(Certificate.id).all()


def _pipe_availability(pipes, cert_type, current_cert=None):
    allowed_original_id = None
    if current_cert is not None:
        allowed_original_id = current_cert.reissue_of_id or current_cert.id
    links = _same_type_certificates(
        pipes, cert_type, allowed_original_id)
    by_pipe = {}
    for linked in links:
        for pipe in linked.pipes:
            by_pipe.setdefault(pipe.id, linked)
    states = {}
    for pipe in pipes:
        status = (pipe.final_decision_value or 'PENDING').upper()
        unavailable = status != 'ACCEPT'
        states[pipe.id] = {
            'available': not unavailable and pipe.id not in by_pipe,
            'reason': ((pipe.final_decision_reason or status.title())
                       if unavailable else None),
            'certificate': by_pipe.get(pipe.id),
        }
    return states


def _selection_pipe_json(pipe, state, selected=False):
    """Serialize certificate policy for the shared selection component."""
    linked = state.get('certificate')
    reason = state.get('reason') or ''
    if linked:
        reason = (f'Already issued on {linked.certificate_no}. '
                  'Use print/edit on the original, or authorized reissue.')
    return {
        'id': pipe.id,
        'code': pipe.no_code or pipe.pipe_code or f'#{pipe.id}',
        'no_code': pipe.no_code or '',
        'barcode': pipe.warehouse_barcode or '',
        'dn': f'DN{pipe.diameter}' if pipe.diameter else '',
        'pipe_class': pipe.pipe_class or '',
        'order': (pipe.production_order.order_number
                  if pipe.production_order else ''),
        'status': ('ISSUED' if linked else
                   (pipe.final_decision_value or 'PENDING')),
        'reason': reason,
        'selectable': bool(state.get('available')),
        'selected': bool(selected and state.get('available')),
        'url': url_for('stages.view', id=pipe.id),
    }


def _requested_reissue(form, cert_type):
    """Resolve and authorize an exceptional reissue request."""
    raw_original_id = (form.get('reissue_of_id') or '').strip()
    if not raw_original_id:
        return None, None, None
    try:
        original_id = int(raw_original_id)
    except ValueError:
        return None, None, 'Select a valid original certificate for reissue.'
    original = Certificate.query.filter_by(
        id=original_id, reissue_of_id=None).first()
    if original is None:
        return None, None, 'The original certificate for reissue was not found.'
    if original.cert_type != cert_type:
        return None, None, 'A reissue must have the same type as its original certificate.'
    reason = (form.get('reissue_reason') or '').strip()
    if not reason:
        return None, None, 'A non-empty reissue reason is required.'
    if not has_permission(current_user.role, 'certificates', 'reissue'):
        return None, None, 'You do not have certificate reissue permission.'
    return original, reason, None


@certificates_bp.route('/')
@login_required
@requires_permission('certificates', 'list')
def index():
    cert_type = request.args.get('type')
    # The list shows each certificate's order number and customer, so bring the
    # order along rather than issuing a query per row.
    query = Certificate.query.options(
        joinedload(Certificate.production_order))
    if cert_type in Certificate.TYPES:
        query = query.filter(Certificate.cert_type == cert_type)
    certs = query.order_by(Certificate.id.desc()).limit(500).all()
    return render_template('certificates/list.html',
                           certificates=certs, active_type=cert_type)


@certificates_bp.route('/lookup-pipes')
@login_required
@requires_permission('certificates', 'add')
def lookup():
    """AJAX scan/manual lookup using certificate-specific eligibility."""
    term = request.args.get('q') or request.args.get('barcode') or ''
    matches = lookup_pipes(term)
    cert_type = _cert_type(request.args.get('type'))
    current_id = request.args.get('current_certificate_id', type=int)
    current_cert = (Certificate.query.filter_by(id=current_id).first()
                    if current_id else None)
    states = _pipe_availability(matches, cert_type, current_cert=current_cert)
    rows = [_selection_pipe_json(pipe, states[pipe.id]) for pipe in matches]
    return jsonify({'ok': bool(rows), 'matches': rows})


@certificates_bp.route('/new', methods=['GET', 'POST'])
@login_required
@requires_permission('certificates', 'add')
def new():
    if request.method == 'POST':
        _begin_issuance_write()
    try:
        order_ids = _order_ids_from_request(request.values)
    except ValueError:
        order_ids = []
        flash('The submitted production-order selection is invalid.', 'danger')

    lookup_term = (request.values.get('pipe_lookup') or '').strip()
    lookup_matches = lookup_pipes(lookup_term) if lookup_term else []
    for pipe in lookup_matches:
        if pipe.production_order_id not in order_ids:
            order_ids.append(pipe.production_order_id)

    orders = _load_orders(order_ids)
    cert_type = _cert_type(request.values.get('type'))

    primary_id = _primary_order_id(request.values, orders)

    if request.method == 'POST':
        if not orders:
            flash('Pick a production order first.', 'warning')
            return redirect(url_for('certificates.new', type=cert_type))

        reissue_of, reissue_reason, reissue_error = _requested_reissue(
            request.form, cert_type)
        if reissue_error:
            db.session.rollback()
            flash(reissue_error, 'danger')
            return _render_form(None, orders, cert_type, primary_id, set())

        selected_pipes, selection_error = _validate_posted_pipes(
            request.form, orders)
        raw_selected = request.form.getlist('pipe_ids')
        selected_ids = ({int(value) for value in raw_selected}
                        if all(str(value).strip().isdigit()
                               for value in raw_selected) else set())
        if selection_error:
            db.session.rollback()
            flash(selection_error, 'danger')
            return _render_form(
                None, orders, cert_type, primary_id, selected_ids)

        # A direct scan posts only the exact pipe IDs. Derive any additional
        # order coverage from the freshly reloaded pipes instead of requiring
        # (or trusting) matching client-supplied order IDs.
        loaded_order_ids = {order.id for order in orders}
        for pipe in selected_pipes:
            if pipe.production_order_id not in loaded_order_ids:
                orders.append(pipe.production_order)
                loaded_order_ids.add(pipe.production_order_id)

        selected_orders = []
        seen_order_ids = set()
        for pipe in selected_pipes:
            if pipe.production_order_id not in seen_order_ids:
                seen_order_ids.add(pipe.production_order_id)
                selected_orders.append(pipe.production_order)
        conflicts = compatibility_conflicts(selected_orders)
        if conflicts:
            db.session.rollback()
            flash('Incompatible production-order specifications: '
                  + '; '.join(conflicts), 'danger')
            return _render_form(
                None, orders, cert_type, primary_id, selected_ids)

        duplicates = _same_type_certificates(selected_pipes, cert_type)
        blocking_duplicates = [duplicate for duplicate in duplicates
                               if reissue_of is None
                               or duplicate.id != reissue_of.id]
        if blocking_duplicates:
            db.session.rollback()
            numbers = ', '.join(
                duplicate.certificate_no for duplicate in blocking_duplicates)
            flash('Selected pipe(s) already belong to saved certificate(s) '
                  f'of this type: {numbers}. Reprint or edit the original.',
                  'danger')
            return _render_form(
                None, orders, cert_type, primary_id, selected_ids)

        cert = Certificate(
            cert_type=cert_type,
            # A placeholder until _apply_form settles the real primary order
            # against whichever pipes get ticked; never left pointing at an
            # order that turns out to have no selected pipes.
            production_order_id=primary_id,
            issue_date=datetime.utcnow().date(),
            created_by_id=current_user.id,
            reissue_of_id=reissue_of.id if reissue_of else None,
            reissue_reason=reissue_reason,
        )
        pipes = _apply_form(
            cert, request.form, orders, primary_id,
            selected_pipes=selected_pipes)
        if not pipes:
            db.session.rollback()
            flash('Select at least one pipe for the certificate.', 'warning')
            return _render_form(None, orders, cert_type, primary_id, set())

        mixed = _mixed_dn_class(pipes) if cert_type == Certificate.MTC else None
        if mixed:
            db.session.rollback()
            flash('An MTC covers one DN and one class; these pipes span '
                  f'{mixed}. Untick the odd ones out.', 'danger')
            return _render_form(None, orders, cert_type, primary_id,
                                {p.id for p in pipes})

        if len(_customer_names(cert.orders)) > 1:
            flash('These pipes span more than one customer.', 'warning')

        if not _issue(cert):
            flash('Selected pipe(s) were claimed by another saved certificate '
                  'while this form was open. Reprint or edit the original.',
                  'danger')
            return _render_form(None, orders, cert_type, primary_id,
                                {p.id for p in pipes})

        flash(f'Certificate {cert.certificate_no} created.', 'success')
        return redirect(url_for('certificates.print_view', id=cert.id))

    return _render_form(
        None, orders, cert_type, primary_id,
        {pipe.id for pipe in lookup_matches})


@certificates_bp.route(
    '/new/selection/<cert_type>/<int:primary_id>/<int:reissue_id>/<order_token>')
@login_required
@requires_permission('certificates', 'add')
def new_selection_state(cert_type, primary_id, reissue_id, order_token):
    """No-JS bridge for the shared selector without losing cert state."""
    try:
        stored_ids = parse_order_ids(order_token.split(','))
        submitted = request.args.getlist('order_ids')
        order_ids = parse_order_ids(submitted) if submitted else stored_ids
    except ValueError:
        order_ids = stored_ids if 'stored_ids' in locals() else []
    values = {
        'type': _cert_type(cert_type),
        'primary_order_id': primary_id,
        'order_ids': order_ids,
    }
    if reissue_id:
        values['reissue_of_id'] = reissue_id
    pipe_lookup = (request.args.get('pipe_lookup') or '').strip()
    if pipe_lookup:
        values['pipe_lookup'] = pipe_lookup
    return redirect(url_for('certificates.new', **values))


@certificates_bp.route(
    '/<int:id>/edit/selection/<int:primary_id>/<order_token>')
@login_required
@requires_permission('certificates', 'edit')
def edit_selection_state(id, primary_id, order_token):
    """No-JS selector bridge retaining the certificate being edited."""
    Certificate.query.get_or_404(id)
    try:
        stored_ids = parse_order_ids(order_token.split(','))
        submitted = request.args.getlist('order_ids')
        order_ids = parse_order_ids(submitted) if submitted else stored_ids
    except ValueError:
        order_ids = stored_ids if 'stored_ids' in locals() else []
    values = {'primary_order_id': primary_id, 'order_ids': order_ids}
    pipe_lookup = (request.args.get('pipe_lookup') or '').strip()
    if pipe_lookup:
        values['pipe_lookup'] = pipe_lookup
    return redirect(url_for('certificates.edit', id=id, **values))


def _render_form(cert, orders, cert_type, primary_id, selected_ids):
    """The builder form, with everything the picker needs in one place.

    Each pipe's length rides along so the form can add up what got ticked
    and show the figure the user is confirming — the same figure the print
    would compute (``pipe_lengths`` is shared with ``group_pipes``).
    """
    pipes = _selectable_pipes(orders) if orders else []
    legacy_orders = fallback_orders(orders)
    if legacy_orders:
        numbers = ', '.join(order.order_number for order in legacy_orders)
        current_app.logger.warning(
            'Certificate preview uses legacy Application fallback for order(s): %s',
            numbers)
    available = _orders()
    # A total the user typed survives a validation bounce; otherwise the
    # stored one (editing) or nothing (the form mirrors the computed sum).
    typed_total = None
    if request.method == 'POST':
        typed_total = _parse_total_length(request.form.get('total_length_m'))
    if typed_total is None and cert is not None:
        typed_total = cert.total_length_m
    can_reissue = has_permission(
        current_user.role, 'certificates', 'reissue')
    reissue_candidate = None
    raw_reissue_id = request.values.get('reissue_of_id', type=int)
    if can_reissue and cert is None and raw_reissue_id:
        reissue_candidate = Certificate.query.filter_by(
            id=raw_reissue_id, cert_type=cert_type,
            reissue_of_id=None).first()

    reissue_query = (request.values.get('reissue_query') or '').strip()
    if reissue_query:
        reissue_options = (Certificate.query
                           .filter(Certificate.cert_type == cert_type)
                           .filter(Certificate.reissue_of_id.is_(None))
                           .filter(Certificate.certificate_no.contains(
                               reissue_query, autoescape=True))
                           .order_by(Certificate.certificate_no)
                           .limit(50).all())
    elif reissue_candidate is not None:
        reissue_options = [reissue_candidate]
    else:
        reissue_options = []

    order_token = ','.join(str(order.id) for order in orders) or '0'
    if cert is None:
        selection_state_action = url_for(
            'certificates.new_selection_state', cert_type=cert_type,
            primary_id=primary_id or 0,
            reissue_id=reissue_candidate.id if reissue_candidate else 0,
            order_token=order_token)
    else:
        selection_state_action = url_for(
            'certificates.edit_selection_state', id=cert.id,
            primary_id=primary_id or 0, order_token=order_token)

    metadata_availability = _pipe_availability(
        pipes, cert_type, current_cert=cert)
    availability = (_pipe_availability(
        pipes, cert_type, current_cert=reissue_candidate)
        if reissue_candidate is not None else metadata_availability)
    selection_rows = [
        _selection_pipe_json(
            pipe, availability[pipe.id], selected=pipe.id in selected_ids)
        for pipe in pipes
    ]
    duplicate_certificates = list({
        state['certificate'].id: state['certificate']
        for state in metadata_availability.values() if state['certificate']
    }.values())
    return render_template(
        'certificates/form.html', certificate=cert, orders=orders,
        cert_type=cert_type, available_orders=available,
        customers=_customers(available), primary_id=primary_id,
        typed_total=typed_total,
        pipes=pipes, lengths=pipe_lengths(pipes) if pipes else {},
        selected_ids=selected_ids,
        fallback_order_numbers=[order.order_number for order in legacy_orders],
        can_reissue=can_reissue,
        reissue_candidate=reissue_candidate,
        reissue_options=reissue_options,
        reissue_query=reissue_query,
        selection_state_action=selection_state_action,
        pipe_availability=availability,
        selection_rows=selection_rows,
        duplicate_certificates=duplicate_certificates)


@certificates_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@requires_permission('certificates', 'edit')
def edit(id):
    if request.method == 'POST':
        _begin_issuance_write()
    cert = Certificate.query.get_or_404(id)
    try:
        requested = _order_ids_from_request(request.values)
    except ValueError:
        requested = []
        flash('The submitted production-order selection is invalid.', 'danger')
    orders = _load_orders(requested) if requested else cert.orders

    # Editing keeps the stored primary unless the form says otherwise.
    primary_id = (request.values.get('primary_order_id', type=int)
                  or cert.production_order_id)
    if primary_id not in {o.id for o in orders}:
        primary_id = _primary_order_id(request.values, orders)

    if request.method == 'POST':
        pipes, selection_error = _validate_posted_pipes(request.form, orders)
        selected_ids = {pipe.id for pipe in pipes}
        if selection_error:
            db.session.rollback()
            flash(selection_error, 'danger')
            return _render_form(
                cert, orders, cert.cert_type, primary_id,
                {int(value) for value in request.form.getlist('pipe_ids')
                 if str(value).strip().isdigit()})

        loaded_order_ids = {order.id for order in orders}
        for pipe in pipes:
            if pipe.production_order_id not in loaded_order_ids:
                orders.append(pipe.production_order)
                loaded_order_ids.add(pipe.production_order_id)

        selected_orders = []
        seen_order_ids = set()
        for pipe in pipes:
            if pipe.production_order_id not in seen_order_ids:
                seen_order_ids.add(pipe.production_order_id)
                selected_orders.append(pipe.production_order)
        conflicts = compatibility_conflicts(selected_orders)
        duplicates = _same_type_certificates(
            pipes, cert.cert_type,
            exclude_id=cert.reissue_of_id or cert.id)
        if conflicts or duplicates:
            db.session.rollback()
            if conflicts:
                flash('Incompatible production-order specifications: '
                      + '; '.join(conflicts), 'danger')
            if duplicates:
                flash('Selected pipe(s) already belong to saved certificate(s) '
                      'of this type: '
                      + ', '.join(item.certificate_no for item in duplicates)
                      + '. Reprint or edit the original.', 'danger')
            return _render_form(
                cert, orders, cert.cert_type, primary_id, selected_ids)

        pipes = _apply_form(
            cert, request.form, orders, primary_id, selected_pipes=pipes)
        mixed = (_mixed_dn_class(pipes)
                 if pipes and cert.cert_type == Certificate.MTC else None)
        if not pipes:
            # _apply_form already emptied cert.pipes on a session-attached row;
            # drop that before rendering so nothing can flush it later.
            db.session.rollback()
            flash('Select at least one pipe for the certificate.', 'warning')
        elif mixed:
            db.session.rollback()
            flash('An MTC covers one DN and one class; these pipes span '
                  f'{mixed}. Untick the odd ones out.', 'danger')
        else:
            if len(_customer_names(cert.orders)) > 1:
                flash('These pipes span more than one customer.', 'warning')
            _replace_ordinary_claims(cert, pipes)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash('Selected pipe(s) were claimed by another saved '
                      'certificate while this form was open. Reprint or edit '
                      'the original.', 'danger')
                return _render_form(
                    cert, orders, cert.cert_type, primary_id, selected_ids)
            flash(f'Certificate {cert.certificate_no} updated.', 'success')
            return redirect(url_for('certificates.print_view', id=cert.id))

    return _render_form(cert, orders, cert.cert_type, primary_id,
                        {p.id for p in cert.pipes})


@certificates_bp.route('/<int:id>/print')
@login_required
@requires_permission('certificates', 'print')
def print_view(id):
    cert = Certificate.query.get_or_404(id)
    order = cert.production_order
    rows = group_pipes(cert.pipes, order)
    legacy_orders = fallback_orders(cert.orders)
    context = {
        'cert': cert,
        'order': order,
        'rows': rows,
        'customer': customer_name(order),
        'description': item_description(order),
        'total_qty': cert.quantity,
        # The confirmed metres when the user set them, else the rows' sum.
        'total_length': cert.effective_total_length(rows),
        'standards_text': certificate_standards(
            cert, separator=' / ', fallback='ISO 2531 / EN 545'),
        'application_fallback_warning': (
            'Legacy Application fallback used for order(s): '
            + ', '.join(order.order_number for order in legacy_orders)
            if legacy_orders else None),
    }
    if cert.cert_type == Certificate.WORK_TEST:
        context.update({
            'elements': chemical_limits(),
            'dn_range': dn_range_label(cert.pipes, order),
            'sample': mechanical_sample(cert.pipes),
            'standards_text': certificate_standards(
                cert, separator=' & ', fallback=WORK_TEST_STANDARD_FALLBACK),
            'coating_text': external_coating_statement(cert),
        })
    elif cert.cert_type == Certificate.MTC:
        # The MTC states one delivery, not a line per length: total metres and
        # every class that went into it.
        classes = [r['pipe_class'] for r in rows if r['pipe_class']]
        context.update({
            'mtc_elements': MTC_ELEMENTS,
            'chem_summary': mtc_chemical_summary(cert.pipes, cert.values_mode),
            'chem_source': MTC_CHEMICAL_STANDARD_SOURCE,
            'mech_standards': MTC_MECHANICAL_STANDARDS,
            'heats': mtc_heats(cert.pipes, cert.values_mode),
            'batches': batch_numbers(cert.pipes),
            'dn_range': dn_range_label(cert.pipes, order),
            'total_metres': context['total_length'],
            'class_label': ', '.join(dict.fromkeys(classes)),
        })
    return render_template(cert.template, **context)


@certificates_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@requires_permission('certificates', 'delete')
def delete(id):
    cert = Certificate.query.get_or_404(id)
    if cert.reissues:
        numbers = ', '.join(reissue.certificate_no for reissue in cert.reissues)
        flash(
            f'Certificate {cert.certificate_no} cannot be deleted because '
            f'reissue certificate(s) preserve its audit trail: {numbers}.',
            'danger',
        )
        return redirect(url_for('certificates.index'))
    number = cert.certificate_no
    db.session.delete(cert)
    db.session.commit()
    flash(f'Certificate {number} deleted.', 'success')
    return redirect(url_for('certificates.index'))


def _orders():
    # The picker names each order's customer and filters by it, so bring the
    # customer along rather than one query per option.
    return (ProductionOrder.query
            .options(joinedload(ProductionOrder.customer_ref))
            .order_by(ProductionOrder.id.desc())
            .limit(200)
            .all())


def _customers(orders):
    """The customers behind the offered orders, for the picker's filter.

    Orders carry either a linked customer or a legacy free-text name; both
    kinds get a filter entry, keyed so the template can match options to it
    (``c<id>`` for a linked customer, the name itself for a legacy one).
    """
    seen = {}
    for o in orders:
        key = _customer_key(o)
        if key and key not in seen:
            seen[key] = customer_name(o)
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def _customer_key(order):
    linked = getattr(order, 'customer_ref', None)
    if linked is not None:
        return f'c{linked.id}'
    name = (order.customer_name or '').strip()
    return name or None


certificates_bp.add_app_template_global(_customer_key, name='customer_key')


def _issue(cert, attempts=5):
    """Allocate the serial, claims and certificate in the locked transaction."""
    _lock_number_namespace(cert.cert_type, cert.issue_date)
    pipes = list(cert.pipes)
    _replace_ordinary_claims(cert, pipes)
    for _ in range(attempts):
        cert.certificate_no = Certificate.generate_certificate_no(
            cert.cert_type, when=cert.issue_date)
        try:
            # A savepoint handles a stale serial without abandoning the outer
            # transaction or its pipe/spec locks. PostgreSQL's advisory lock
            # and SQLite's IMMEDIATE transaction prevent real allocator races;
            # the unique number remains the final backstop.
            with db.session.begin_nested():
                db.session.add(cert)
                db.session.flush()
            db.session.commit()
            return True
        except IntegrityError:
            # begin_nested rolled back only this insert attempt. Rebuild claim
            # objects because SQLAlchemy detaches failed pending children.
            cert.pipe_claims = []
            _replace_ordinary_claims(cert, pipes)
    db.session.rollback()
    return False
