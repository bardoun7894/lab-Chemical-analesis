"""
Report Service - PDF and Excel Generation

Generates reports with Arabic support using ReportLab and xlsxwriter.
"""

from io import BytesIO
from datetime import date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

try:
    import xlsxwriter
    HAS_XLSXWRITER = True
except ImportError:
    HAS_XLSXWRITER = False

# Try to import Arabic support
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_ARABIC = True
except ImportError:
    HAS_ARABIC = False


def reshape_arabic(text):
    """Reshape Arabic text for proper display"""
    if not HAS_ARABIC or not text:
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except:
        return text


def generate_chemical_pdf(analyses, date_from, date_to):
    """
    Generate PDF report for chemical analyses.

    Args:
        analyses: List of ChemicalAnalysis objects
        date_from: Start date string
        date_to: End date string

    Returns:
        BytesIO buffer containing PDF
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1*cm,
        bottomMargin=1*cm
    )

    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=20,
        alignment=1  # Center
    )
    title = Paragraph(f"Chemical Analysis Report<br/>{date_from} to {date_to}", title_style)
    elements.append(title)
    elements.append(Spacer(1, 10*mm))

    # Summary
    total = len(analyses)
    accepted = sum(1 for a in analyses if a.decision == 'ACCEPT')
    rejected = sum(1 for a in analyses if a.decision == 'REJECT')

    summary_text = f"Total: {total} | Accepted: {accepted} | Rejected: {rejected} | Rate: {round(accepted/total*100, 1) if total > 0 else 0}%"
    summary = Paragraph(summary_text, styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 10*mm))

    # Table header
    headers = ['Date', 'Furnace', 'Ladle#', 'C', 'Si', 'Mg', 'Cu', 'Cr', 'S', 'Mn', 'P', 'CE', 'Decision']

    # Table data
    data = [headers]
    for a in analyses:
        row = [
            str(a.test_date) if a.test_date else '',
            a.furnace.furnace_code if a.furnace else '',
            str(a.ladle_no) if a.ladle_no else '',
            f"{a.carbon:.3f}" if a.carbon else '',
            f"{a.silicon:.3f}" if a.silicon else '',
            f"{a.magnesium:.4f}" if a.magnesium else '',
            f"{a.copper:.4f}" if a.copper else '',
            f"{a.chromium:.4f}" if a.chromium else '',
            f"{a.sulfur:.4f}" if a.sulfur else '',
            f"{a.manganese:.4f}" if a.manganese else '',
            f"{a.phosphorus:.4f}" if a.phosphorus else '',
            f"{a.carbon_equivalent:.3f}" if a.carbon_equivalent else '',
            a.decision or ''
        ]
        data.append(row)

    # Create table
    col_widths = [20*mm, 15*mm, 18*mm, 12*mm, 12*mm, 12*mm, 12*mm, 12*mm, 12*mm, 12*mm, 12*mm, 15*mm, 20*mm]
    table = Table(data, colWidths=col_widths)

    # Table style
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ])

    # Color code decisions
    for i, a in enumerate(analyses, start=1):
        if a.decision == 'ACCEPT':
            style.add('BACKGROUND', (-1, i), (-1, i), colors.lightgreen)
        elif a.decision == 'REJECT':
            style.add('BACKGROUND', (-1, i), (-1, i), colors.salmon)

    table.setStyle(style)
    elements.append(table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    return buffer


def generate_chemical_excel(analyses, date_from, date_to):
    """
    Generate Excel report for chemical analyses.

    Args:
        analyses: List of ChemicalAnalysis objects
        date_from: Start date string
        date_to: End date string

    Returns:
        BytesIO buffer containing Excel file
    """
    if not HAS_XLSXWRITER:
        raise ImportError("xlsxwriter is required for Excel export")

    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    worksheet = workbook.add_worksheet('Chemical Analysis')

    # Formats
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#4472C4',
        'font_color': 'white',
        'border': 1,
        'align': 'center',
        'valign': 'vcenter'
    })

    cell_format = workbook.add_format({
        'border': 1,
        'align': 'center',
        'valign': 'vcenter'
    })

    number_format = workbook.add_format({
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'num_format': '0.0000'
    })

    accept_format = workbook.add_format({
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'bg_color': '#92D050',
        'bold': True
    })

    reject_format = workbook.add_format({
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'bg_color': '#FF6B6B',
        'bold': True
    })

    # Title
    title_format = workbook.add_format({
        'bold': True,
        'font_size': 14,
        'align': 'center'
    })
    worksheet.merge_range('A1:M1', f'Chemical Analysis Report ({date_from} to {date_to})', title_format)

    # Headers
    headers = ['Date', 'Furnace', 'Ladle#', 'Ladle ID', 'C', 'Si', 'Mg', 'Cu', 'Cr', 'S', 'Mn', 'P', 'CE', 'MnE', 'MgE', 'Decision', 'Notes']

    for col, header in enumerate(headers):
        worksheet.write(2, col, header, header_format)

    # Data
    for row, a in enumerate(analyses, start=3):
        worksheet.write(row, 0, str(a.test_date) if a.test_date else '', cell_format)
        worksheet.write(row, 1, a.furnace.furnace_code if a.furnace else '', cell_format)
        worksheet.write(row, 2, a.ladle_no, cell_format)
        worksheet.write(row, 3, a.ladle_id or '', cell_format)
        worksheet.write(row, 4, a.carbon, number_format)
        worksheet.write(row, 5, a.silicon, number_format)
        worksheet.write(row, 6, a.magnesium, number_format)
        worksheet.write(row, 7, a.copper, number_format)
        worksheet.write(row, 8, a.chromium, number_format)
        worksheet.write(row, 9, a.sulfur, number_format)
        worksheet.write(row, 10, a.manganese, number_format)
        worksheet.write(row, 11, a.phosphorus, number_format)
        worksheet.write(row, 12, a.carbon_equivalent, number_format)
        worksheet.write(row, 13, a.manganese_equivalent, number_format)
        worksheet.write(row, 14, a.magnesium_equivalent, number_format)

        # Decision with color
        decision_fmt = accept_format if a.decision == 'ACCEPT' else reject_format if a.decision == 'REJECT' else cell_format
        worksheet.write(row, 15, a.decision or '', decision_fmt)

        worksheet.write(row, 16, a.notes or '', cell_format)

    # Set column widths
    worksheet.set_column('A:A', 12)
    worksheet.set_column('B:B', 10)
    worksheet.set_column('C:C', 8)
    worksheet.set_column('D:D', 15)
    worksheet.set_column('E:O', 10)
    worksheet.set_column('P:P', 12)
    worksheet.set_column('Q:Q', 30)

    workbook.close()
    buffer.seek(0)

    return buffer


def generate_daily_production_pdf(pipes, report_date, by_shift, by_diameter):
    """
    Generate PDF report for daily production.

    Args:
        pipes: List of Pipe objects
        report_date: The report date
        by_shift: Dict of pipes grouped by shift
        by_diameter: Dict of count by diameter

    Returns:
        BytesIO buffer containing PDF
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1*cm,
        bottomMargin=1*cm
    )

    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=20,
        alignment=1
    )
    title = Paragraph(f"Daily Production Report<br/>{report_date}", title_style)
    elements.append(title)
    elements.append(Spacer(1, 10*mm))

    # Summary
    total = len(pipes)
    summary_text = f"Total Pipes: {total}"
    for dn, count in by_diameter.items():
        summary_text += f" | DN{dn}: {count}"

    summary = Paragraph(summary_text, styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 10*mm))

    # By shift sections
    for shift_num in [1, 2, 3]:
        shift_pipes = by_shift.get(shift_num, [])
        if not shift_pipes:
            continue

        shift_title = Paragraph(f"<b>Shift {shift_num} ({len(shift_pipes)} pipes)</b>", styles['Heading2'])
        elements.append(shift_title)

        # Table
        headers = ['No. Code', 'Ladle ID', 'DN', 'Type', 'Machine', 'Weight (kg)']
        data = [headers]

        for pipe in shift_pipes:
            row = [
                pipe.no_code or '',
                pipe.ladle_id or '',
                str(pipe.diameter) if pipe.diameter else '',
                pipe.pipe_type or '',
                pipe.machine.machine_code if pipe.machine else '',
                f"{pipe.actual_weight:.1f}" if pipe.actual_weight else ''
            ]
            data.append(row)

        table = Table(data, colWidths=[35*mm, 30*mm, 20*mm, 20*mm, 25*mm, 25*mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 10*mm))

    doc.build(elements)
    buffer.seek(0)

    return buffer


