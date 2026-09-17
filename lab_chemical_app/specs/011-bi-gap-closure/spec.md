# Feature Specification: BI Gap Closure (Analytics Phase 2)

**Feature Branch**: `011-bi-gap-closure`
**Created**: 2026-07-27
**Status**: Draft
**Input**: Gap analysis of DrAlaa's CCM_BI_Dashboard_v44.html vs the app (session 2026-07-27). The 7 panels his Excel dashboard has that the app lacks. Goal: he stops needing the Excel roundtrip.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - YTD comparison (Priority: P1)

A manager opens **Reports → YTD Comparison**, picks the current year and a compare year, and sees month-by-month production (count + actual weight + planned/ISO weight), reject count and reject %, plus a Reject % by stage × month matrix table (matching the layout in his Excel dashboard) and a stage reject-% comparison chart between the two years.

**Independent Test**: For months with known data, the table's reject % per stage matches manual counts of stage decisions (case-insensitive) for that month.

**Acceptance Scenarios**:
1. **Given** pipes across two years, **When** comparing, **Then** each month row shows both years' values and a delta.
2. **Given** a stage with zero decisions in a month, **When** rendering the matrix, **Then** the cell shows an empty/no-data marker, not 0%.
3. **Given** months with no data at all, **When** rendering, **Then** the report renders empty-safe.

### User Story 2 - Period compare panel with alerts (Priority: P1)

A quality engineer opens **Reports → Period Compare**, picks period A and period B (date ranges) and alert thresholds, and sees KPI cards (produced, weight, reject %, defect count, saving kg) with deltas, plus an alerts list of every metric that breached its threshold (e.g. reject % up more than +2pp).

**Independent Test**: With synthetic data where reject % rises 3pp between two periods, the alert fires; with default thresholds unchanged, no false alert on flat data.

**Acceptance Scenarios**:
1. **Given** both periods have data, **When** comparing, **Then** every KPI shows A, B, delta (absolute and %).
2. **Given** a metric breaching its threshold, **When** rendering, **Then** it appears in the alerts list with direction and magnitude.
3. **Given** an empty period, **When** comparing, **Then** the report shows a no-data state, never division-by-zero.

### User Story 3 - Stage funnel (Priority: P1)

A production manager opens **Reports → Stage Funnel** and sees, for the DB-driven stage list in canonical order, how many pipes reached each stage and the drop between consecutive stages — rendered as a funnel/bar visual plus a table.

**Independent Test**: With pipes recorded through known stages, funnel counts match direct PipeStage counts per stage name.

**Acceptance Scenarios**:
1. **Given** pipes at various stages, **When** rendering, **Then** each stage shows its pipe count and the drop from the previous stage (absolute + %).
2. **Given** a newly added custom stage, **When** rendering, **Then** it appears automatically (DB-driven list).
3. **Given** date/DN/order filters, **When** applied, **Then** funnel recomputes over the filtered pipes.

### User Story 4 - Defect heatmap DN × Pipe Class (Priority: P2)

A quality engineer opens **Reports → Defect Heatmap** and sees a matrix: rows = DN, columns = pipe class, cells = defect count (and optionally defect % of pipes in that cell), color-scaled from green to red, with a metric toggle (count / %).

**Independent Test**: Cell values match direct counts of defective PipeStages joined to pipes with that diameter/class.

**Acceptance Scenarios**:
1. **Given** defects spread across DN/class, **When** rendering, **Then** cell color intensity is proportional to the cell value relative to the max cell.
2. **Given** a cell with zero pipes, **When** rendering, **Then** it shows a no-data marker (distinct from zero defects).
3. **Given** the % metric selected, **Then** cells show defects ÷ pipes in that cell (never divide-by-zero).

### User Story 5 - Saving matrix DN × Pipe Class (Priority: P2)

A manager opens **Reports → Saving Matrix** and sees the cross-tab of planned weight (iso_weight with Product fallback per Phase 1), actual weight, saving kg and saving % by DN × class, with row/column totals.

**Independent Test**: A cell's saving matches per-pipe sums for pipes in that DN/class; pipes with neither weight source are excluded and counted in the coverage note.

