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
                pipe.pipe_class or '',
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


def generate_daily_report_pdf(report):
    """
    PDF of the v52-style daily report (BI dashboard section).

    Args:
        report: dict returned by bi_service.daily_report()

    Returns:
        BytesIO buffer containing PDF
    """
    R = reshape_arabic
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.2*cm, leftMargin=1.2*cm,
        topMargin=1.2*cm, bottomMargin=1.2*cm,
    )

    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DailyTitle', parent=styles['Heading1'],
        fontSize=16, spaceAfter=4, alignment=1)
    sub_style = ParagraphStyle(
        'DailySub', parent=styles['Normal'],
        fontSize=10, alignment=1, textColor=colors.grey)

    t = report["today"]
    y = report["yest"]

    elements.append(Paragraph(R("التقرير اليومي للإنتاج والجودة"), title_style))
    elements.append(Paragraph(R("مصنع CCM — أنابيب الحديد الزهر المطيل"), sub_style))
    elements.append(Paragraph(
        f"{R(report['arabic_date'])} — {report['date']}", sub_style))
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph(
        R(f"الحالة العامة: {report['status']['text']} — "
          f"معدل الرفض: {t['rej_pct']:.2f}%"), styles['Heading2']))
    elements.append(Spacer(1, 4*mm))

    def _arrow(cur, prev, lower_better=True):
        if prev is None:
            return "—"
        diff = cur - prev
        if abs(diff) < 0.01:
            return "→"
        return ("▲" if diff > 0 else "▼")

    # KPI table
    kpi_data = [[R("المؤشر"), R("اليوم"), R("التغيير عن أمس")],
                [R("إجمالي الإنتاج"), str(t["produced"]),
                 _arrow(t["produced"], y["produced"] if y else None, False)],
                [R("تم البت فيها"), str(t["n"]),
                 _arrow(t["n"], y["n"] if y else None, False)],
                [R("المرفوض"), str(t["rejects"]),
                 _arrow(t["rejects"], y["rejects"] if y else None)],
                [R("معدل الرفض"), f"{t['rej_pct']:.2f}%",
                 _arrow(t["rej_pct"], y["rej_pct"] if y else None)],
                [R("Saving %"), f"{t['sav_pct']:.2f}%",
                 f"Saving: {t['sav_kg'] / 1000:.2f} MT"]]
    wa = report["week_avg"]
    if wa:
        kpi_data.append([R("متوسط آخر أسبوع"),
                         f"{wa['rej_pct']:.2f}%", f"{wa['n_per_day']} ماسورة/يوم"])
    elements.append(_pdf_table(kpi_data, [55*mm, 45*mm, 45*mm]))
    elements.append(Spacer(1, 6*mm))

    def _perf_table(title, rows):
        data = [[R(h) for h in (title, "الإجمالي", "مرفوض", "%", "التغيير")]]
        for r in rows:
            data.append([R(str(r["key"])), str(r["total"]), str(r["rejects"]),
                         f"{r['pct']:.1f}%", _arrow(r["pct"], r.get("y_pct"))])
        return _pdf_table(data, [45*mm, 25*mm, 25*mm, 25*mm, 25*mm])

    elements.append(_perf_table("الماكينة", report["machine_rows"]))
    elements.append(Spacer(1, 4*mm))
    elements.append(_perf_table("المشرف", report["sup_rows"]))
    elements.append(Spacer(1, 6*mm))

    # Rejection reasons
    if report["top_reasons"]:
        data = [[R(h) for h in ("#", "السبب", "العدد", "%")]]
        for i, r in enumerate(report["top_reasons"], 1):
            data.append([str(i), R(r["reason"]), str(r["count"]),
                         f"{r['pct']:.1f}%"])
        elements.append(_pdf_table(data, [12*mm, 70*mm, 30*mm, 28*mm]))
        elements.append(Spacer(1, 6*mm))

    # Molds
    if report["top_molds"]:
        data = [[R(h) for h in ("#", "القالب", "مرفوض")]]
        for i, m in enumerate(report["top_molds"], 1):
            data.append([str(i), R(m["mold"]), str(m["count"])])
        elements.append(_pdf_table(data, [12*mm, 70*mm, 30*mm]))
        elements.append(Spacer(1, 6*mm))

    # Shifts + week footer
    data = [[R(h) for h in ("الوردية", "الإجمالي", "مرفوض", "%")]]
    for r in report["shift_rows"]:
        data.append([R(f"الوردية {r['key']}"), str(r["total"]),
                     str(r["rejects"]), f"{r['pct']:.1f}%"])
    elements.append(_pdf_table(data, [45*mm, 35*mm, 35*mm, 25*mm]))
    if wa:
        elements.append(Spacer(1, 2*mm))
        elements.append(Paragraph(R(
            f"متوسط آخر أسبوع: {wa['rej_pct']:.2f}% رفض | "
            f"{wa['n_per_day']} ماسورة/يوم"), sub_style))
    elements.append(Spacer(1, 6*mm))

    # Shift detail
    if report["shift_detail_rows"]:
        data = [[R(h) for h in ("الوردية", "القطر DN", "النوع",
                                "متخذ القرار", "الإجمالي", "مرفوض", "%")]]
        for r in report["shift_detail_rows"]:
            data.append([str(r["shift"]), f"DN{r['dn']}", R(str(r["cls"])),
                         R(r["approver"]), str(r["total"]),
                         str(r["rejects"]), f"{r['pct']:.1f}%"])
        elements.append(_pdf_table(
            data, [16*mm, 22*mm, 22*mm, 34*mm, 18*mm, 18*mm, 18*mm]))
        elements.append(Spacer(1, 6*mm))

    # Summary
    elements.append(Paragraph(R("الملخص التنفيذي (تلقائي)"), styles['Heading2']))
    elements.append(Paragraph(R(report["summary"]), styles['Normal']))
    elements.append(Spacer(1, 6*mm))

    # Alerts
    if report["alerts"]:
        elements.append(Paragraph(R("تنبيهات اليوم"), styles['Heading2']))
        for a in report["alerts"]:
            elements.append(Paragraph(R(a), styles['Normal']))
        elements.append(Spacer(1, 6*mm))

    # Signatures
    sig = Table(
        [[R(s) for s in report["signatures"]]],
        colWidths=[55*mm, 55*mm, 55*mm])
    sig.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 0.8, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(Spacer(1, 10*mm))
    elements.append(sig)

    doc.build(elements)
    buffer.seek(0)
    return buffer


def _pdf_table(data, col_widths):
    """Styled table used by the daily report PDF."""
    table = Table(data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#334155')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor('#f1f5f9')]),
    ]))
    return table


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
