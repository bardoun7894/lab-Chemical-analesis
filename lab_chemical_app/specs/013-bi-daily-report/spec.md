# Feature Specification: v52-Style Daily Report in BI Dashboard

**Branch**: `013-bi-daily-report`
**Created**: 2026-08-17
**Status**: Approved

## Goal

Reproduce the daily report panel of the standalone `CCM_BI_Dashboard_v52.html`
(lines 1490–1516 UI, 5689–6030 logic) inside the Flask app's BI dashboard page
(`/reports/bi-dashboard`), computed server-side from the database, with print
and PDF export.

## User Story

A quality manager opens **Reports → CCM BI Dashboard**. At the top, a
"📋 التقرير اليومي" card lets him pick a report date (default: last
production date) and press ⚡ توليد. The page renders the full daily report:
status banner, 4 KPI cards vs yesterday and week average, machine/supervisor/
shift tables, top rejection reasons, shift × DN × class × decision-maker
detail, auto executive summary, alerts, and signature row. He can print it or
download a server-generated PDF.

## Acceptance Criteria (full parity with v52)

1. **Header** — gradient header with title "📋 التقرير اليومي للإنتاج
   والجودة", factory line, Arabic + ISO date.
2. **Status banner** — `rejPct > 7` → "يحتاج تدخل فوري 🔴", `> 4` →
   "متابعة مستمرة 🟡", else "جيد ✅".
3. **KPI cards (4)** — إجمالي الإنتاج, المرفوض, معدل الرفض, Saving% — each
   with ▲/▼/→ change vs yesterday; reject% also shows 6-day week average.
4. **Machine table** — total/rejected/%/change vs yesterday, rows colored by
   the 4%/7% thresholds, sorted worst first.
5. **Supervisor table** — same columns, from `Pipe.shift_engineer`.
6. **Top-5 rejection reasons** — count + % of rejects, from
   `final_decision_reason` (blank/`nan` filtered).
7. **Shift table** — total/rejected/% + week-average footer (reject % and
   pipes/day).
8. **Shift × DN × class detail** — grouped rows per shift with DN, pipe
   class, decision-maker (top `approved_by` name + "+N"), total/rejected/%.
9. **Decision-maker hint** — when no stage has `approved_by`, show the ⚠️
   hint (adapted: names come from stage approvals, not Excel "مفتش 1").
10. **Auto executive summary** — Arabic one-paragraph text identical in
    structure to v52 (production, reject % vs yesterday, worst machine, top
    reason, saving) + 📋 copy button.
11. **Alerts** — machine ≥7% 🔴 / ≥4% 🟡, negative saving 🔴, total reject ≥7%
    🔴, top reason ≥5 occurrences ⚠️.
12. **Signature row** — مسؤول الإنتاج / مراقب الجودة / مدير التشغيل.
13. **Exports** — 🖨️ print (CSS-isolated) + 📄 server-side reportlab PDF
    download. Date defaults to latest `production_date` (v52 auto-pick).
14. **Bonus** — top-3 rejected molds table (computed in v52 but never
    rendered there).
15. **Permissions** — page under existing `reports.view`; PDF under
    `reports.view` + `reports.export` (same pattern as `spc.xlsx`).
16. Existing BI dashboard sections unchanged.

## Field Mapping (v52 → Flask)

| v52 column | Flask source |
|---|---|
| `القرار طرد` (1/2) | `final_decision_value` + `bi_service._is_final/_is_reject` |
| `Machine No.` | `analytics_service.pipe_machine_code(pipe)` |
| `Shift Supervisor.` | `pipe.shift_engineer` |
| `Shift` | `pipe.shift` |
| `DN` / pipe type | `pipe.diameter` / `pipe.pipe_class` |
| `ISO/Actual Weight` | `pipe.iso_weight` / `pipe.actual_weight` |
| `سبب الرفض` | `pipe.final_decision_reason` |
| mold | `pipe.mold_number` |
| `مفتش 1` | `PipeStage.approved_by` → `User.full_name` (fallback `full_name_ar`/`username`) |

## Data Slices (v52 logic, reports.py 5703–5722)

- **today** = pipes on the report date (auto-pick latest production date)
- **yesterday** = pipes on the previous *production* date (not calendar)
- **week average** = mean over the last 6 production dates before today;
  reject % is pooled (rejects/decided over the window), pipes/day rounded

## Deviations (intentional)

- PDF is a real server-side download (reportlab) instead of v52's
  `window.print` popup — same content.
- Denominators follow the project rule: reject % = rejects / **decided**
  pipes only (HOLD/WAITING excluded), matching bi_service conventions.
- Saving uses `iso_weight` (v52 parity), not `planned_weight`.

## Out of Scope

- The other v52 panels (analytics, SPC, AI, data quality) — BI dashboard
  already covers them.
- Editing/annotating the report.
