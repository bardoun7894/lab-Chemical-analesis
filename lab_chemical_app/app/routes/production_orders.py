"""
Production Orders Routes - امر انتاج / امر شغل
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models.production_order import ProductionOrder
from app.models.pipe import Pipe
from app.models.chemical import ChemicalAnalysis

production_orders_bp = Blueprint('production_orders', __name__)


@production_orders_bp.route('/')
@login_required
def index():
    """List all production orders"""
    # Get filters
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)

    # Build query
    query = ProductionOrder.query

    if status:
        query = query.filter(ProductionOrder.status == status)

    if search:
        query = query.filter(
            db.or_(
                ProductionOrder.order_number.ilike(f'%{search}%'),
                ProductionOrder.customer_name.ilike(f'%{search}%')
            )
        )

    # Order by date descending
    query = query.order_by(ProductionOrder.order_date.desc())

    # Paginate
    orders = query.paginate(page=page, per_page=20, error_out=False)

    return render_template('production_orders/list.html',
                          orders=orders,
                          status=status,
                          search=search)


@production_orders_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    """Add new production order"""
    if request.method == 'POST':
        order = ProductionOrder()

        # Auto-generate order number if not provided
        order_number = request.form.get('order_number', '').strip()
        if not order_number:
            order_number = order.generate_order_number()

        order.order_number = order_number
        order.customer_name = request.form.get('customer_name', '')
        order.customer_code = request.form.get('customer_code', '')
        order.target_quantity = int(request.form.get('target_quantity', 0))
        order.sales_number = request.form.get('sales_number', '')
        order.diameter = int(request.form.get('diameter', 0)) if request.form.get('diameter') else None
        order.pipe_class = request.form.get('pipe_class', '')

        # Product Specifications
        order.product_code = request.form.get('product_code', '')
        order.product_description = request.form.get('product_description', '')
        order.product_weight = float(request.form.get('product_weight')) if request.form.get('product_weight') else None
        order.product_length = float(request.form.get('product_length')) if request.form.get('product_length') else None

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
        order.created_by_id = current_user.id

        try:
            db.session.add(order)
            db.session.commit()
            flash('تم إنشاء أمر الإنتاج بنجاح', 'success')
            return redirect(url_for('production_orders.view', id=order.id))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    return render_template('production_orders/form.html', order=None)


@production_orders_bp.route('/<int:id>')
@login_required
def view(id):
    """View production order details"""
    order = ProductionOrder.query.get_or_404(id)

    # Get pipes for this order
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    return render_template('production_orders/detail.html',
                          order=order,
                          pipes=pipes)


@production_orders_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    """Edit production order"""
    order = ProductionOrder.query.get_or_404(id)

    if request.method == 'POST':
        order.order_number = request.form.get('order_number', order.order_number)
        order.customer_name = request.form.get('customer_name', '')
        order.customer_code = request.form.get('customer_code', '')
        order.target_quantity = int(request.form.get('target_quantity', 0))
        order.sales_number = request.form.get('sales_number', '')
        order.diameter = int(request.form.get('diameter', 0)) if request.form.get('diameter') else None
        order.pipe_class = request.form.get('pipe_class', '')

        # Product Specifications
        order.product_code = request.form.get('product_code', '')
        order.product_description = request.form.get('product_description', '')
        order.product_weight = float(request.form.get('product_weight')) if request.form.get('product_weight') else None
        order.product_length = float(request.form.get('product_length')) if request.form.get('product_length') else None

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

        try:
            db.session.commit()
            flash('تم تحديث أمر الإنتاج بنجاح', 'success')
            return redirect(url_for('production_orders.view', id=order.id))
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {str(e)}', 'danger')

    return render_template('production_orders/form.html', order=order)


@production_orders_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    """Delete production order"""
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


@production_orders_bp.route('/api/search')
@login_required
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


@production_orders_bp.route('/api/<int:id>/stats')
@login_required
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
def progress(id):
    """Production progress tracking page"""
    order = ProductionOrder.query.get_or_404(id)

    # Get all pipes with their stages
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    # Get chemical analyses linked to this order
    chemical_analyses = ChemicalAnalysis.query.filter_by(production_order_id=order.id).all()

    # Get all ladle_ids from pipes for getting other chemical analyses
    ladle_ids = [p.ladle_id for p in pipes if p.ladle_id]
    chemical_by_ladle = {c.ladle_id: c for c in ChemicalAnalysis.query.filter(
        ChemicalAnalysis.ladle_id.in_(ladle_ids)
    ).all()} if ladle_ids else {}

    # Build progress data
    stages_data = {stage: {'accept': 0, 'reject': 0, 'pending': 0} for stage in Pipe.STAGES}

    for pipe in pipes:
        for stage_name in Pipe.STAGES:
            stage = pipe.get_stage(stage_name)
            if stage and stage.decision:
                if stage.decision == 'ACCEPT':
                    stages_data[stage_name]['accept'] += 1
                elif stage.decision == 'REJECT':
                    stages_data[stage_name]['reject'] += 1
                else:
                    stages_data[stage_name]['pending'] += 1
            else:
                stages_data[stage_name]['pending'] += 1

    return render_template('production_orders/progress.html',
                          order=order,
                          pipes=pipes,
                          chemical_analyses=chemical_analyses,
                          chemical_by_ladle=chemical_by_ladle,
                          stages_data=stages_data,
                          stages=Pipe.STAGES)


@production_orders_bp.route('/<int:id>/print-stickers')
@login_required
def print_stickers(id):
    """Print stickers for all pipes in production order"""
    order = ProductionOrder.query.get_or_404(id)
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    return render_template('production_orders/print_stickers.html',
                          order=order,
                          pipes=pipes)


@production_orders_bp.route('/<int:id>/generate-batch-stickers')
@login_required
def generate_batch_stickers(id):
    """Generate PDF with all stickers for production order"""
    from io import BytesIO
    from flask import send_file
    from app.services.qr_service import create_batch_stickers

    order = ProductionOrder.query.get_or_404(id)
    size = request.args.get('size', 'medium')

    # Get all pipes for this order
    pipes = Pipe.query.filter_by(production_order_id=order.id).order_by(Pipe.no_code).all()

    if not pipes:
        flash('لا توجد أنابيب في هذا الأمر', 'warning')
        return redirect(url_for('production_orders.view', id=order.id))

    # Build pipe info for stickers
    pipes_info = []
    for pipe in pipes:
        # Get chemical analysis
        chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        decision = chem.decision if chem else 'N/A'

        # Get all stages info
        stages_summary = []
        for stage_name in pipe.STAGES:
            stage = pipe.get_stage(stage_name)
            if stage and stage.decision:
                stages_summary.append(f"{stage_name}:{stage.decision[:1]}")  # CCM:A, Zinc:A, etc.

        pipes_info.append({
            'no_code': pipe.no_code,
            'ladle_id': pipe.ladle_id,
            'diameter': pipe.diameter,
            'pipe_class': pipe.pipe_class,
            'production_date': pipe.production_date.isoformat() if pipe.production_date else '',
            'weight': float(pipe.actual_weight) if pipe.actual_weight else '',
            'decision': decision,
            'order_number': order.order_number,
            'customer': order.customer_name or '',
            'stages': '|'.join(stages_summary)
        })

    # Generate PDF with all stickers
    pdf_buffer = create_batch_stickers(pipes_info, size)

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'stickers_{order.order_number}.pdf'
    )
