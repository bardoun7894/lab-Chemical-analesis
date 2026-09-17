# Tasks: SPC, Process Capability & Lab Trends (Analytics Phase 1)

**Input**: [spec.md](./spec.md) + [plan.md](./plan.md)
**Prerequisites**: plan.md read; on branch `010-spc-capability`

## Phase 1 — Setup

- [x] T001 Create branch `010-spc-capability` from `main` (note: current session is on `feat/permissions-restore-defaults` — commit/stash state first; see concurrent-sessions memory)
- [x] T002 [P] Register permission keys `reports.spc` + `reports.capability` in `app/models/permission.py` taxonomy and ROLE_DEFAULTS (admin/super_admin only)

## Phase 2 — US1: SPC control charts (P1)

- [x] T003 [US1] Write failing tests `tests/test_spc_service.py`: IMR limits (CL=mean, UCL/LCL=mean±2.66MR̄), X̄-R with A2/D3/D4, P-chart, C-chart vs hand-computed fixtures
- [x] T004 [US1] Add tests for Nelson rules 1,2,3,5,6,7,8 — one synthetic series per rule
- [x] T005 [US1] Add edge-case tests: n<2 → insufficient-data marker; σ=0 → no-variation state; SUPERSEDED tests excluded; case-insensitive decision compare for P-chart
- [x] T006 [US1] Implement `app/services/spc_service.py` (`build_series`, `imr_limits`, `xbar_r_limits`, `p_chart_limits`, `c_chart_limits`, `nelson_violations`) until T003–T005 pass
- [x] T007 [US1] Route `GET /reports/spc` in `app/routes/reports.py` with `reports.spc` gate + unified filters
- [x] T008 [US1] Template `app/templates/reports/spc.html` (extends `_analytics_base.html`, Chart.js chart, violation list, insufficient-data state) + launcher link in `reports/index.html`
- [x] T009 [US1] Excel export of chart series via `export_service.build_xlsx`
- [x] T010 [US1] Route tests: admin 200, ungranted role 403, empty DB renders safe

## Phase 3 — US2: Process capability (P1)

- [x] T011 [US2] Write failing tests `tests/test_capability_service.py`: Cp/Cpk/Pp/Ppk vs textbook fixtures; one-sided tensile; n<30 low-confidence flag
- [x] T012 [US2] Test that `spec_limits_for` delegates to the decision service's element_rules resolver (mock/spy — no re-implementation)
- [x] T013 [US2] Implement `app/services/capability_service.py` until T011–T012 pass
- [x] T014 [US2] Route `GET /reports/capability` + template `capability.html` (indices table, histogram with LSL/USL lines, unavailable-limits list)
- [x] T015 [US2] Route tests: 200/403, no-limits characteristic shows "unavailable" (never fabricated)

## Phase 4 — US3: Lab trends dashboard (P2)

- [x] T016 [US3] Route `GET /reports/lab-trends` reusing `spc_service.build_series` for tensile, hardness, elongation, nodularity, CE, microstructure %
- [x] T017 [US3] Template `lab_trends.html` — six independent panels, point→source-record links, shared filter bar; label microstructure honestly ("microstructure %", not "ferrite")
- [x] T018 [US3] Route test: renders with partial data (one empty panel must not break others)

## Phase 5 — US4: Stage cycle-time analytics (P2)

- [x] T019 [US4] Write failing tests `tests/test_cycle_time_service.py`: per-stage dwell, inter-stage waiting, lead time, missing-timestamp exclusion + coverage fraction
- [x] T020 [US4] Implement `app/services/cycle_time_service.py` until T019 passes
- [x] T021 [US4] Route `GET /reports/stage-cycle-time` + template (stage ranking by avg dwell, bottleneck table, coverage note); stage list via `ProductionStage.active_names()`

## Phase 6 — US5: Stage audit-trail report (P3)

- [x] T022 [US5] Route `GET /reports/stage-audit` querying `PipeStageHistory` with pipe/stage/user/date filters
- [x] T023 [US5] Template `stage_audit.html` with TableTools column/row picker + Excel export

## Phase 7 — US6: Weight-saving fix (P3)

- [x] T024 [US6] Failing test: pipe with `iso_weight=0` + product weight → saving via product standard; both missing → excluded + counted
- [x] T025 [US6] Patch `analytics_service.weight_saving()` / `iso_metric_value()` fallback to `Product.weight_kg`; coverage note in `weight_saving.html`

## Phase 8 — Polish & verify

- [x] T026 Full test suite green (regression: no existing behavior changed)
- [x] T027 Manual smoke on prod data via test_client + `_user_id` session: SC-001 ✅ 3 chars match hand-calc, SC-002 ✅ Cpk 0.228 == textbook, SC-004 ⚠️ prod has 4/387 stage_time values → 0% coverage (report shows honest note; math verified by unit fixtures)
- [x] T028 Request code review (`requesting-code-review`)
- [x] T029 Backup prod DB (backups screen / online-backup API), deploy via `git show HEAD:` hot-swap pattern, auth-smoke-test all new routes
- [x] T030 `/kb-spec log done`, reply drafted for WhatsApp (send path is the user's Telegram bot — daemon has no CLI send); `/kb-ingest` backlog (51 raw files) carried over

**Dependencies**: T002 before T007/T014; T006 before T016; T024–T025 independent. **Parallel**: Phases 4/5/6/7 can run in parallel after T006 (build_series is shared).
