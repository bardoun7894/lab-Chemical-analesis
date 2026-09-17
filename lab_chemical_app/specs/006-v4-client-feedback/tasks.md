# 006 — V4 Client Feedback (GCP_QC_Full_Arabic_Development_Report V4)

Source: WhatsApp docx received 2026-07-11. Screenshots in `./screenshots/` (image1–15).
Status legend: [ ] pending · [~] partial/exists, verify · [x] done

## P0 — Bugs (client says "not working")

- [x] T01 §19 **Lab HOLD duplicates the pipe** — FIXED 2026-07-12: assign_mechanical_roles ran on every pipe save and reset resolved lab decisions back to WAITING (wiping mechanical HOLD results); added idempotency guard + no_code uniqueness check in edit_pipe + logged (not swallowed) failures. — on Register/Edit Pipe form (image4), a lab HOLD decision creates a duplicate pipe record. "Big problem."
- [x] T02 §40 **Permissions don't work correctly** — VERIFIED 2026-07-12: switches were never disabled (unchecked Bootstrap switches look grey/"disabled"); enforcement via requires_permission is real DB lookup. Added clarifying banner to the matrix. Remaining: fail-open for unseeded screens (see T31).
- [x] T03 §48 **Lab test result doesn't show on the pipe** — FIXED 2026-07-12: WAITING was correct-by-design (mechanical test required first) but the UI never said so nor offered a path; pipe detail now shows a "Mechanical Test Required" card with prefilled Add link + explicit "WAITING — mechanical test required" badge.
- [~] T04 §7 **Pipes List export** — PARTIAL 2026-07-12: added 11 chemical element columns (C..CE) + per-stage decision columns to export.xlsx (filters were already respected). Remaining: group_by mirroring + custom column-picker export. (image1) — Export must produce Excel of the current on-screen view respecting active filters (incl. all element analyses). Plus: custom export dialog to pick arbitrary DB columns.
- [~] T05 §17/§47 **AI prompt button doesn't work** — FIXED 2026-07-12: Google retired gemini-2.0-flash (404 on every call); switched all defaults + prod settings to gemini-2.5-flash, AI analysis verified live on prod. Remaining: extra providers (Ollama, LM Studio, DeepSeek, more Google models).
- [x] T06 §8 **Delivery Records screen doesn't work** — FIXED 2026-07-12: root cause was the pipe form silently discarding all Delivery fields (date/sales order/customer/receipt/bundle) — zero Delivery rows ever persisted. add() and edit_pipe() now save them; Delivery Records card added to Reports index. Verified E2E on prod.

## P1 — Decision integrity & audit

- [x] T07 §5/§15/§44 **Decision locking** — DONE 2026-07-12: (1) chemical decision change now requires supervisor/admin + written reason; (2) mechanical decision change (supersede) now requires supervisor/admin; (3) stage decisions locked in pipe edit form for non-supervisors (API route already had it). Operator-block verified live on prod.
- [ ] T08 §16 **Decision must consider ALL tests**, not a subset.
- [x] T09 §42 **Mechanical retest with history** — VERIFIED 2026-07-12: already implemented (§4.4 supersede: old test kept SUPERSEDED, new ACTIVE with mandatory retest reason); now also gated on supervisor via T07.
- [ ] T10 §43 **Change log** — record creator and last editor per record; visible. (Data exists: PipeStageHistory, created_by/updated_by columns — needs UI.)
- [x] T11 §20 **Delivery stage needs a decision** — FIXED 2026-07-12: Delivery row in pipe form now shows the decision dropdown (Accept/Refused/Hold from stage_decision_types) alongside Sales Order; persisted end-to-end. Verified on prod.

## P1 — Lab / Mechanical form UX (image3)

