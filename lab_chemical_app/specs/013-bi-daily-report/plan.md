# v52 Daily Report in BI Dashboard — Implementation Plan

Source of truth: `CCM_BI_Dashboard_v52.html` lines 1490–1516 (panel),
5689–5802 (`buildDailyReport`/`calcStats`), 5815–6015 (`renderDailyHTML`).

## Files Touched

| File | Change |
|---|---|
| `app/services/bi_service.py` | + `daily_report(report_date)` and `_daily_stats(pipes)` |
| `app/routes/reports.py` | `bi_dashboard()` accepts `?rpt_date=`; + `/reports/export/daily-report-pdf` |
| `app/services/report_service.py` | + `generate_daily_report_pdf(report)` |
| `app/templates/reports/bi_dashboard.html` | + "📋 التقرير اليومي" section (top) + print CSS |
| `tests/test_bi_daily_report.py` | new |

## Task 1 — `bi_service.daily_report(report_date=None)`

- Resolve dates from `Pipe.production_date` distinct values (sorted).
  Auto-pick last when `report_date` is None / not a production date.
- `yesterday` = previous production date; `week_dates` = last 6 before today.
- One query for all pipes in `{today, yesterday} ∪ week_dates`.
- `_daily_stats(pipes)` (port of v52 `calcStats`):
  - `valid` = decided (`_is_final`), `rej` = `_is_reject`
  - `iso`/`act` = sums of `iso_weight`/`actual_weight`;
    `sav = (iso-act)/iso*100`, `sav_kg = iso-act`
  - `by_machine` / `by_sup` (`shift_engineer`) / `by_shift`: `{total, rejects}`
  - `shift_detail`: group `shift|dn|pipe_class`; decision-maker names from
    one `PipeStage` query (`approved_by_id` → User map, top name + extras)
  - `top_reasons` = 5 (from `final_decision_reason`, blank/`nan` filtered)
  - `top_molds` = 3 (from `mold_number` on rejected pipes)
- Week average: pooled reject % + `round(n/len(week_dates))` pipes/day.
- `status` (7/4 thresholds), `alerts` list, Arabic `summary` string,
  Arabic date (same month names as v52).
- Return dict; every table row carries yesterday % for ▲▼ arrows.

## Task 2 — Route wiring (`app/routes/reports.py`)

- `bi_dashboard()`: parse `?rpt_date=` → `data["daily_report"] =
  bi_service.daily_report(parsed_date)`; keep JSON payload untouched.
- New route `/reports/export/daily-report-pdf`:
  `login_required` + `requires_permission("reports","view")` +
  `requires_permission("reports","export")` (same as `spc.xlsx`), returns
  `send_file(pdf_buffer, mimetype="application/pdf",
  download_name=f"daily_report_{date}.pdf", as_attachment=True)`.

## Task 3 — `report_service.generate_daily_report_pdf(report)`

- A4 portrait reportlab doc, same margins as `generate_daily_production_pdf`.
- Sections: title + Arabic date, status line, KPI table, machine table,
  supervisor table, reasons table, molds table, shift table + week footer,
  shift-detail table, summary paragraph (`reshape_arabic`), alerts,
  signature row. All Arabic strings passed through `reshape_arabic`.

## Task 4 — Template section (`bi_dashboard.html`)

- Top card: date input (pre-filled), ⚡ توليد (GET submit), 🖨️ طباعة
  (`window.print()`), 📄 PDF (link to export route), 📋 نسخ النص
  (clipboard JS).
- Report body `#dailyReport` (print-isolated via `@media print` hiding
  everything else): all sections per spec with v52 colors/thresholds.
- Arrows rendered server-side into the data (Python-side helper strings) or
  via Jinja macro — keep logic in service, template renders dicts.

## Task 5 — Tests (`tests/test_bi_daily_report.py`)

Same `_Base` pattern as `tests/test_bi_service.py`:
1. stats math: decided denominator, rejects, saving %
2. yesterday = previous production date (gap over weekend-like hole)
3. week average pooled % + pipes/day
4. machine/supervisor/shift rows + yesterday arrows data
5. reasons filter (`nan`, blank) and top-5; molds top-3
6. alerts triggers (machine ≥7/≥4, saving<0, total≥7, reason≥5)
7. shift_detail grouping + approved_by names
8. auto-pick latest date when None
9. route: `/reports/bi-dashboard?rpt_date=…` renders; PDF route 200 +
   `%PDF` magic; permission denial without `reports.export`

## Task 6 — Full suite + manual verify

`python -m pytest tests/ -x` then eyeball
`/reports/bi-dashboard?rpt_date=…` against the v52 HTML on the same data.

## Self-Review

- No new tables/migrations (read-only feature).
- No changes to existing sections of the BI dashboard.
- All thresholds/labels byte-match v52 where rendered.
