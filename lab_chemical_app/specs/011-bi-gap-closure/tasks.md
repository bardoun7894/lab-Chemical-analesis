# Tasks: BI Gap Closure (Analytics Phase 2)

**Input**: [spec.md](./spec.md) + [plan.md](./plan.md)
**Prerequisites**: on branch `011-bi-gap-closure` (created from main post-Phase-1)

## Phase 1 — Service core (TDD)

- [x] T001 Write failing tests `tests/test_bi_service.py`: `ytd_comparison` monthly rows + deltas; zero-denominator reject cell → None; case-insensitive reject counts
- [x] T002 Tests: `stage_reject_matrix` stage×month percentages; empty cell → None; two-year span
- [x] T003 Tests: `period_compare` KPIs + deltas; alert fires exactly above threshold, not at it; empty period → no-data state
- [x] T004 Tests: `stage_funnel` counts match direct PipeStage counts; drops computed; custom stage appears (DB-driven)
- [x] T005 Tests: `defect_heatmap` cell counts + % metric; zero-pipe cell → None; max tracked
- [x] T006 Tests: `saving_matrix` cells reconcile with per-pipe `planned_weight`; both-missing excluded + counted
- [x] T007 Tests: `data_quality` fill rates vs direct counts; score 100 on complete fixture
- [x] T008 Tests: `diagnose` — every rule trigger + no-trigger case; clean fixture → score 100, no problems; minimum-denominator guard
- [x] T009 Implement `app/services/bi_service.py` until T001–T008 pass

## Phase 2 — Routes & templates

- [x] T010 `GET /reports/ytd-comparison` + `ytd_comparison.html` (monthly table w/ deltas, stage×month matrix, YoY chart)
- [x] T011 `GET /reports/period-compare` + `period_compare.html` (KPI cards w/ deltas, threshold inputs, alerts list)
- [x] T012 `GET /reports/stage-funnel` + `stage_funnel.html` (CSS funnel bars + drop table)
- [x] T013 `GET /reports/defect-heatmap` + `defect_heatmap.html` (server-colored matrix, count/% toggle)
- [x] T014 `GET /reports/saving-matrix` + `saving_matrix.html` (cross-tab + totals + coverage note)
- [x] T015 `GET /reports/data-quality` + `data_quality.html` (fill-rate bars, per-stage table, gap→report mapping)
- [x] T016 `GET /reports/diagnosis` + `diagnosis.html` (score card, problems list w/ RCA hints + drill links)
- [x] T017 Launcher cards in `reports/index.html` (Quality/Analytics sections)
- [x] T018 Route tests `tests/test_bi_routes.py`: granted 200, empty-safe, diagnosis clean fixture score 100

## Phase 3 — Verify, review, ship

- [x] T019 Full suite green
- [x] T020 Prod smoke via test_client: all 7 routes 200; SC-001 3 sampled YTD cells; SC-003 funnel vs direct counts; SC-005 data-quality vs direct COUNTs
- [x] T021 Code review pass; fix findings
- [x] T022 Backup prod DB; hot-swap deploy from `git show HEAD:`; in-container + anon smoke
- [x] T030 `/kb-spec log done`, memory update, draft WhatsApp reply (Phase 2 shipped; Excel no longer needed for these panels)