**Acceptance Scenarios**:
1. **Given** pipes with product-standard weights, **When** rendering, **Then** cells aggregate correctly and totals reconcile with the weight-saving report.
2. **Given** a DN/class cell with no pipes, **Then** it renders empty, not 0 kg.

### User Story 6 - Data quality panel (Priority: P2)

An admin opens **Reports → Data Quality** and sees field-by-field fill rates across the operational tables (Pipe: actual_weight, iso_weight>0, product link, order link, shift_engineer, final decision; PipeStage: stage_date, stage_time, machine; per-stage breakdown), an overall completeness score, and the specific gaps ranked by impact.

**Independent Test**: On a seeded DB with known nulls, percentages match direct COUNT queries.

**Acceptance Scenarios**:
1. **Given** the prod data reality (stage_time ~1%), **When** rendering, **Then** the panel shows that honestly with a "blocks which report" note per gap.
2. **Given** all fields filled, **Then** score is 100% and no gaps listed.

### User Story 7 - Auto diagnosis + RCA hints (Priority: P3)

A manager opens **Reports → Auto Diagnosis** and sees a health score (0–100), a problems list detected by deterministic rules (e.g. reject % this month vs trailing 3-month average up >50%; one stage contributing >40% of rejects; defect type concentrated >60% on one machine; stage_time coverage <20%; >15% of pipes with no final decision), each with an RCA hint pointing at the most likely contributing dimension (stage/machine/DN/class), and a link to the report that drills in.

**Independent Test**: On synthetic data engineered to trigger a specific rule, that rule fires with the correct magnitude; on clean data the score is 100 with zero problems.

**Acceptance Scenarios**:
1. **Given** a reject spike driven by one stage, **When** diagnosing, **Then** the problem names that stage and its share.
2. **Given** rules that don't trigger, **Then** they don't appear (no noise).
3. **Given** an empty DB, **Then** the page renders a no-data state, score hidden.

## Edge Cases

- Mixed-case decisions (`accept`/`ACCEPT`) — all counts case-insensitive (project rule).
- Pipes with no product/order link — excluded from weight metrics, counted in coverage.
- Cells/months with zero denominators — no-data marker, never 0% or NaN.
- Stage list changes (custom stages) — all reports DB-driven via `ProductionStage.active_names()`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: YTD comparison report: monthly count/weight/planned/reject for two chosen years + Reject % stage × month matrix + year-over-year stage comparison chart.
- **FR-002**: Period compare report: arbitrary A/B date ranges, KPI deltas, user-settable alert thresholds, alerts list.
- **FR-003**: Stage funnel report over the DB-driven stage list with per-stage counts and consecutive drops, honoring the unified filters.
- **FR-004**: Defect heatmap DN × pipe class with count/% metric toggle and relative color scaling.
- **FR-005**: Saving matrix DN × pipe class reusing the Phase-1 `planned_weight()` fallback, with totals and coverage note.
- **FR-006**: Data quality panel with per-field fill rates, per-stage timestamp coverage, overall score, and gap→blocked-report mapping.
- **FR-007**: Auto diagnosis: deterministic rule engine (no AI calls), health score, problems + RCA hints + drill-in links. Rules must each have a unit test with a trigger and a no-trigger case.
- **FR-008**: All routes gated by the permission matrix (`reports.view` family, consistent with Phase 1 lab-trends/cycle-time/stage-audit), reuse `_analytics_base.html`, unified filters where meaningful, no new dependencies.
- **FR-009**: No schema changes; no changes to business logic, decision engine, or data-entry flows.

## Success Criteria *(mandatory)*

- **SC-001**: YTD reject % per stage/month matches manual counts for 3 sampled cells on prod data.
- **SC-002**: Period compare alerts fire exactly on threshold breach (unit-tested boundaries).
- **SC-003**: Funnel counts equal direct PipeStage counts on prod data.
- **SC-004**: Heatmap and saving-matrix cells reconcile with direct queries for sampled DN/class cells.
- **SC-005**: Data-quality percentages match direct COUNT queries on prod.
- **SC-006**: Every diagnosis rule has trigger + no-trigger tests; clean fixture scores 100.
- **SC-007**: Full regression suite passes; new routes 200 for granted roles, denied (302) for ungranted.
