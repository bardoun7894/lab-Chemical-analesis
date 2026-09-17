# Research: SPC, Process Capability & Lab Trends (Phase 1)

**Generated**: 2026-07-27
**Feature**: [spec.md](./spec.md)
**Basis**: [Gap analysis](../009-analytics-platform-gap/gap-analysis.md) vs DrAlaa's analytics prompt (WhatsApp 2026-07-27)

## Prior art from KB

*Queried at 2026-07-27 · Mode: pre · Question: "What prior decisions, constraints, existing reports, and data gaps does the KB document for SPC / capability / trends / cycle-time analytics?"*
*(kb-query script currently hangs — grounded from KB index + verified project memory instead; citations below are live KB articles)*

- [[concepts/iso-weight-data-gap]] — `Pipe.iso_weight` is always 0.0; saving-weight math must guard `iso>0 and act>0`. `Product.weight_kg` exists as a viable standard-weight fallback.
- [[concepts/shift-responsible-attribution-gap]] — `PipeStage.shift_responsible` 100% NULL; attribute via `Pipe.shift_engineer` (54% coverage, bucket rest as غير محدد). `lab_decision` (not `final_decision_value`) is the live decision signal.
- [[concepts/machine-filter-per-stage-semantics]] — machine attribution lives on the CCM `PipeStage`, never `Pipe.machine_id`. SPC by-machine must use `analytics_service.pipe_machine_code()`.
- [[concepts/mechanical-test-supersede-pattern]] — only `status='ACTIVE'` MechanicalTests count for any statistics; SUPERSEDED rows are history.
- [[concepts/mechanical-statistical]] (report exists) — `analytics_service.mechanical_statistical()` already computes avg/min/max/std + 10-bin histogram; Phase 1 extends, not replaces.
- [[concepts/list-page-unified-filters-pattern]] — reuse `parse_filters` / `apply_pipe_filters` 16-field filter set for all new report pages.
- [[concepts/non-conformance-register]] — `classify_decision` does NOT cover Hold; any defect/oom chart reusing classification must handle HOLD explicitly.
- [[concepts/element-rules-range-gap-bug]] — OPEN BUG: gaps between element_rules ranges + float drift can misclassify in-spec ladles. Capability math must read the same rules the decision engine uses, and this bug must not be propagated into Cpk results.
- [[concepts/pipe-detail-mechanical-card]] — mechanical data display patterns already established (decision badges, ACTIVE-only).
- Memory: [[analytics-data-realities]] — prod facts: 0 deliveries, machine only on CCM stage, attribution constraints.
- Memory: [[stage-management-db-backed]] — `ProductionStage` is DB-backed; stage lists must come from `active_names()`, never hardcoded.
- Memory: [[decision-case-sensitivity]] — never exact-case-compare stage decisions; compare case-insensitively.

## Data availability for Phase 1 (verified in audit)

| Signal | Source | Coverage |
|---|---|---|
| Tensile (kgf + MPa), elongation, hardness, nodularity, carbides | `MechanicalTest` (ACTIVE) | good |
| All chemical elements + CE | `ChemicalAnalysis` per ladle | good |
| Accept/reject/hold proportions | `Pipe.lab_decision`, `PipeStage.decision` | good |
| Defect counts/types | `PipeStage.has_defect`, `defect_type`, `DefectType` | good |
| Spec limits (chemistry) | `element_rules` (decision engine source of truth) | good — see open bug above |
| Spec limit (tensile) | decision-service MPa threshold | one-sided |
| Stage timestamps (cycle/stage/waiting time) | `PipeStage.stage_date` + `stage_time` | present, unexploited |
| Audit trail | `PipeStageHistory` | written, never queried by any report |
