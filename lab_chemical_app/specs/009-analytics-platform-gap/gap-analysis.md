# Gap Analysis — DrAlaa's Analytics Platform Prompt vs. Existing Reports

**Date**: 2026-07-27
**Source prompt**: DrAlaa via WhatsApp 2026-07-27 18:22 (AI-authored mega-prompt for a Manufacturing Analytics / BI / SPC / Capability / AI platform)
**Codebase audit**: full inventory of `app/routes/reports.py`, `app/routes/analytics.py`, `analytics_service.py`, `export_service.py`, models, templates
**Data realities**: verified against prod DB (see KB `analytics-data-realities`, `iso-weight-data-gap`, `shift-responsible-attribution-gap`)

Legend: ✅ exists · 🟡 partial / exists in weaker form · 🔴 missing but feasible with current data · ⛔ impossible without new data collection

---

## 1. The 12 Dashboards

| # | Dashboard | Status | Notes |
|---|-----------|--------|-------|
| 1 | Executive | 🟡 | `management_dashboard` exists (KPIs, top defects, best machine, worst stage) + dashboard designer (`/analytics/dashboards`). Needs polish, not rebuild. |
| 2 | Production | 🟡 | `production_summary`, `daily_production`, `order_performance` exist. |
| 3 | Quality | 🟡 | `defect_analysis` (Pareto + matrices), `defect_summary`, `non_conformance` register exist. |
| 4 | Laboratory | 🟡 | `chemical_analysis` report + `mechanical_statistical` exist. Missing trend charts. |
| 5 | Traceability | 🟡 | `heat_traceability` (ladle → pipes → mech) exists. Missing completeness scoring + timeline. |
| 6 | Machine | 🟡 | `machine_performance` exists (fixed to use CCM stage machine). Counts only — no utilization/downtime (no data). |
| 7 | Shift | 🟡 | `shift_engineer` + `shift_engineer_comparison` exist. Attribution only via `Pipe.shift_engineer` (54% coverage). |
| 8 | Operator | 🔴 | No operator field on stages (`shift_responsible` 100% NULL). Feasible only as alias of shift engineer unless data entry changes. |
| 9 | Customer | 🟡 | `customer_production` exists. |
| 10 | Inventory | ⛔ | **No inventory data anywhere in the schema.** Needs a new module + data entry. |
| 11 | Maintenance (MTBF/MTTR) | ⛔ | **No downtime/maintenance/breakdown data.** DrAlaa himself flagged MTTR/MTBF as unrealistic — confirmed. Needs a downtime-log table + operator data entry. |
| 12 | AI Analytics | 🟡 | `ai_service.generate_report_summary()` already powers 3 report summaries + editable prompts exist. Expandable. |

## 2. KPI Center

Existing: KPI designer at `/analytics/kpis` with live formulas, thresholds, admin-managed (`kpis.json`) — the *framework* the prompt asks for is already there.

| KPI group | Status |
|---|---|
| Production / Yield / FPY / Overall Yield / Scrap / Rework / Accept / Reject rates | ✅ (`production_summary`, `rft_report`, `management_dashboard`) |
| OEE / Availability / Performance / Downtime | ⛔ needs machine runtime + downtime data |
| MTBF / MTTR | ⛔ DrAlaa flagged; confirmed impossible today |
| Cycle / Stage / Waiting / Queue / Lead time | 🔴 derivable from `PipeStage.stage_date/stage_time` + `Pipe.created` — no schema change, just new queries |
| Lab performance / Lab cycle time | 🔴 derivable (`ChemicalAnalysis.test_date` vs pipe/ladle creation) |
| Tensile / Hardness / Elongation / Nodularity / Ferrite / CE trends | 🔴 data all present on `MechanicalTest` + `ChemicalAnalysis`; needs trend charts (only snapshot stats today) |
| Customer complaints | ⛔ no complaints entity |
| NCR | ✅ `non_conformance` register |
| CAPA | ⛔ no CAPA entity |
| Operator productivity / efficiency | 🟡 only via `shift_engineer` (54% coverage) |
| Shift performance | ✅ |
| Inventory / Shipment statistics | ⛔ / 🟡 (0 delivery records in prod) |
| Traceability completeness | 🔴 computable from stage presence per pipe |

## 3. Report Library

~20 reports already exist covering: production, orders, customers, delivery, RFT, defects, traceability, mechanical (×2), chemical, stage/machine/mold performance, weight saving, annealing, shift engineer (×2), non-conformance, daily production.

| Prompt asks | Status |
|---|---|
| Executive / Production / Quality / Lab / Traceability / Mechanical / Chemical / Coating / Machine / Operator / Bundle reports | ✅/🟡 exist (coating & inspection = stage reports; bundle via `bundle_number`) |
| Hydro Test report | 🔴 `PipeStage.measurement_value/measurement_type` can carry it — needs a dedicated view |
| Audit report | 🟡 `PipeStageHistory` + `audit_service` exist but **no report queries them** — quick win |
| Maintenance / Inventory / Security / Customer complaints / CAPA reports | ⛔ no underlying data |
| AI reports | 🟡 3 AI summaries exist, expandable |

**Per-report features**: filtering ✅ (16-field unified set) · grouping ✅ · export ✅ (xlsx/PDF/CSV/print + column/row picker) · search 🟡 · sorting 🟡 · **drill down 🔴 · drill through 🔴 · scheduling ⛔ · editable layout 🟡 (column picker) · cloning 🔴**

## 4. SPC Module — 🔴 none today, but feasible