- [x] T12 §9 Pipe picker filters — already existed (search + DN/Ladle/Order/date/annealing server-side filters, 2026-06-10); verified.
- [x] T13 §10 — DONE 2026-07-12: sample pipes marked ⭐FIRST/LAST/ALL in the picker; selecting a pipe shows a master-data panel (DN, class, production order, ladle, order-in-ladle, sample badge). Verified on prod (105 pipes carry roles).
- [x] T14 §11–12 — DONE 2026-07-12: DN auto-fills into the form field; class/order/ladle displayed from pipe master data; Ladle ID auto-filled (read-only) from pipe code.
- [x] T15 §13–14 — DONE 2026-07-12: pipe order (#N) shown in picker options and panel; ladle filter surfaces all pipes of a ladle.
- [x] T16 §18 — already done (commit ac2b0c0: Ladle ID moved to top of add form above No. Code); verified in template.
- [x] T17 §25 — already exists (pipe-detail Mechanical Test card by ladle, 2026-06-29) + new 'Mechanical Test Required' card from T03; verified.

## P1 — Reports

- [x] T18 §23–24 — DONE 2026-07-12: pipe-detail stage table defect cell now shows defect TYPE badge + defect reason + decision reason (previously reason-only). Deployed.
- [x] T19 §26–27 **Full Pipe Traceability Export** — VERIFIED 2026-07-12: /reports/export/traceability-excel already emits 4 sheets (Pipes, Chemical Analyses, Mechanical Tests, Stages) and is the Export Data card target. Matches spec.
- [x] T20 §22/§45 — VERIFIED 2026-07-12: /reports/annealing-hourly groups pipes by entry hour (00–23) with pipe codes/ladle/DN/date/time + date-range filters. Matches spec.
- [~] T21 §46 Daily email reports — SCHEDULED 2026-07-12: host cron 18:05 daily runs send-daily-report (log: daily_report_cron.log). BLOCKED on client: SMTP server/credentials + recipients must be entered in Admin > Email Settings; until then it no-ops safely.
- [x] T22 §21 — DONE 2026-07-12: Annealing row in pipe form now has Temperature (°C) input; stored as measurement_value/type 'Temperature'; shows in detail Measurement column and exports. Verified E2E.

## P2 — Stickers (images 8–11; target design = image10)

- [x] T23 §32–34 — VERIFIED 2026-07-12: /stickers/bulk already filters by production order, ladle, sales order, customer, dates, final decision, and prints one batch PDF.
- [x] T24 §35 — DONE 2026-07-12: sticker Decision is now the pipe LAB decision as PASS / NOT PASS (client: "should be lab decision pass or not"). Was final_decision_value, which stays empty until every post-lab stage is recorded, so pipes that had passed the lab still printed PENDING. Mapping: ACCEPT→PASS, HOLD→NOT PASS (HOLD), REJECT/BLOCKED→NOT PASS, WAITING/none→PENDING; a downstream stage REJECT still overrides a lab PASS. Never the ladle chemical decision. Verified on prod (C0001→PASS, P101→NOT PASS (HOLD)).
- [x] T25 §3/§6/§36 — DONE 2026-07-12: Code128 barcode of pipe code added (python-barcode, pinned in requirements + Dockerfile); QR already opens /stages/<id> link with metadata fragment; logo + recycle symbol slots existed (uploadable in settings).
- [x] T26 §37–38 — DONE 2026-07-12: layout now matches target design (barcode left, big DIP title + bold product code centered, labeled bold field grid, website footer). ROOT CAUSE of 'inconsistent fonts': container had NO TrueType fonts — installed fonts-dejavu-core (also pinned in Dockerfile). New settings: Show Barcode toggle + Sticker Title text; sizes/DPI/colors/component toggles existed.

- [x] T26b Sticker padding/overflow — FIXED 2026-07-12: rewrote create_sticker_image() with a real layout engine. Was: absolute % offsets, no clipping → long product codes/descriptions/values bled past the border and the two columns collided (client PDF: "PO-20260610-00**DN**: 800"). Now: uniform padding, non-overlapping header/barcode/center bands, auto-shrink-to-fit for every value, 2-line wrap+ellipsis for description, grid height budgeted against the footer. Verified at all 4 sizes + client's Q0004 pipe on prod.

## P2 — Data & integration

- [ ] T27 §28 Import screen for historical pipe data.
- [ ] T28 §29 Query manager: ad-hoc DB extraction + export of DB tables.
- [ ] T29 §30 KPI / Dashboard designer screen.
- [ ] T30 §31 Power BI connectivity.

## P2 — Permissions model

- [ ] T31 §39 Per-screen/module permissions by department.
- [ ] T32 §41 Manager role distinct from Admin.

## P3 — New features

- [ ] T33 §2 Furnace machines + numbering. [~] `/admin/furnaces` just added — verify numbering.
- [ ] T34 §3 OCR camera scan of pipe number.
- [ ] T35 §49 Input screens redesign per mockups: HOT ZONE (image14: PO+CCM details+annealing process+CCM/annealing decisions) and Cold Zone (image15: 3-step wizard + delivery data).