def generate_defect_report_pdf(chem_defects, stage_defects, defects_by_stage, date_from, date_to):
    """
    Generate PDF report for defects.

    Args:
        chem_defects: List of ChemicalAnalysis with defects
        stage_defects: List of PipeStage with defects
        defects_by_stage: Dict of defect counts by stage
        date_from: Start date
        date_to: End date

    Returns:
        BytesIO buffer containing PDF
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1*cm,
        bottomMargin=1*cm
    )

    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=20,
        alignment=1
    )
    title = Paragraph(f"Defect Summary Report<br/>{date_from} to {date_to}", title_style)
    elements.append(title)
    elements.append(Spacer(1, 10*mm))

    # Summary
    total_chem = len(chem_defects)
    total_stage = len(stage_defects)

    summary = Paragraph(f"Chemical Defects: {total_chem} | Stage Defects: {total_stage}", styles['Normal'])
    elements.append(summary)
    elements.append(Spacer(1, 10*mm))

    # Defects by stage
    if defects_by_stage:
        stage_title = Paragraph("<b>Defects by Stage:</b>", styles['Heading2'])
        elements.append(stage_title)

        data = [['Stage', 'Count']]
        for stage, count in defects_by_stage.items():
            data.append([stage, str(count)])

        table = Table(data, colWidths=[80*mm, 40*mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(table)

    doc.build(elements)
    buffer.seek(0)

    return buffer