No control charts, no control limits, no Western Electric/Nelson rules anywhere. **But the measurement data exists:**

- **I-MR / X̄-R**: tensile_mpa, elongation, hardness, nodularity (`MechanicalTest`), all chemical elements (`ChemicalAnalysis`) — subgroup by ladle/day/machine ✅
- **P / NP charts**: accept/reject proportions per day/shift/machine ✅
- **C / U charts**: defect counts per pipe/stage (`has_defect`, defect types) ✅
- **Western Electric / Nelson rules**: pure computation on the series ✅
- **By machine / shift / product / DN / heat / ladle / order**: all joinable ✅

This is the single biggest *buildable* win in the prompt.

## 5. Process Capability — 🔴 none today, partially feasible

Cp/Cpk/Pp/Ppk need **spec limits**. We have them for:
- **Chemical elements** — `element_rules` ranges (the decision engine already uses them) → Cpk per element ✅
- **Tensile** — one-sided lower spec (MPa threshold in decision service) → Cpk ✅
- ⛔ hardness/elongation/nodularity limits not stored as rules — would need a small spec-limits config table (minor extension).

Cpm / sigma / Z / DPMO are arithmetic on top. Capability histograms/trends feasible.

## 6. Statistical Analysis

| Tool | Status |
|---|---|
| Descriptive stats | ✅ `stat_summary()` (avg/min/max/std) |
| Histogram | ✅ (tensile, 10-bin) |
| Pareto | ✅ (defect analysis) |
| Scatter | 🟡 (mechanical_properties chart data) |
| Box plot / Correlation matrix / Regression / Distribution fitting / Normality (Shapiro, Anderson-Darling) / Confidence intervals / ANOVA / Time series | 🔴 all feasible — pure Python (numpy/scipy) on existing measurements |

## 7. Quality Engineering Tools

Pareto ✅ · Run charts 🔴 feasible · Check sheets 🔴 feasible · Stratification 🟡 (group-by exists) · Fishbone / 5-Why 🔴 UI-only builds · **FMEA / Risk matrix / CAPA ⛔ — need new tables + data entry**

## 8. Manufacturing Analytics (traceability-derived)

Throughput ✅ · Machine/shift comparison ✅ · Scrap/rework analysis 🟡 · **Cycle/stage/waiting/queue time, bottleneck analysis, production timeline, product journey 🔴 — all derivable from existing `PipeStage` timestamps** (no schema change).

## 9. AI Analytics

Daily/weekly/monthly/exec summaries 🔴 feasible (ai_service + editable prompts infra exists) · Production/quality insights 🔴 feasible · Anomaly detection 🔴 feasible (stats-based) · Root-cause suggestions / CAPA recommendations 🟡 (LLM-generated, advisory) · **Predictive maintenance ⛔** (no maintenance data) · Predictive quality 🟡 (possible on chemistry→defect correlation, treat carefully)

## 10. Analytics Warehouse Layer

⛔ defer. SQLite + ~hundreds of pipes does not need a star schema/ETL. If performance ever hurts, summary tables + caching are enough. **Recommend telling DrAlaa this is over-engineering for current scale.**

## 11. Security

Role-based ✅ (permission matrix, module.screen) · dashboard/report/KPI/query permissions ✅ (analytics.* keys exist) · audit logging 🟡 (audit_service exists, reports don't surface it) · **row-level security 🔴** (not present; small user base — likely unnecessary).

---

## Minimal schema extensions recommended (per the prompt's own rule: don't fabricate, extend minimally)

1. **`machine_downtime` table** (machine, start, end, reason, category) → unlocks OEE, Availability, Downtime, MTBF, MTTR. *Requires operators to log downtime — process change, not just code.* (DrAlaa already senses this.)
2. **`capa` table** (NCR ref, root cause, action, owner, due, status) → unlocks CAPA reports.
3. **`customer_complaint` table** → unlocks complaints KPI.
4. **Spec-limits config for mechanical properties** (extend or reuse element-rules pattern) → unlocks full capability suite.
5. **No schema needed but data-entry fixes**: record deliveries (0 today), weigh pipes (`actual_weight` 40% missing), set `shift_engineer` (54% missing), use `Product.weight_kg` as ISO weight fallback instead of the always-0 `Pipe.iso_weight`.

## Proposed phasing (build order)

- **Phase 1 — SPC + Capability + Trends** (biggest feasible win, zero schema change): I-MR/X̄-R/P charts + Nelson rules on mechanical & chemical data; Cpk vs element_rules & tensile threshold; trend dashboards for the 6 lab properties; cycle/stage-time analytics from existing timestamps; audit-trail report on `PipeStageHistory`; weight-saving fix via `Product.weight_kg`.
- **Phase 2 — Stats & Quality tools**: box plots, correlation, regression, normality, run charts, fishbone/5-why UI, drill-down/drill-through on existing reports, expanded AI summaries (daily/weekly/monthly).
- **Phase 3 — New data modules** (needs DrAlaa's buy-in on data entry): downtime log → OEE/MTBF/MTTR; CAPA; complaints.
- **Phase 4 — optional/defer**: warehouse layer, report scheduling, semantic model designer, row-level security.

## Answer to DrAlaa's direct question

The prompt's *method* (extend, reuse, don't fabricate, minimal schema extension) is sound and matches the codebase. ~40% is already built, ~35% is buildable on existing data (SPC/capability/stats/trends are the real gap), ~25% needs new data collection he has to commit to (OEE/MTBF/MTTR/inventory/CAPA/complaints). His instinct on MTTR/MTBF was correct.
