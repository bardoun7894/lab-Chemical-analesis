"""
Production Orders Routes - امر انتاج / امر شغل
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, current_app)
from flask_login import login_required, current_user
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.production_order import ProductionOrder
from app.services import application_spec_service
from app.models.pipe import Pipe
from app.models.chemical import ChemicalAnalysis
from app.models.product import Product, Customer
from app.services.permission_service import requires_permission

production_orders_bp = Blueprint('production_orders', __name__)


def _orders_query(args):
    """Build the filtered ProductionOrder query shared by index() and export."""
    query = ProductionOrder.query

    status = args.get('status', '')
    search = args.get('search', '')
    date_from = args.get('date_from', '')
    date_to = args.get('date_to', '')
    customer = args.get('customer', '')
    diameter = args.get('diameter', '')
    pipe_class = args.get('pipe_class', '')
    priority = args.get('priority', '')
    sales_number = args.get('sales_number', '')
    order_number = args.get('order_number', '')
    product_id = args.get('product_id', '')

    if status:
        query = query.filter(ProductionOrder.status == status)
    if search:
        query = query.filter(
            db.or_(
                ProductionOrder.order_number.ilike(f'%{search}%'),
                ProductionOrder.customer_name.ilike(f'%{search}%')
            )
        )
    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            query = query.filter(ProductionOrder.order_date >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
            query = query.filter(ProductionOrder.order_date <= dt)
        except ValueError:
            pass
    if customer:
        query = query.filter(
            db.or_(
                ProductionOrder.customer_name.ilike(f'%{customer}%'),
                ProductionOrder.customer_code.ilike(f'%{customer}%')
            )
        )
    if diameter:
        dn = _parse_diameter(diameter)
        if dn is not None:
            query = query.filter(ProductionOrder.diameter == dn)
    if pipe_class:
        query = query.filter(ProductionOrder.pipe_class == pipe_class)
    if priority:
        query = query.filter(ProductionOrder.priority == priority)
    if sales_number:
        query = query.filter(ProductionOrder.sales_number.ilike(f'%{sales_number}%'))
    if order_number:
        query = query.filter(ProductionOrder.order_number.ilike(f'%{order_number}%'))
    if product_id:
        pid = _parse_int(product_id, 0)
        if pid:
            query = query.filter(ProductionOrder.product_id == pid)

    return query.order_by(ProductionOrder.order_date.desc())


@production_orders_bp.route('/export.xlsx')
@login_required
@requires_permission('orders', 'list')
def export_excel():
    """Export/print filtered production orders. Honours cols=/ids=/format=print."""
    from app.services import export_service, table_registry
    query = export_service.filter_ids(_orders_query(request.args),
                                      ProductionOrder.id, request.args.get('ids'))
    rows = query.all()
    selected = export_service.resolve_columns(
        table_registry.order_columns(), request.args.get('cols'))
    if request.args.get('format') == 'print':
        return export_service.build_print('Production Orders', selected, rows)
    return export_service.build_xlsx('Orders', selected, rows, 'production_orders.xlsx')


@production_orders_bp.route('/')
@login_required
@requires_permission('orders', 'list')
def index():
    """List all production orders"""
    # Get filters (kept for template context)
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    customer = request.args.get('customer', '')
    diameter = request.args.get('diameter', '')
    pipe_class = request.args.get('pipe_class', '')
    priority = request.args.get('priority', '')
    sales_number = request.args.get('sales_number', '')
    order_number = request.args.get('order_number', '')
    product_id = request.args.get('product_id', '')
    page = request.args.get('page', 1, type=int)

    query = _orders_query(request.args)

    # Paginate
    orders = query.paginate(page=page, per_page=20, error_out=False)

    # Distinct values for select filters
    diameter_options = [
        d[0] for d in db.session.query(ProductionOrder.diameter)
        .filter(ProductionOrder.diameter.isnot(None))
        .distinct().order_by(ProductionOrder.diameter).all()
    ]
    pipe_class_options = [
        c[0] for c in db.session.query(ProductionOrder.pipe_class)
        .filter(ProductionOrder.pipe_class.isnot(None),
                ProductionOrder.pipe_class != '')
        .distinct().order_by(ProductionOrder.pipe_class).all()
    ]
    product_options = Product.query.filter_by(is_active=True).order_by(Product.product_code).all()

    from app.services import export_service, table_registry
    return render_template('production_orders/list.html',
                          picker_columns=export_service.picker_meta(table_registry.order_columns()),
                          orders=orders,
                          status=status,
                          search=search,
                          date_from=date_from,
                          date_to=date_to,
                          customer=customer,
                          diameter=diameter,
                          pipe_class=pipe_class,
                          priority=priority,
                          sales_number=sales_number,
                          order_number=order_number,
                          product_id=product_id,
                          diameter_options=diameter_options,
                          pipe_class_options=pipe_class_options,
                          product_options=product_options)


def _parse_diameter(raw):
    """Parse diameter value — accepts int, 'DN300', 'P10', '300', etc.
    Extracts digits and returns int, or None if nothing valid."""
    if raw is None or raw == '':
        return None
    if isinstance(raw, int):
        return raw
    import re
    digits = re.sub(r'[^0-9]', '', str(raw))
    try:
        return int(digits) if digits else None
    except (TypeError, ValueError):
        return None


def _parse_int(raw, default=0):
    """Parse int safely, returning default on failure."""
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_float(raw):
    """Parse float safely, returning None on failure."""
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# The Application block is shared with the Product now — same constants, same
# input names, same parser. Re-exported under the names the order form and
# detail templates are handed.
APPLICATION_STANDARDS = application_spec_service.APPLICATION_STANDARDS
APPLICATION_LAYERS = application_spec_service.APPLICATION_LAYERS
APPLICATION_FIELDS = application_spec_service.APPLICATION_FIELDS


def _read_application_profile():
    """Read the Application block from an order post."""
    return application_spec_service.read_application_profile(request.form)


def _order_number_taken(order_number, exclude_id=None):
    """True when another order already carries this number."""
    if not order_number:
        return False
    q = ProductionOrder.query.filter(ProductionOrder.order_number == order_number)
    if exclude_id is not None:
        q = q.filter(ProductionOrder.id != exclude_id)
    return db.session.query(q.exists()).scalar()


def _duplicate_number_message(order_number):
    """The message the user sees instead of a raw unique-constraint traceback."""
    return (f'رقم أمر الإنتاج "{order_number}" مستخدم بالفعل. '
            f'اختر رقماً آخر أو اترك الحقل فارغاً ليُولَّد تلقائياً.')


@production_orders_bp.route('/add', methods=['GET', 'POST'])
@login_required
@requires_permission('orders', 'add')
def add():
    """Add new production order"""
    if not current_user.is_supervisor:
        flash('Only supervisors and admins can create production orders.', 'error')
        return redirect(url_for('production_orders.index'))

    order = None
    if request.method == 'POST':
      try:
        order = ProductionOrder()

        # Auto-generate order number if not provided
        order_number = request.form.get('order_number', '').strip()
        if not order_number:
            order_number = order.generate_order_number()

        order.order_number = order_number
        order.customer_name = request.form.get('customer_name', '')
        order.customer_code = request.form.get('customer_code', '')
        order.target_quantity = _parse_int(request.form.get('target_quantity'), 0)
        order.sales_number = request.form.get('sales_number', '')
        order.diameter = _parse_diameter(request.form.get('diameter'))
        order.pipe_class = request.form.get('pipe_class', '')

        # Product from configurator
        product_id = request.form.get('product_id', type=int)
        if product_id:
            order.product_id = product_id
            product = Product.query.get(product_id)
            if product:
                order.product_code = product.product_code
                order.product_description = product.description_en
                order.product_weight = product.weight_kg
                order.product_length = product.length_m
                if product.dn_param:
                    dn = product.dn_value
                    if dn is not None:
                        order.diameter = dn
                if product.class_param:
                    order.pipe_class = product.class_param.code
        else:
            order.product_code = request.form.get('product_code', '')
            order.product_description = request.form.get('product_description', '')
            order.product_weight = _parse_float(request.form.get('product_weight'))
            order.product_length = _parse_float(request.form.get('product_length'))

        # Customer from dropdown
        customer_id = request.form.get('customer_id', type=int)
        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                order.customer_name = customer.name_en
                order.customer_code = customer.code
                order.customer_id = customer_id

        # Dates
        order_date = request.form.get('order_date')
        if order_date:
            order.order_date = datetime.strptime(order_date, '%Y-%m-%d').date()
        else:
            order.order_date = datetime.utcnow().date()

        start_date = request.form.get('start_date')
        if start_date:
            order.start_date = datetime.strptime(start_date, '%Y-%m-%d').date()

        expected_end_date = request.form.get('expected_end_date')
        if expected_end_date:
            order.expected_end_date = datetime.strptime(expected_end_date, '%Y-%m-%d').date()

        order.status = request.form.get('status', 'pending')
        order.priority = request.form.get('priority', 'normal')
        order.notes = request.form.get('notes', '')
        order.specifications = request.form.get('specifications', '')

        application_profile, has_application = _read_application_profile()
        if has_application:
            order.application_profile = application_profile
        else:
            # Order creation paths that never render the block (OCR, API) still
            # get the product's spec. The order owns its own copy from here on,
            # so editing the product later leaves this order alone.
            seed = Product.query.get(order.product_id) if order.product_id else None
            if seed and seed.application_profile:
                order.application_profile = dict(seed.application_profile)

        order.created_by_id = current_user.id

        # Caught here rather than at the INSERT so the user gets a sentence
        # instead of a UniqueViolation traceback, and keeps what they typed.
        if _order_number_taken(order.order_number):
            flash(_duplicate_number_message(order.order_number), 'danger')
        else:
            try:
                db.session.add(order)
                db.session.commit()
                flash('تم إنشاء أمر الإنتاج بنجاح', 'success')
                return redirect(url_for('production_orders.view', id=order.id))
            except IntegrityError:
                # Lost the race against a concurrent create.
                db.session.rollback()
                flash(_duplicate_number_message(order.order_number), 'danger')
                order = None
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Failed to create production order')
                flash('تعذر حفظ أمر الإنتاج. حاول مرة أخرى.', 'danger')
                order = None
      except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to process production order form')
        flash('تعذر قراءة بيانات النموذج. تحقق من الحقول وحاول مرة أخرى.', 'danger')
        order = None

    products = Product.query.filter_by(is_active=True).order_by(Product.product_code).all()
    customers = Customer.query.filter_by(is_active=True).order_by(Customer.name_en).all()
    return render_template('production_orders/form.html', order=order, products=products, customers=customers,
                           application_standards=APPLICATION_STANDARDS,
                           application_layers=APPLICATION_LAYERS)


@production_orders_bp.route('/<int:id>')
@login_required
@requires_permission('orders', 'list')
def view(id):
    """View production order details"""
    order = ProductionOrder.query.get_or_404(id)

    # Get pipes for this order
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    return render_template('production_orders/detail.html',
                          order=order,
                          pipes=pipes,
                          application_standards=APPLICATION_STANDARDS,
                          application_layers=APPLICATION_LAYERS)


@production_orders_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@requires_permission('orders', 'edit')
def edit(id):
    """Edit production order"""
    order = ProductionOrder.query.get_or_404(id)

    if not current_user.is_supervisor:
        flash('Only supervisors and admins can edit production orders.', 'error')
        return redirect(url_for('production_orders.view', id=order.id))

    if request.method == 'POST':
      try:
        new_number = (request.form.get('order_number') or '').strip() or order.order_number
        if _order_number_taken(new_number, exclude_id=order.id):
            flash(_duplicate_number_message(new_number), 'danger')
            return render_template(
                'production_orders/form.html', order=order,
                products=Product.query.filter_by(is_active=True).order_by(Product.product_code).all(),
                customers=Customer.query.filter_by(is_active=True).order_by(Customer.name_en).all(),
                application_standards=APPLICATION_STANDARDS,
                application_layers=APPLICATION_LAYERS)
        order.order_number = new_number
        order.customer_name = request.form.get('customer_name', '')
        order.customer_code = request.form.get('customer_code', '')
        order.target_quantity = _parse_int(request.form.get('target_quantity'), 0)
        order.sales_number = request.form.get('sales_number', '')
        order.diameter = _parse_diameter(request.form.get('diameter'))
        order.pipe_class = request.form.get('pipe_class', '')

        # Product from configurator
        product_id = request.form.get('product_id', type=int)
        if product_id:
            order.product_id = product_id
            product = Product.query.get(product_id)
            if product:
                order.product_code = product.product_code
                order.product_description = product.description_en
                order.product_weight = product.weight_kg
                order.product_length = product.length_m
                if product.dn_param:
                    dn = product.dn_value
                    if dn is not None:
                        order.diameter = dn
                if product.class_param:
                    order.pipe_class = product.class_param.code
        else:
            order.product_code = request.form.get('product_code', '')
            order.product_description = request.form.get('product_description', '')
            order.product_weight = _parse_float(request.form.get('product_weight'))
            order.product_length = _parse_float(request.form.get('product_length'))

        # Customer from dropdown
        customer_id = request.form.get('customer_id', type=int)
        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                order.customer_name = customer.name_en
                order.customer_code = customer.code
                order.customer_id = customer_id

        # Dates
        order_date = request.form.get('order_date')
        if order_date:
            order.order_date = datetime.strptime(order_date, '%Y-%m-%d').date()

        start_date = request.form.get('start_date')
        if start_date:
            order.start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        else:
            order.start_date = None

        expected_end_date = request.form.get('expected_end_date')
        if expected_end_date:
            order.expected_end_date = datetime.strptime(expected_end_date, '%Y-%m-%d').date()
        else:
            order.expected_end_date = None

        actual_end_date = request.form.get('actual_end_date')
        if actual_end_date:
            order.actual_end_date = datetime.strptime(actual_end_date, '%Y-%m-%d').date()
        else:
            order.actual_end_date = None

        order.status = request.form.get('status', 'pending')
        order.priority = request.form.get('priority', 'normal')
        order.notes = request.form.get('notes', '')
        order.specifications = request.form.get('specifications', '')

        application_profile, has_application = _read_application_profile()
        if has_application:
            order.application_profile = application_profile

        order.modified_by_id = current_user.id

        try:
            db.session.commit()
            flash('تم تحديث أمر الإنتاج بنجاح', 'success')
            return redirect(url_for('production_orders.view', id=order.id))
        except IntegrityError:
            db.session.rollback()
            flash(_duplicate_number_message(new_number), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update production order %s', id)
            flash('تعذر حفظ التعديلات. حاول مرة أخرى.', 'danger')
      except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to process production order form')
        flash('تعذر قراءة بيانات النموذج. تحقق من الحقول وحاول مرة أخرى.', 'danger')

    products = Product.query.filter_by(is_active=True).order_by(Product.product_code).all()
    customers = Customer.query.filter_by(is_active=True).order_by(Customer.name_en).all()
    return render_template('production_orders/form.html', order=order, products=products, customers=customers,
                           application_standards=APPLICATION_STANDARDS,
                           application_layers=APPLICATION_LAYERS)


@production_orders_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@requires_permission('orders', 'delete')
def delete(id):
    """Delete production order"""
    if not current_user.is_supervisor:
        flash('Only supervisors and admins can delete production orders.', 'error')
        return redirect(url_for('production_orders.index'))

    order = ProductionOrder.query.get_or_404(id)

    # Check if there are pipes linked to this order
    if order.pipes.count() > 0:
        flash('لا يمكن حذف أمر الإنتاج لأنه يحتوي على أنابيب مرتبطة', 'danger')
        return redirect(url_for('production_orders.view', id=order.id))

    try:
        db.session.delete(order)
        db.session.commit()
        flash('تم حذف أمر الإنتاج بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'خطأ: {str(e)}', 'danger')

    return redirect(url_for('production_orders.index'))


@production_orders_bp.route('/api/ocr-extract', methods=['POST'])
@login_required
@requires_permission('orders', 'add')
def api_ocr_extract():
    """Extract production order data from a document image via Gemini Vision."""
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image file'}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({'success': False, 'error': 'Empty filename'}), 400
    try:
        image_bytes = file.read()
        from app.services.pipe_ocr_service import extract_order_from_image
        values = extract_order_from_image(image_bytes, file.filename)
        extracted = sum(1 for v in values.values() if v is not None)
        return jsonify({
            'success': True, 'values': values, 'extracted': extracted,
            'message': f'Extracted {extracted} fields. Please verify.',
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@production_orders_bp.route('/api/search')
@login_required
@requires_permission('orders', 'list')
def api_search():
    """API: Search production orders"""
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify({'orders': []})

    orders = ProductionOrder.query.filter(
        db.or_(
            ProductionOrder.order_number.ilike(f'%{query}%'),
            ProductionOrder.customer_name.ilike(f'%{query}%')
        )
    ).limit(10).all()

    result = []
    for order in orders:
        result.append({
            'id': order.id,
            'order_number': order.order_number,
            'customer_name': order.customer_name,
            'target_quantity': order.target_quantity,
            'produced_quantity': order.produced_quantity,
            'status': order.status
        })

    return jsonify({'orders': result})


@production_orders_bp.route('/api/<int:id>/product-details')
@login_required
@requires_permission('orders', 'list')
def api_product_details(id):
    """API: Return product data (diameter, class, etc.) for use by pipe registration form."""
    order = ProductionOrder.query.get_or_404(id)
    latest_ladles = [
        ca.ladle_id
        for ca in order.chemical_analyses.order_by(db.desc('id')).limit(10).all()
        if ca.ladle_id
    ]
    product = order.product
    return jsonify({
        'order_id': order.id,
        'order_number': order.order_number,
        'customer_name': order.customer_name,
        'diameter': order.diameter,
        'pipe_class': order.pipe_class,
        'product_code': order.product_code,
        'product_description': order.product_description,
        'product_weight': order.product_weight,
        'product_length': order.product_length,
        'zinc_type': product.zinc_type_param.name_en if product and product.zinc_type_param else None,
        'internal_finish': product.internal_finish_param.name_en if product and product.internal_finish_param else None,
        'external_finish': product.external_finish_param.name_en if product and product.external_finish_param else None,
        'intent_use': product.intent_use_param.name_en if product and product.intent_use_param else None,
        'ladles': latest_ladles,
        'target_quantity': order.target_quantity,
        'produced_quantity': order.produced_quantity,
    })


@production_orders_bp.route('/api/<int:id>/stats')
@login_required
@requires_permission('orders', 'list')
def api_stats(id):
    """API: Get production order statistics"""
    order = ProductionOrder.query.get_or_404(id)

    # Count by stage
    stage_stats = {}
    for stage_name in Pipe.STAGES:
        stage_stats[stage_name] = {
            'accept': 0,
            'reject': 0,
            'pending': 0
        }

    for pipe in order.pipes:
        for stage_name in Pipe.STAGES:
            stage = pipe.get_stage(stage_name)
            if stage:
                if stage.decision == 'ACCEPT':
                    stage_stats[stage_name]['accept'] += 1
                elif stage.decision == 'REJECT':
                    stage_stats[stage_name]['reject'] += 1
                else:
                    stage_stats[stage_name]['pending'] += 1
            else:
                stage_stats[stage_name]['pending'] += 1

    return jsonify({
        'order_number': order.order_number,
        'target_quantity': order.target_quantity,
        'produced_quantity': order.produced_quantity,
        'completed_quantity': order.completed_quantity,
        'rejected_quantity': order.rejected_quantity,
        'progress_percentage': order.progress_percentage,
        'stage_stats': stage_stats
    })


@production_orders_bp.route('/<int:id>/progress')
@login_required
@requires_permission('orders', 'progress')
def progress(id):
    """Production progress tracking page"""
    order = ProductionOrder.query.get_or_404(id)

    # Get all pipes with their stages
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    # Get all ladle_ids from pipes — pipes link to their analysis by ladle
    # (Pipe.ladle_id -> ChemicalAnalysis.ladle_id), so the order's analyses are
    # the analyses for those ladles, plus any directly linked via production_order_id.
    ladle_ids = [p.ladle_id for p in pipes if p.ladle_id]
    chemical_by_ladle = {c.ladle_id: c for c in ChemicalAnalysis.query.filter(
        ChemicalAnalysis.ladle_id.in_(ladle_ids)
    ).all()} if ladle_ids else {}

    # Panel list: union of directly-linked analyses and those matched by pipe ladle,
    # deduped by id. This is why the panel was always empty before — add() never set
    # production_order_id, so the direct-FK-only query returned nothing.
    direct_analyses = ChemicalAnalysis.query.filter_by(production_order_id=order.id).all()
    _seen = set()
    chemical_analyses = []
    for ca in list(chemical_by_ladle.values()) + direct_analyses:
        if ca.id not in _seen:
            _seen.add(ca.id)
            chemical_analyses.append(ca)

    # Build stage counters using the shared decision classifier
    from app.models.pipe import PipeStage
    stages_data = {stage: {'accept': 0, 'reject': 0, 'pending': 0} for stage in Pipe.STAGES}

    for pipe in pipes:
        for stage_name in Pipe.STAGES:
            stage = pipe.get_stage(stage_name)
            bucket = PipeStage.classify_decision(stage.decision if stage else None)
            stages_data[stage_name][bucket] += 1

    # Per-pipe stage bucket lookup for the template ({pipe_id: {stage_name: bucket}})
    pipe_stage_status = {}
    for pipe in pipes:
        row = {}
        for stage_name in Pipe.STAGES:
            stage = pipe.get_stage(stage_name)
            row[stage_name] = PipeStage.classify_decision(stage.decision if stage else None)
        pipe_stage_status[pipe.id] = row

    return render_template('production_orders/progress.html',
                          order=order,
                          pipes=pipes,
                          chemical_analyses=chemical_analyses,
                          chemical_by_ladle=chemical_by_ladle,
                          stages_data=stages_data,
                          pipe_stage_status=pipe_stage_status,
                          stages=Pipe.STAGES)


@production_orders_bp.route('/<int:id>/print-stickers')
@login_required
@requires_permission('orders', 'print_stickers')
def print_stickers(id):
    """Print stickers for all pipes in production order"""
    from app.routes.stickers import DEFAULT_STICKER_SIZE, get_sticker_sizes, wants_internal

    order = ProductionOrder.query.get_or_404(id)
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    return render_template('production_orders/print_stickers.html',
                          order=order,
                          pipes=pipes,
                          sizes=get_sticker_sizes(),
                          default_size=DEFAULT_STICKER_SIZE,
                          internal=wants_internal())


@production_orders_bp.route('/<int:id>/generate-batch-stickers')
@login_required
@requires_permission('orders', 'print_stickers')
def generate_batch_stickers(id):
    """Generate PDF with all stickers for production order"""
    from io import BytesIO
    from flask import send_file
    from app.routes.stickers import DEFAULT_STICKER_SIZE, render_batch, wants_internal
    from app.services.qr_service import create_batch_stickers

    order = ProductionOrder.query.get_or_404(id)
    # The same default as every other print path. This screen kept its own
    # literal 'medium' and so printed the short 100x60 label while the
    # stickers screen printed the 100x90 card — the same pipe, two sizes,
    # depending only on which button you reached for.
    size = request.args.get('size', DEFAULT_STICKER_SIZE)

    # Get all pipes for this order
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    if not pipes:
        flash('لا توجد أنابيب في هذا الأمر', 'warning')
        return redirect(url_for('production_orders.view', id=order.id))

    # Rendered by the real sticker renderer so a batch sheet is identical to
    # what the preview and the single-pipe print produce.
    pngs, width_mm, height_mm = render_batch(pipes, size,
                                             internal=wants_internal())
    pdf_buffer = create_batch_stickers(pngs, width_mm, height_mm)

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'stickers_{order.order_number}.pdf'
    )
