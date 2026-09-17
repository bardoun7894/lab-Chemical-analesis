# Feature Specification: Stock by Stage & Warehouse Handover Note

**Branch**: `015-stock-and-handover`
**Created**: 2026-09-09
**Status**: Draft — awaiting client confirmation on OQ1–OQ4

## Source

Client WhatsApp messages, 2026-09-09 19:59–20:04. Five asks; three were already
covered or nearly covered and were handled outside this spec:

| # | Ask (verbatim) | Disposition |
|---|---|---|
| 1 | الشهادات الباتش والاستاندر | Already live — `/certificates`, MTC carries `batch_no` and a standard-limits / actual-results toggle |
| 2 | تقرير بالشخص اللي وافق و تقرير مقارنه بينهم | `/reports/approvals` existed; `/reports/approver-comparison` added 2026-09-09 |
| 3 | تقرير عن اسباب العيوب و علاقتها بالقطر والمكن والعيوب نفسها و المراحل | Covered by the BI crosstab (dimensions: reason, defect_type, dn, class, machine, mold, stage, shift) |
| 4 | محتاج اعرف عدد المواسير بالقطر والكلاس و اللي على كل مرحله و جاهزه للتوريد بالعدد والطول والوزن | **This spec, Part A** |
| 5 | تقرير تسليم للمخزن … السليم وكل تفاصيله و المرفوض وكل تفاصيله | **This spec, Part B** |

Groundwork already landed on `main` (2026-09-09) and is a prerequisite for both
parts:

- `pipes` (count) and `length` (m) are selectable crosstab measures. They were
  computed per cell but not exposed, so "how many pipes, how many metres" could
  not be answered on screen at all.
- `current_stage` is a crosstab dimension — the pipe's latest *decided* stage,
  as distinct from `stage`, which is every stage it passed through.
- Preset "Current stage × DN + Class".

## Part A — Stock by Stage (`/reports/stock-by-stage`)

### Goal

One screen that answers "how much is standing where, and how much can ship",
split by diameter and class, measured in pipes, metres and kilograms.

### User story

A production manager opens **Reports → Stock by Stage**. The page shows a
matrix: rows are DN × Class, columns are the production stages in canonical
order plus a final **Ready for delivery** column. Each cell shows pipe count,
total length in metres and total weight in kg. Row and column totals close.
He filters by date range, customer, or order and the matrix follows.

### Acceptance criteria

1. **Matrix shape** — rows DN × Class, columns = `ProductionStage.active_names()`
   in order, then `Ready for delivery`, then `Delivered`, then `Total`.
2. **One pipe, one column.** Every pipe is counted in exactly one column. A
   pipe that traversed eight stages contributes once, at its current stage.
   Column totals must sum to the grand total.
3. **Three measures per cell** — pipe count, total length (m, one decimal),
   total weight (kg, one decimal). Length uses
   `analytics_service.pipe_length` (Finish-stage measurement, else the product
   standard length); weight uses `Pipe.actual_weight`.
4. **Missing data is visible, not zero.** A pipe with no length source
   contributes 0 m but is counted in a per-column "no length data" coverage
   figure shown under the table. Same for weight. Silently printing a short
   total as if it were complete is the failure mode to avoid.
5. **Rejected pipes are excluded from Ready for delivery** and shown in their
   own column, not folded into the stage they were rejected at.
6. **Filters** — the standard analytics filter bar (date range, DN, class,
   customer, order, machine) via `analytics_service.parse_filters`.
7. **Export** — Excel via the generic `/reports/export/<name>.xlsx` path, and
   the Table Tools column/print picker like every other list page.
8. **Permission** — its own `reports.stock_by_stage` key, auto-granted to the
   non-admin roles through `_DEFAULT_REPORT_SCREENS`.

### The Ready-for-delivery rule — OQ1

A pipe is **ready for delivery** when all of:

- its effective decision (final, else lab) classifies as `accept`; and
- it carries a decided stage row for the last pre-Delivery stage
  (`ProductionStage.active_names()[-2]` today, i.e. the stage before Delivery); and
- it has **no** Delivery stage row.

**Open question OQ1**: is passing the last pre-Delivery stage the client's
definition of ready, or does he mean "accepted and not yet delivered" regardless
of how far down the line the pipe got? These give materially different numbers.
Confirm before building.

## Part B — Warehouse Handover Note (`/reports/handover-note`)

### Goal

The document that goes to the warehouse with a delivery: what is being handed
over, in full, with the rejected items listed separately and just as completely.

### User story

A shipping clerk picks a delivery — by receipt number, bundle, sales order, or
date — and prints the handover note. The note has two blocks: **سليم** and
**مرفوض**. Each block lists DN, class, every pipe number, and the block totals
(count, length, weight). The rejected block additionally carries the reject
reason per pipe. The warehouse signs one document that accounts for both.

### Acceptance criteria

1. **Two blocks, same columns** — accepted and rejected are formatted
   identically so the two can be read against each other. Suppressing detail on
   the rejected block is the current gap and is not acceptable.
2. **Per-pipe columns** — pipe number (`pipe_code` / `no_code`), DN, class,
   length (m), weight (kg), and for the rejected block the reject reason
   (`defect_reason`, else `reason`, else `defect_type`).
3. **Grouped subtotals** — rows grouped by DN × Class with a subtotal line
   (count, length, weight) per group, then a block total, then a grand total
   across both blocks.
4. **Selection** — by delivery receipt, bundle number, sales order, customer,
   or date range. Any combination narrows.
5. **Print layout** — A4 portrait, header carrying the delivery reference,
   date, customer, and a signature row (delivered by / received by). Reuse the
   existing print CSS conventions; no new `!important`.
6. **The reject side reuses `nonconformance_service`** rather than
   reimplementing "what counts as rejected" — one classifier, one answer.
7. **Existing `/reports/delivery-report` is superseded, not deleted.** It stays
   for now; the handover note is a separate route.
8. **Permission** — its own `reports.handover_note` key.

### Open questions

- **OQ2** — does a rejected pipe physically go to the warehouse (the client's
  "وهيتسلم مرفوضات" reads as yes), or is it listed for accounting only? Changes
  whether the rejected block counts toward the delivered totals.
- **OQ3** — is the note issued per delivery receipt, per bundle, or per truck?
  The data model has receipt and bundle; there is no truck concept.
- **OQ4** — should the note carry a serial of its own (like certificates do)
  and be stored, or is it a print-on-demand view over the delivery data?
  Storing it means a new table and a numbering scheme.

## Data realities that constrain both parts

Verified against the code on 2026-09-09; carried forward from the KB so the
plan does not rediscover them:

- `Pipe.iso_weight` is 0 on every production pipe — the standard weight comes
  from `analytics_service.planned_weight` (product standard), never from
  `iso_weight`.
- `final_decision_value` is filled on a small minority of pipes;
  `lab_decision` is the live signal. Use `bi_service._effective_decision`.
- `Pipe.machine_id` is always NULL; the machine lives on the CCM stage row.
- `PipeStage.shift_responsible` is 100% NULL; `Pipe.shift_engineer` is the only
  populated attribution field.
- Stage names are dynamic (`ProductionStage.active_names()`), and history rows
  keep retired names — see `analytics_service.stage_name_aliases()`.

## Out of scope

- Any change to how stages are entered or decided.
- Truck / logistics modelling.
- Replacing `/reports/delivery-report` or `/reports/delivery-overview`.
