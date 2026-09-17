# Feature Specification: SPC, Process Capability & Laboratory Trends (Analytics Phase 1)

**Feature Branch**: `010-spc-capability`
**Created**: 2026-07-27
**Status**: Draft
**Input**: Gap analysis (specs/009) of DrAlaa's analytics-platform prompt — Phase 1: everything buildable on existing data with zero schema change: SPC charts, process capability, lab trends, cycle-time analytics, audit-trail report, weight-saving fix.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - SPC control charts on lab measurements (Priority: P1)

A quality engineer opens **Reports → SPC Charts**, picks a characteristic (e.g. Tensile MPa, Elongation, Hardness, Nodularity, or any chemical element), a chart type (I-MR for individual measurements, X̄-R when subgrouped by ladle), and a date range plus the standard filters (DN, class, machine, shift, order). They see the control chart with center line, UCL/LCL, out-of-control points highlighted, and a list of Western Electric / Nelson rule violations with the offending points. They can export the chart data to Excel.

**Why this priority**: The single biggest gap vs the client's prompt; all data exists today; directly answers "SPC by machine/shift/product/heat/ladle".

**Independent Test**: With existing prod data, selecting Tensile MPa / I-MR / last 90 days renders a chart whose CL/UCL/LCL match a hand calculation over the same series, and every point beyond 3σ is flagged.

**Acceptance Scenarios**:

1. **Given** ACTIVE mechanical tests exist in the range, **When** the engineer selects Tensile MPa + I-MR, **Then** the chart shows CL = mean, UCL/LCL = mean ± 2.66·MR̄, and points outside limits are marked.
2. **Given** a chemical element selected, **When** grouping by ladle, **Then** each ladle contributes one point (its analysis value) and the series is ordered by test date.
3. **Given** a series with 9 consecutive points on one side of CL, **When** the chart renders, **Then** a Nelson rule-2 violation is listed with those point indices.
4. **Given** fewer than 2 data points, **When** rendering, **Then** an empty-safe "insufficient data" state shows (no crash, no fake limits).
5. **Given** SUPERSEDED mechanical tests in range, **When** the series is built, **Then** only ACTIVE tests are included.

---

### User Story 2 - Process capability vs spec limits (Priority: P1)

A quality engineer opens **Reports → Capability**, picks a characteristic with known spec limits (chemical elements from element_rules; tensile vs its one-sided MPa threshold), and sees Cp, Cpk, Pp, Ppk, sigma level, DPMO, plus a capability histogram with spec lines (LSL/USL) overlaid. Characteristics without stored limits are listed as "no spec limits configured" rather than fabricated.

**Why this priority**: Second pillar of the prompt's quality stack; reuses the same series-building as US1; spec limits already exist in element_rules.

**Independent Test**: For carbon on a grade with a known element_rules range, computed Cpk matches the textbook formula over the same data; one-sided tensile reports only Cpk-lower.

**Acceptance Scenarios**:

1. **Given** a two-sided spec (element min+max), **When** computing, **Then** Cp/Cpk/Pp/Ppk all render with within-sample and overall sigma distinguished.
2. **Given** a one-sided spec (tensile lower limit), **When** computing, **Then** only Cpk-lower / Ppk-lower are shown and Cp is omitted.
3. **Given** an element whose element_rules have gaps between ranges (known open bug), **When** computing, **Then** the report uses the exact same rule resolution as the decision service and never invents a midpoint.
4. **Given** fewer than 30 points, **When** rendering, **Then** indices are shown with a visible "low confidence (n<30)" note.

---

### User Story 3 - Laboratory trends dashboard (Priority: P2)

A lab manager opens **Reports → Lab Trends** and sees small-multiple trend charts over time for the six properties the client named — Tensile, Hardness, Elongation, Nodularity, Carbon Equivalent, plus Ferrite proxy (percent_70/percent_40 microstructure fields) — filterable by DN, order, furnace, machine; each point links through to the underlying chemical analysis or mechanical test.

**Why this priority**: Directly named in the prompt ("Tensile Trend … Carbon Equivalent Trend"); trivial once US1 series-building exists.

**Independent Test**: Each trend chart renders with at least the last 90 days of data and clicking a point navigates to the source record's detail page.

**Acceptance Scenarios**:

1. **Given** data exists, **When** the dashboard loads, **Then** each of the six charts renders independently and an empty chart never breaks the others.
2. **Given** a DN filter applied, **When** charts re-render, **Then** all six reflect the same filter set.

---

### User Story 4 - Cycle / stage / waiting time analytics (Priority: P2)

A production manager opens **Reports → Stage Cycle Time** and sees, per stage (from the DB-backed stage list), the average/min/max time pipes spend, waiting time between consecutive stages, and end-to-end lead time per pipe — with a bottleneck ranking (stages sorted by average dwell time).

**Why this priority**: Prompt asks for cycle/stage/waiting/queue time + bottleneck analysis; derivable from existing `PipeStage.stage_date`/`stage_time` — zero schema change.

**Independent Test**: For a pipe with known stage timestamps, the report's per-stage dwell times match manual subtraction; stages missing timestamps are excluded with a coverage note.

**Acceptance Scenarios**:

1. **Given** pipes with 2+ timestamped stages, **When** the report runs, **Then** inter-stage waiting times are computed in hours and aggregated per stage pair.
2. **Given** stages with missing date or time, **When** aggregating, **Then** those intervals are excluded and the coverage fraction is displayed.
3. **Given** the admin adds a new custom stage, **When** the report runs, **Then** it appears automatically (stage list is DB-driven).

