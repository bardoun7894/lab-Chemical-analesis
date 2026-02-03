"""
Main Routes - Dashboard and Home
"""
from flask import Blueprint, render_template, redirect, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from datetime import date, timedelta
from app import db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.pipe import Pipe, PipeStage
from app.models.mechanical import MechanicalTest
from app.services.ai_service import generate_dashboard_summary

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Home page - redirect to dashboard if logged in, otherwise to login"""
    if current_user.is_authenticated:
        return render_template('dashboard.html', **get_dashboard_stats())
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard with summary statistics"""
    return render_template('dashboard.html', **get_dashboard_stats())


@main_bp.route('/api/ai-summary')
@login_required
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
        'recent_analyses': recent_analyses,
        'recent_pipes': recent_pipes,
        'furnace_stats': furnace_stats
    }
