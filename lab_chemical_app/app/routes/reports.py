"""
Reports Routes
"""
from flask import Blueprint, render_template, request, send_file, make_response
from flask_login import login_required
from datetime import date, timedelta
from io import BytesIO
from app import db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.pipe import Pipe, PipeStage
from app.models.mechanical import MechanicalTest

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/')
@login_required
def index():
    """Reports dashboard"""
    return render_template('reports/index.html')


@reports_bp.route('/daily-production')
@login_required
def daily_production():
    """Daily production report"""
    report_date = request.args.get('date', date.today().isoformat())
    report_date = date.fromisoformat(report_date)

    # Get pipes for this date
    pipes = Pipe.query.filter_by(production_date=report_date)\
        .order_by(Pipe.shift, Pipe.no_code).all()

    # Group by shift
    by_shift = {1: [], 2: [], 3: []}
    for pipe in pipes:
        shift = pipe.shift or 1
        if shift in by_shift:
            by_shift[shift].append(pipe)

    # Summary stats
    total = len(pipes)
    by_diameter = {}
    for pipe in pipes:
        dn = pipe.diameter or 'Unknown'
        by_diameter[dn] = by_diameter.get(dn, 0) + 1

    return render_template('reports/daily_production.html',
                          report_date=report_date,
                          pipes=pipes,
                          by_shift=by_shift,
                          total=total,
                          by_diameter=by_diameter)


@reports_bp.route('/chemical-analysis')
@login_required
def chemical_analysis():
    """Chemical analysis report"""
    date_from = request.args.get('date_from', (date.today() - timedelta(days=7)).isoformat())
    date_to = request.args.get('date_to', date.today().isoformat())
    furnace_id = request.args.get('furnace_id', type=int)

    query = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to
    )

    if furnace_id:
        query = query.filter_by(furnace_id=furnace_id)

    analyses = query.order_by(ChemicalAnalysis.test_date.desc()).all()
    furnaces = Furnace.query.filter_by(is_active=True).all()

    # Stats
    total = len(analyses)
    accepted = sum(1 for a in analyses if a.decision == 'ACCEPT')
    rejected = sum(1 for a in analyses if a.decision == 'REJECT')
    defects = sum(1 for a in analyses if a.has_defect)

    return render_template('reports/chemical_analysis.html',
                          analyses=analyses,
                          furnaces=furnaces,
                          date_from=date_from,
                          date_to=date_to,
                          selected_furnace=furnace_id,
                          stats={
                              'total': total,
                              'accepted': accepted,
                              'rejected': rejected,
                              'defects': defects,
                              'rate': round(accepted/total*100, 1) if total > 0 else 0
                          })


@reports_bp.route('/defect-summary')
@login_required
def defect_summary():
    """Defect summary report"""
    date_from = request.args.get('date_from', (date.today() - timedelta(days=30)).isoformat())
    date_to = request.args.get('date_to', date.today().isoformat())

    # Chemical analysis defects
    chem_defects = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to,
        ChemicalAnalysis.has_defect == True
    ).all()

    # Stage defects
    stage_defects = db.session.query(PipeStage)\
        .join(Pipe)\
        .filter(
            Pipe.production_date >= date_from,
            Pipe.production_date <= date_to,
            PipeStage.has_defect == True
        ).all()

    # Group by stage
    defects_by_stage = {}
    for stage in stage_defects:
        name = stage.stage_name
        defects_by_stage[name] = defects_by_stage.get(name, 0) + 1

    return render_template('reports/defect_summary.html',
                          chem_defects=chem_defects,
                          stage_defects=stage_defects,
                          defects_by_stage=defects_by_stage,
                          date_from=date_from,
                          date_to=date_to)


@reports_bp.route('/export/chemical-pdf')
@login_required
def export_chemical_pdf():
    """Export chemical analysis report as PDF"""
    date_from = request.args.get('date_from', (date.today() - timedelta(days=7)).isoformat())
    date_to = request.args.get('date_to', date.today().isoformat())

    analyses = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to
    ).order_by(ChemicalAnalysis.test_date.desc()).all()

    # Generate PDF using ReportLab
    from app.services.report_service import generate_chemical_pdf
    pdf_buffer = generate_chemical_pdf(analyses, date_from, date_to)

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'chemical_report_{date_from}_to_{date_to}.pdf'
    )


@reports_bp.route('/export/chemical-excel')
@login_required
def export_chemical_excel():
    """Export chemical analysis report as Excel"""
    date_from = request.args.get('date_from', (date.today() - timedelta(days=7)).isoformat())
    date_to = request.args.get('date_to', date.today().isoformat())

    analyses = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.test_date >= date_from,
        ChemicalAnalysis.test_date <= date_to
    ).order_by(ChemicalAnalysis.test_date.desc()).all()

    # Generate Excel using xlsxwriter
    from app.services.report_service import generate_chemical_excel
    excel_buffer = generate_chemical_excel(analyses, date_from, date_to)

    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'chemical_report_{date_from}_to_{date_to}.xlsx'
    )
