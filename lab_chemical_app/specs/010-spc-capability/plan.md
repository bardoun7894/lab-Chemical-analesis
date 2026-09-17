# Implementation Plan: SPC, Process Capability & Lab Trends (Analytics Phase 1)

**Branch**: `010-spc-capability` | **Date**: 2026-07-27 | **Spec**: [spec.md](./spec.md)

## Summary

Add SPC control charts (I-MR, X̄-R, P, C) with Nelson/WECO rule detection, process capability indices (Cp/Cpk/Pp/Ppk/DPMO) against existing spec limits, a six-panel lab-trends dashboard, stage cycle-time analytics, a stage audit-trail report, and a weight-saving fallback fix — all read-only analytics over existing tables, reusing the unified filter set, analytics base template, TableTools export, Chart.js, and the permission matrix. Zero schema changes; zero changes to business logic or data-entry flows.

## Technical Context

- **Stack**: Flask + SQLAlchemy + SQLite, Jinja2, Chart.js, Bootstrap 5 (existing app stack; no new deps — SPC/capability math is pure Python stdlib `statistics`)
- **Patterns to reuse**: `analytics_service.parse_filters`/`apply_pipe_filters`, `_analytics_base.html`, `export_service` (build_xlsx/build_print), TableTools macro, `@requires_permission`, `ProductionStage.active_names()`, `pipe_machine_code()`
- **Storage**: none new — all series computed per request (data volume is small; cache later only if profiling demands)

## Architecture

### New service: `app/services/spc_service.py`
- `build_series(characteristic, filters)` → ordered `[(date, value, subgroup, source_id, source_url)]` from ACTIVE `MechanicalTest` or `ChemicalAnalysis` (single entry point used by SPC, capability, and trends).
- `imr_limits(values)` / `xbar_r_limits(subgroups)` / `p_chart_limits(proportions, n)` / `c_chart_limits(counts)` → CL/UCL/LCL per chart type (constants A2/D3/D4 for X̄-R).
- `nelson_violations(values, cl, sigma)` → list of rule violations with point indices (rules 1,2,3,5,6,7,8 per spec FR-003).
- `p_chart_data(filters, group_by)` → proportions of non-accept decisions (case-insensitive compare, HOLD counted separately per non-conformance lesson).
- `c_chart_data(filters, group_by)` → defect counts per pipe/day/stage.

### New service: `app/services/capability_service.py`
- `spec_limits_for(characteristic)` → (LSL, USL) from `element_rules` via the **same resolution function the decision service uses** (never re-implement; import it — avoids propagating the element-rules gap bug) and the tensile MPa threshold constant.
- `capability_indices(values, lsl, usl)` → Cp/Cpk (within, via MR̄/d2), Pp/Ppk (overall, via sample σ), sigma level, DPMO; one-sided when only one limit exists; `n<30` → low-confidence flag.

### New service: `app/services/cycle_time_service.py`
- `stage_dwell(filters)` → per-stage avg/min/max dwell from `PipeStage.stage_date`+`stage_time`; inter-stage waiting per consecutive pair; per-pipe lead time; coverage fraction (excluded intervals counted).

### Routes (all in `app/routes/reports.py`, all `reports.view`-family permissions)
| Route | Permission key | Service |
|---|---|---|
| `GET /reports/spc` | `reports.spc` (new) | spc_service |
| `GET /reports/capability` | `reports.capability` (new) | capability_service |
| `GET /reports/lab-trends` | `reports.view` | spc_service.build_series |
| `GET /reports/stage-cycle-time` | `reports.view` | cycle_time_service |
| `GET /reports/stage-audit` | `reports.view` | PipeStageHistory query |
| `GET /reports/export/spc.xlsx` etc. | `reports.export` | export_service.build_xlsx |

New permission keys registered in `app/models/permission.py` taxonomy + ROLE_DEFAULTS (admin/super_admin only; fail-closed for others — matches matrix authority rules).

### Templates (`app/templates/reports/`)
`spc.html`, `capability.html`, `lab_trends.html`, `stage_cycle_time.html`, `stage_audit.html` — all extending `_analytics_base.html`, Chart.js for charts, TableTools for the audit table. Links added to `reports/index.html` launcher. Design tokens from style.css only (no inline styles, no new !important — per UI redesign memory).

### Weight-saving fix
`analytics_service.weight_saving()` + `iso_metric_value()`: when `Pipe.iso_weight <= 0`, resolve `Product.weight_kg` via `pipe.production_order.product` (or `pipe.product_id`); exclude + count when both missing.

## Testing approach (TDD)

- `tests/test_spc_service.py` — IMR/X̄-R/P/C limit math vs hand-computed fixtures; Nelson rules each triggered by synthetic series; σ=0 and n<2 edge cases; ACTIVE-only filtering.
- `tests/test_capability_service.py` — Cp/Cpk/Pp/Ppk vs textbook values; one-sided tensile; element_rules resolution delegated (mock to prove no re-implementation); n<30 flag.
- `tests/test_cycle_time_service.py` — dwell/waiting/lead math; missing-timestamp exclusion + coverage.
- Route tests via Flask test_client + `_user_id` session (prod-smoke pattern): 200 for admin, 403 for ungranted role, empty-safe rendering on empty DB.
- Regression: full existing suite must pass untouched.

## Risk notes

- **Element-rules gap bug**: capability reads limits through the decision service's resolver; the open bug stays open but is not amplified.
- **Ferrite proxy**: `percent_70/percent_40` used as microstructure trend; label honestly in UI ("microstructure %", not "ferrite") pending DrAlaa's confirmation.
- **Performance**: series capped (e.g. 2000 points) with a visible truncation note — no silent caps.
- **Deploy**: hot-swap safe (new files + additive edits); backup DB before deploy per DrAlaa's standing instruction; `git show HEAD:` deploy pattern (never dirty-tree scp).
