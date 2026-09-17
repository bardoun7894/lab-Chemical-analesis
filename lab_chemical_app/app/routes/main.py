"""
Main Routes - Dashboard, Home, and Attachments
"""
import os
from flask import Blueprint, render_template, redirect, url_for, jsonify, request, send_file, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func
from datetime import date, timedelta
from app import db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.pipe import Pipe, PipeStage
from app.models.mechanical import MechanicalTest
from app.services.ai_service import generate_dashboard_summary
from app.services.permission_service import requires_permission

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Home page - redirect to dashboard if logged in, otherwise to login"""
    if current_user.is_authenticated:
        return render_template('dashboard.html', **get_dashboard_stats())
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
@requires_permission('main', 'dashboard')
def dashboard():
    """Main dashboard with summary statistics"""
    return render_template('dashboard.html', **get_dashboard_stats())


@main_bp.route('/api/ai-summary')
@login_required
@requires_permission('main', 'dashboard')
def api_ai_summary():
    """API endpoint for AI dashboard summary"""
    stats = get_dashboard_stats()['stats']
    result = generate_dashboard_summary(stats)
    return jsonify(result)


def get_dashboard_stats():
    """Get dashboard statistics"""
    today = date.today()
    week_ago = today - timedelta(days=7)

    # Today's stats
    today_analyses = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date == today
    ).count()

    today_pipes = Pipe.query.filter(
        Pipe.production_date == today
    ).count()

    today_tests = MechanicalTest.query.filter(
        MechanicalTest.test_date == today
    ).count()

    # This week stats
    week_analyses = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= week_ago
    ).count()

    week_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago
    ).count()

    # Defect stats (this week)
    week_defects = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= week_ago,
        ChemicalAnalysis.has_defect == True
    ).count()

    # Acceptance rate
    total_decisions = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= week_ago,
        ChemicalAnalysis.decision.isnot(None)
    ).count()

    accepted = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= week_ago,
        ChemicalAnalysis.decision == 'ACCEPT'
    ).count()

    acceptance_rate = (accepted / total_decisions * 100) if total_decisions > 0 else 0

    # Recent analyses
    recent_analyses = ChemicalAnalysis.query.order_by(
        ChemicalAnalysis.created_at.desc()
    ).limit(5).all()

    # Recent pipes
    recent_pipes = Pipe.query.order_by(
        Pipe.created_at.desc()
    ).limit(5).all()

    # Furnace stats
    furnace_stats = db.session.query(
        Furnace.furnace_code,
        func.count(ChemicalAnalysis.id).label('count')
    ).join(ChemicalAnalysis).filter(
        ChemicalAnalysis.test_date >= week_ago
    ).group_by(Furnace.furnace_code).all()

    return {
        'stats': {
            'chem_today': today_analyses,
            'pipes_today': today_pipes,
            'mech_today': today_tests,
            'defects_week': week_defects,
            'acceptance_rate': round(acceptance_rate, 1)
        },
        'today': today,
        'week_ago': week_ago,
        'recent_analyses': recent_analyses,
        'recent_pipes': recent_pipes,
        'furnace_stats': furnace_stats
    }


# ============================================================================
# Attachment Upload/Download Routes
# ============================================================================

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@main_bp.route('/attachments/upload', methods=['POST'])
@login_required
@requires_permission('main', 'attachments')
def upload_attachment():
    """Upload an attachment for any record"""
    from app.models.attachment import Attachment

    if not current_user.can_edit:
        flash('You do not have permission to upload attachments.', 'error')
        return redirect(request.form.get('redirect_url', '/'))

    table_name = request.form.get('table_name')
    record_id = request.form.get('record_id', type=int)
    redirect_url = request.form.get('redirect_url', '/')

    if not table_name or not record_id:
        flash('Invalid attachment parameters', 'error')
        return redirect(redirect_url)

    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(redirect_url)

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(redirect_url)

    if not allowed_file(file.filename):
        flash('File type not allowed', 'error')
        return redirect(redirect_url)

    # Create upload directory
    upload_dir = os.path.join(UPLOAD_FOLDER, table_name)
    os.makedirs(upload_dir, exist_ok=True)

    # Save file
    filename = secure_filename(file.filename)
    # Add timestamp to prevent conflicts
    import time
    timestamp = int(time.time())
    safe_filename = f'{timestamp}_{filename}'
    filepath = os.path.join(upload_dir, safe_filename)
    file.save(filepath)

    # Get file size
    file_size = os.path.getsize(filepath)

    # Create attachment record
    attachment = Attachment(
        table_name=table_name,
        record_id=record_id,
        filename=filename,
        filepath=os.path.join('uploads', table_name, safe_filename),
        file_size=file_size,
        mime_type=file.content_type,
        description=request.form.get('description', ''),
        uploaded_by_id=current_user.id
    )
    db.session.add(attachment)
    db.session.commit()

    flash('File uploaded successfully', 'success')
    return redirect(redirect_url)


@main_bp.route('/attachments/<int:id>/download')
@login_required
@requires_permission('main', 'attachments')
def download_attachment(id):
    """Download an attachment"""
    from app.models.attachment import Attachment

    attachment = Attachment.query.get_or_404(id)
    full_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'static', attachment.filepath
    )

    if not os.path.exists(full_path):
        flash('File not found', 'error')
        return redirect(url_for('main.index'))

    return send_file(full_path, as_attachment=True, download_name=attachment.filename)


@main_bp.route('/attachments/<int:id>/delete', methods=['POST'])
@login_required
@requires_permission('main', 'attachments')
def delete_attachment(id):
    """Delete an attachment"""
    from app.models.attachment import Attachment

    if not current_user.is_supervisor:
        flash('Only supervisors or admins can delete attachments.', 'error')
        return redirect(request.form.get('redirect_url', '/'))

    attachment = Attachment.query.get_or_404(id)
    redirect_url = request.form.get('redirect_url', '/')

    # Delete file
    full_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'static', attachment.filepath
    )
    if os.path.exists(full_path):
        os.remove(full_path)

    db.session.delete(attachment)
    db.session.commit()

    flash('Attachment deleted', 'success')
    return redirect(redirect_url)
