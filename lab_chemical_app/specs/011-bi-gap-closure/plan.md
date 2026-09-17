# Implementation Plan: BI Gap Closure (Analytics Phase 2)

**Branch**: `011-bi-gap-closure` | **Date**: 2026-07-27 | **Spec**: [spec.md](./spec.md)

## Summary

Seven read-only BI panels closing the gap vs DrAlaa's Excel dashboard: YTD comparison, period compare with alerts, stage funnel, defect heatmap DN×Class, saving matrix DN×Class, data quality panel, and a rules-based auto-diagnosis with RCA hints. Same architecture as Phase 1: pure-Python services + reports.py routes + `_analytics_base.html` templates, permission-matrix gated, zero schema changes.

## Technical Context

- Flask + SQLAlchemy + SQLite, Jinja2, Chart.js, Bootstrap 5 (existing stack, no new deps)
- Reuse: `parse_filters`/`apply_pipe_filters`, `planned_weight()` (Phase 1), `ProductionStage.active_names()`, `pipe_machine_code()`, `_render_analytics_report`, `export_service`, case-insensitive decision compares
- All matrices computed in Python (volumes small); heatmap colors computed server-side (green→red scale by value/max) so print works without JS

## Architecture

### New service: `app/services/bi_service.py`
- `ytd_comparison(year, compare_year, filters)` → monthly rows (count, actual_wt, planned_wt, rejects, reject_pct) per year + deltas; `stage_reject_matrix(year, compare_year)` → {stage: {month: pct}} from PipeStage decisions (case-insensitive), None for zero-denominator cells.
- `period_compare(a_from, a_to, b_from, b_to, thresholds)` → KPIs (produced, actual/planned weight, reject %, defect count, saving kg) per period + deltas; `alerts` = metrics breaching thresholds (each threshold user-settable, sane defaults).
- `stage_funnel(filters)` → ordered [(stage, count, drop_abs, drop_pct)] over pipes that have a PipeStage row per stage (canonical order).
- `defect_heatmap(filters, metric)` → {dns, classes, cells{(dn,cls): (defects, pipes)}, max} — % metric = defects/pipes, None when pipes=0.
- `saving_matrix(filters)` → same shape, cells aggregate planned/actual/saving via `planned_weight()`; excluded counted.
- `data_quality()` → per-field fill rates (Pipe: actual_weight, iso_weight>0, product_id, production_order_id, shift_engineer, final_decision_value; PipeStage: stage_date, stage_time, machine_id) + per-stage timestamp coverage + overall score (mean of field rates) + gap→blocked-report mapping.
- `diagnose(filters)` → list of {rule, severity, title, detail, rca_hint, drill_url}; rules: reject-spike vs trailing 3mo (>50%), stage reject concentration (>40%), defect-machine concentration (>60%), stage_time coverage (<20%), undecided pipes (>15%), negative saving. Score = 100 − Σ severity weights, floored at 0.

### Routes (all in `app/routes/reports.py`, gated `reports.view` family)
| Route | Service |
|---|---|
| `GET /reports/ytd-comparison` | ytd_comparison + stage_reject_matrix |
| `GET /reports/period-compare` | period_compare |
| `GET /reports/stage-funnel` | stage_funnel |
| `GET /reports/defect-heatmap` | defect_heatmap |
| `GET /reports/saving-matrix` | saving_matrix |
| `GET /reports/data-quality` | data_quality |
| `GET /reports/diagnosis` | diagnose |

### Templates (`app/templates/reports/`)
`ytd_comparison.html`, `period_compare.html`, `stage_funnel.html`, `defect_heatmap.html`, `saving_matrix.html`, `data_quality.html`, `diagnosis.html` — all extend `_analytics_base.html`; heatmap/matrices as server-colored HTML tables (print-safe); funnel as CSS bars; launcher cards in `reports/index.html`.

## Testing approach (TDD)

- `tests/test_bi_service.py` — fixtures with known counts per function; boundary tests for alerts (exactly at threshold = no alert, +ε = alert); zero-denominator cells → None; case-insensitive decisions.
- `tests/test_bi_routes.py` — auth pattern from Phase 1: granted 200, empty-DB safe, diagnosis clean fixture = score 100/no problems.
- Regression: full suite must pass.

## Risk notes

- **Decision case-sensitivity**: all compares `.lower()` (project rule).
- **Reconciliation**: saving matrix and weight_saving share `planned_weight()` so totals match.
- **Diagnosis noise**: rules need denominators (e.g. ≥10 decided pipes in period) before firing — no alerts on trivial samples.
- **Deploy**: same hot-swap pattern from `git show HEAD:`; DB backup first.