---

### User Story 5 - Stage audit-trail report (Priority: P3)

An admin opens **Reports → Stage Audit** and sees every recorded stage change from `PipeStageHistory` — who changed what, when, old vs new decision, reason — filterable by pipe, stage, user, date. This surfaces data that is already written but never queried.

**Why this priority**: Cheap (read-only over an existing table) and answers the prompt's "Audit Reports" line.

**Independent Test**: A known HOLD-reset (or any recent stage edit) appears in the report with correct before/after values.

**Acceptance Scenarios**:

1. **Given** history rows exist, **When** filtering by pipe code, **Then** only that pipe's changes show, newest first.
2. **Given** the column/row picker (TableTools), **When** exporting, **Then** Excel contains exactly the selected columns/rows.

---

### User Story 6 - Weight-saving report fix (Priority: P3)

The existing weight-saving report stops depending on the always-zero `Pipe.iso_weight`: when `iso_weight <= 0`, it falls back to `Product.weight_kg` (via the pipe's product/order), and rows with neither are excluded with a coverage note instead of showing garbage negatives.

**Why this priority**: One-line-class fix for a report the client already sees; removes a known data trap.

**Independent Test**: A pipe with `iso_weight=0` and a product weight renders a sensible saving; a pipe with neither shows as excluded.

**Acceptance Scenarios**:

1. **Given** iso_weight=0 but Product.weight_kg set, **When** the report runs, **Then** saving = product_weight − actual_weight with a "via product standard" indicator.
2. **Given** both missing, **When** the report runs, **Then** the pipe is excluded and counted in the coverage note.

---

### Edge Cases

- Fewer than 2 points in any SPC series → "insufficient data", never divide-by-zero.
- All values identical (σ = 0) → limits collapse; show "no variation" state, not UCL=CL=infinity.
- Element_rules range gaps / float drift (open bug) → capability must reuse decision-service rule resolution verbatim.
- Case-variant decisions (`accept` vs `ACCEPT`) → P-chart proportions must compare case-insensitively.
- Stage timestamps where time exists but date is null (or vice versa) → interval excluded, counted in coverage.
- Permission matrix: new screens must default OFF for non-admin roles (fail-closed) and be grantable via the matrix.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an SPC report supporting I-MR and X̄-R charts over ACTIVE mechanical-test properties and chemical-analysis elements, with automatic control limits.
- **FR-002**: System MUST provide P charts (proportion non-conforming) over pipe/stage decisions and C charts (defect counts) over stage defects, aggregated by day/shift/machine/DN/order.
- **FR-003**: System MUST evaluate Western Electric / Nelson rules (at minimum: 1 point >3σ; 9 same-side; 6 trending; 2-of-3 >2σ; 4-of-5 >1σ; 15 within 1σ; 8 beyond 1σ both sides) and list violations with point references.
- **FR-004**: System MUST provide a capability report computing Cp/Cpk/Pp/Ppk/sigma/DPMO using spec limits sourced ONLY from element_rules (chemistry) and the decision-service tensile threshold; characteristics without limits MUST be reported as unavailable, never fabricated.
- **FR-005**: System MUST provide a lab-trends dashboard with trend charts for tensile, hardness, elongation, nodularity, carbon equivalent, and ferrite proxy, each linking to source records.
- **FR-006**: System MUST provide stage cycle-time analytics (per-stage dwell, inter-stage waiting, end-to-end lead time, bottleneck ranking) from existing stage timestamps, DB-driven stage list.
- **FR-007**: System MUST provide a stage audit-trail report over PipeStageHistory with pipe/stage/user/date filters and TableTools export.
- **FR-008**: Weight-saving report MUST fall back to Product.weight_kg when Pipe.iso_weight ≤ 0 and exclude rows with neither, showing coverage.
- **FR-009**: All new reports MUST reuse the unified filter set (parse_filters/apply_pipe_filters), the analytics base template, TableTools export, and Chart.js — consistent with existing reports.
- **FR-010**: All new routes MUST be gated by new permission-matrix keys (e.g. `reports.spc`, `reports.capability`) that fail closed and default to admin/super_admin only.
- **FR-011**: No changes to existing business logic, decision engine, or data-entry workflows (client's hard constraint).
- **FR-012**: No schema changes or migrations in this phase.

### Key Entities

- **SPC series**: ordered (date, value, subgroup-key, source-record-id) tuples built from MechanicalTest/ChemicalAnalysis — computed, not persisted.
- **Spec limits**: read from element_rules (chemical) and decision-service constants (tensile) — read-only reuse.
- **PipeStageHistory**: existing audit table, read-only source for US5.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every control chart's CL/UCL/LCL matches an independent hand calculation over the same filtered series (verified on prod data for ≥3 characteristics).
- **SC-002**: Cpk for carbon on a known grade matches textbook formula to 3 decimals.
- **SC-003**: All six lab-trend charts render with prod data with zero template/JS errors.
- **SC-004**: Cycle-time report reproduces manual per-pipe dwell times for ≥5 sampled pipes.
- **SC-005**: No existing report, decision flow, or data-entry screen changes behavior (regression suite passes).
- **SC-006**: New screens visible only to roles granted in the matrix; ungranted roles get 403.
