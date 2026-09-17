# Tasks — 013-bi-daily-report

- [x] 1. `bi_service.daily_report()` + `_daily_stats()` with date resolution, slices, breakdowns, reasons, molds, summary, alerts
- [x] 2. Route wiring: `?rpt_date=` on `/reports/bi-dashboard`; new `/reports/export/daily-report-pdf` with export permission
- [x] 3. `report_service.generate_daily_report_pdf()` — all sections, Arabic reshaped
- [x] 4. Template: daily report section + controls + print CSS in `bi_dashboard.html`
- [x] 5. Tests `tests/test_bi_daily_report.py` — stats, slices, arrows, alerts, PDF, permissions (20 tests)
- [x] 6. Full test suite green (296 passed) + parity check vs v52
