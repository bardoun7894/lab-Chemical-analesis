# GCP QC Pipes Traceability — Full System Improvement Plan

> **Project Rename**: "Lab Chemical Analysis" → **"GCP QC Pipes Traceability"**
> **Date**: 2026-02-27
> **Status**: Planning Phase — No changes applied yet

---

## Milestone 1: Critical Bugs & Fixes (Priority: URGENT)

### Task 1.1 — Fix Daily Production Report Error
- **Problem**: Daily Production Report page throws an error and does not render.
- **Location**: `app/routes/reports.py`, `app/services/report_service.py`, `templates/reports/`
- **Plan**:
  1. Read the report route and identify the exception (likely missing query join or undefined variable).
  2. Check the Jinja template for references to undefined context variables.
  3. Verify the SQL query covers all required joins (Pipes → Stages → ProductionOrders).
  4. Add proper error handling with user-friendly messages.
  5. Test with empty data and with seeded data.

### Task 1.2 — Fix Defect Summary Report Error
- **Problem**: Defect Summary report fails similarly to Daily Production.
- **Location**: `app/routes/reports.py`
- **Plan**:
  1. Trace the defect summary route and query logic.
  2. Fix joins between DefectTypes, Pipes, and Stages.
  3. Ensure defect counts aggregate correctly.
  4. Test rendering with zero defects and with sample data.

### Task 1.3 — Fix "Defects This Week" Dashboard Card Link
- **Problem**: Dashboard card link for "Defects This Week" returns an error page.
- **Location**: `app/routes/main.py`, `templates/main/dashboard.html`
- **Plan**:
  1. Check the href on the dashboard card — likely points to a non-existent route.
  2. Either create the missing route or fix the URL to point to the correct defect report.

### Task 1.4 — Fix Stage Decision Types Screen
- **Problem**: Decisions don't display; adding a new decision returns Internal Server Error.
- **Location**: `app/routes/admin.py`, `app/models/`
- **Plan**:
  1. Read the admin route handling Stage Decision Types CRUD.
  2. Check the model for missing fields or constraint violations causing the 500 error.
  3. Fix the query that loads existing decisions (likely empty result due to wrong filter).
  4. Fix the create/save logic (check for missing foreign keys, unique constraints, or validation errors).
  5. Test list, create, edit, delete operations.

### Task 1.5 — Fix Machine Management — Missing Stages in Dropdown
- **Problem**: When adding a machine, the stage dropdown does not include all stages.
- **Location**: `app/routes/admin.py`, machine add form template
- **Plan**:
  1. Check how stages are loaded for the dropdown (hardcoded list vs DB query).
  2. Ensure all 10+ stages are present in the seed data or reference table.
  3. Update the query/list to include all stages.

### Task 1.6 — Fix AI Daily Summary (Shows Nothing)
- **Problem**: AI Daily Summary section on dashboard is blank.
- **Location**: `app/services/ai_service.py`, `app/routes/main.py`
- **Plan**:
  1. Check if Gemini API key is valid and API call succeeds.
  2. Add error handling — if API fails, show a fallback message instead of blank.
  3. Check the prompt sent to Gemini — ensure it includes actual production data.
  4. Fix the template to display the AI response or show "AI unavailable" gracefully.

### Task 1.7 — Fix AI Auto-Decision (Calculate Auto-Decision Not Working)
- **Problem**: The auto-decision calculation button does not produce results.
- **Location**: `app/services/ai_service.py`, `app/services/decision_service.py`
- **Plan**:
  1. Separate AI-based decision from rule-based decision — auto-decision should NOT depend on AI API.
  2. Ensure rule-based engine reads from `element_rules.json` and calculates correctly.
  3. AI should only provide commentary/notes, not the decision itself.
  4. Fix the route/JS that triggers the calculation.

### Task 1.8 — Fix Progress & Completed Status on Production Orders
- **Problem**: Progress bar shows nothing; orders never reach "Completed" status.
- **Location**: `app/routes/production_orders.py`, `app/models/production_order.py`
- **Plan**:
  1. Define progress calculation: `completed_pipes / total_ordered * 100`.
  2. A pipe is "completed" only when it has a **Final Decision = ACCEPT**.
  3. An order is "Completed" when all pipes reach final decision.
  4. Implement the progress calculation query.

### Task 1.9 — Fix Sticker Icon on Stage Tracking (Does Not Open)
- **Problem**: Clicking the sticker icon on each pipe in Stage Tracking does nothing.
- **Location**: `templates/stages/`, `app/routes/stickers.py`
- **Plan**:
  1. Check the link/button href — likely missing pipe_id parameter.
  2. Fix the URL generation to pass the correct pipe ID.
  3. Ensure the sticker route handles single-pipe sticker generation.

### Task 1.10 — Fix Export Data (Shows Only Chemical, Not Full Pipe)
- **Problem**: Excel export only contains chemical analysis data, not full pipe traceability.
- **Location**: `app/routes/reports.py`, `app/services/report_service.py`
- **Plan**:
  1. Redesign export to be "Full Pipe Traceability Export".
  2. Include: Pipe info → Production Order → Chemical Analysis → Mechanical Tests → All Stage statuses → Final Decision.
  3. One row per pipe with all columns.

---

## Milestone 2: App Rename & Branding

### Task 2.1 — Rename Application
- **Current**: "Lab Chemical Analysis"
- **New**: "GCP QC Pipes Traceability"
- **Plan**:
  1. Update `app/__init__.py` — app title config.
  2. Update all templates: `base.html` title, navbar brand, login page.
  3. Update `app_settings.json` if app name is stored there.
  4. Update Docker labels and compose service name.
  5. Update any email templates or report headers.

---

## Milestone 3: Product Configurator System (New Feature)

### Task 3.1 — Create Product Parameter Reference Tables
- **Plan**:
  1. Create new DB models for each parameter:
     - `ProductParamClass` (Class → code, name_en, name_ar)
     - `ProductParamDN` (DN/Diameter → code, name_en, name_ar)
     - `ProductParamZincType` (Zinc Type → code, name_en, name_ar)
     - `ProductParamLength` (Length → code, name_en, name_ar)
     - `ProductParamInternalFinish`
     - `ProductParamExternalFinish`
     - `ProductParamIntentUse`
     - `ProductParamProjectType`
  2. Or use a single generic table: `ProductParameter(id, param_type, code, name_en, name_ar, description_en, description_ar, sort_order)`.
  3. Seed with initial values from existing data.

### Task 3.2 — Create Product Configuration Admin Screen
- **Plan**:
  1. Admin screen to manage each parameter's values (CRUD).
  2. Tabbed or sectioned UI — one tab per parameter type.
  3. Each entry: Code, Display Name, EN Description, AR Description.

### Task 3.3 — Create Product Model & Auto-Code Generation
- **Plan**:
  1. New `Product` model with FK to each parameter.
  2. `product_code` = concatenation of selected parameter codes (auto-generated).
  3. `description_en` = concatenation of EN descriptions.
  4. `description_ar` = concatenation of AR descriptions.
  5. Also store: `weight_kg`, `length_m`, `wall_thickness`.
  6. Product code regenerates dynamically if parameter codes change.

### Task 3.4 — Create Product Admin Screen
- **Plan**:
  1. Screen with dropdowns for each parameter.
  2. On selection, auto-populate code and descriptions in real-time (JS).
  3. Save creates a Product record.
  4. List/Edit/Delete existing products.

### Task 3.5 — Integrate Product into Production Order
- **Plan**:
  1. Replace free-text fields (Diameter, Class, Weight, Length, Description) with a single Product dropdown.
  2. On product selection, auto-fill all related fields.
  3. Migrate existing orders to link to products (create products from existing data).

---

## Milestone 4: Customer Management (New Feature)

### Task 4.1 — Create Customer Model & Admin Screen
- **Plan**:
  1. New `Customer` model: `id, code, name_en, name_ar, is_active`.
  2. Admin screen: list, add, edit, deactivate customers.
  3. Seed from any existing customer data.

### Task 4.2 — Integrate Customer into Production Orders
- **Plan**:
  1. Replace free-text customer field with FK to Customer table.
  2. Dropdown selection in production order form.
  3. Migrate existing data.

---

## Milestone 5: Decision Engine Redesign (Core Logic)

### Task 5.1 — Redesign Chemical Decision Engine
- **Problem**: No clear final pipe decision; no link between chemical analysis and pipe status.
- **Plan**:
  1. Decision engine reads ladle chemical analysis result.
  2. Based on element rules JSON, assigns one of 4 ladle decisions:
     - `LAST_ONLY` — test last pipe only, generalize result.
     - `FIRST_LAST` — test first & last, generalize if both pass.
     - `FULL_100` — every pipe tested individually.
     - `REJECT` — all pipes blocked immediately.
  3. Store decision on the `ChemicalAnalysis` record (ladle level).
  4. Propagate to all pipes from that ladle.

### Task 5.2 — Implement Pipe Marking System (Stars)
- **Plan**:
  1. Add `mechanical_test_role` field to Pipe: `FIRST`, `LAST`, `ANY`, `ALL`.
  2. When ladle decision = `LAST_ONLY`: mark last pipe as `LAST`, others as `ANY`.
  3. When ladle decision = `FIRST_LAST`: mark first as `FIRST`, last as `LAST`, others as `ANY`.
  4. When ladle decision = `FULL_100`: mark all as `ALL`.
  5. When ladle decision = `REJECT`: mark all as `REJECTED` + block.
  6. Display star icon on marked pipes in the UI.

### Task 5.3 — Implement WAITING Status & Auto-Propagation
- **Plan**:
  1. Pipes with role `ANY` get lab decision = `WAITING`.
  2. When starred pipe's mechanical test completes:
     - **LAST_ONLY**: If PASS → all pipes ACCEPT. If FAIL → all REJECT + BLOCK.
     - **FIRST_LAST**:
       - Both PASS → all ACCEPT.
       - Both FAIL → all REJECT.
       - Mixed → passed pipe ACCEPT, failed pipe REJECT, others HOLD.
     - **FULL_100**: Each pipe's result is independent.
  3. Auto-propagation runs as a service after each mechanical test save.

### Task 5.4 — Implement Final Pipe Decision
- **Problem**: No final decision exists for a pipe.
- **Plan**:
  1. Add `final_decision` field to Pipe model: `ACCEPT`, `REJECT`, `HOLD`, `WAITING`, `BLOCKED`.
  2. Add `final_decision_reason` text field.
  3. Final decision is computed from:
     - Chemical analysis decision (ladle level).
     - Mechanical test result (pipe or generalized).
     - All stage completions.
  4. A pipe can only be "ACCEPT" if all stages passed and lab decision is ACCEPT.
  5. Display final decision prominently on pipe detail and stage tracking.
  6. Block stage progression if pipe is REJECTED or BLOCKED.

### Task 5.5 — Link Lab Stage Decision to Chemical + Mechanical
- **Problem**: Lab stage decision is independent; should combine chemical & mechanical.
- **Plan**:
  1. Lab stage decision = f(chemical_decision, mechanical_result).
  2. Cannot manually override unless user has special permission.
  3. Auto-calculate when either chemical or mechanical data changes.
  4. Remove manual Lab decision or make it override-only with audit log.

---

## Milestone 6: Mechanical Testing Redesign

### Task 6.1 — Implement Re-Test System
- **Plan**:
  1. Add `status` field to MechanicalTest: `ACTIVE`, `SUPERSEDED`.
  2. Add `retest_reason` text field.
  3. Add `superseded_by` FK (self-referencing).
  4. On re-test: create NEW record, mark old as `SUPERSEDED`, link them.
  5. Never delete or modify old test results.
  6. Re-trigger decision engine after each new test.

### Task 6.2 — Add Image Upload for Mechanical Tests
- **Plan**:
  1. Add two image fields: `image_as_polished`, `image_etched`.
  2. Store images in `static/uploads/mechanical/` with unique filenames.
  3. Add file upload fields to the mechanical test form.
  4. Display images in test detail view.
  5. Limit file size (e.g., 5MB per image).

### Task 6.3 — Fix Tensile Calculation
- **Formula**: `tensile_mpa = sigma_kgf_mm2 * 9.8`
- **Plan**:
  1. Verify input field is σ (KgF/mm²).
  2. Auto-calculate MPa on input (JS + backend validation).
  3. Display both values.

### Task 6.4 — Link Mechanical Decision to Pipe Status
- **Plan**:
  1. After mechanical test save → run decision engine.
  2. Update pipe's lab decision and final decision.
  3. Propagate to sibling pipes if applicable (LAST_ONLY / FIRST_LAST scenarios).

---

## Milestone 7: Stage Tracking Improvements

### Task 7.1 — Fix/Redesign Melting Ladle Stage
- **Problem**: Should not be a standalone stage; should be linked to ladle status.
- **Plan**:
  1. Option A: Remove "Melting Ladle" as a stage; ladle info comes from Chemical Analysis.
  2. Option B: Make it auto-populated from ladle data (read-only).
  3. Decision displayed = ladle chemical decision.

### Task 7.2 — Add Delivery Stage
- **Plan**:
  1. Add new stage: `DELIVERY`.
  2. Fields: Sales Order reference, Customer, Delivery Receipt number, Delivery Date, Bundle Number.
  3. Add to stage progression after FINISH.

### Task 7.3 — Add Thickness Dual Reading
- **Problem**: Only 1 thickness field; need Socket + Spigot readings.
- **Plan**:
  1. Add `thickness_socket` and `thickness_spigot` fields to the CCM/relevant stage.
  2. Update form to show two input fields.
  3. Migrate existing single value (decide which field it maps to).

### Task 7.4 — Add Mold Number to Admin Settings
- **Problem**: Mold Number dropdown has no admin screen to manage values.
- **Plan**:
  1. Create `Mold` reference model: `id, mold_number, is_active`.
  2. Admin CRUD screen for molds.
  3. Populate dropdown from DB.

### Task 7.5 — Add Stage Approval Tracking (Who Approved)
- **Problem**: Stage has entry date but no record of who approved.
- **Plan**:
  1. Add `approved_by` FK to User on PipeStage.
  2. Auto-set to `current_user` when stage decision is saved.
  3. Display in stage tracking UI.

### Task 7.6 — Auto-Detect Shift Based on Time
- **Plan**:
  1. Define shift hours in settings (e.g., Morning 6-14, Afternoon 14-22, Night 22-6).
  2. Auto-select shift on stage entry based on current server time.
  3. Allow manual override.

### Task 7.7 — Shift Production Responsible as Dropdown
- **Problem**: Free text field; should be selectable from a list.
- **Plan**:
  1. Use existing Engineers table or create a `ShiftResponsible` reference.
  2. Dropdown in stage forms populated from DB.
  3. Admin screen to manage the list.

### Task 7.8 — Add Pipe Navigation Arrows
- **Problem**: No way to navigate between pipes within the same production order.
- **Plan**:
  1. On pipe detail / stage tracking page, add Previous/Next arrows.
  2. Query pipes in same production order, ordered by pipe number.
  3. Navigate to adjacent pipe.

---

## Milestone 8: Filters Overhaul (All Screens)

### Task 8.1 — Define Standard Filter Set
- **Filters to implement across all list/report screens**:
  - Date Range (from — to)
  - Shift
  - Production Order
  - Sales Order
  - Customer
  - DN (Diameter)
  - Class
  - Product
  - Pipe Code
  - Line
  - Stage
  - Machine
  - Mold
  - Pipe Status / Final Decision
  - Responsible
  - Delivery Receipt
  - Bundle Number

### Task 8.2 — Add Filters to Pipe List
- **Plan**:
  1. Add filter bar above pipe list with dropdowns and date pickers.
  2. Implement server-side filtering with query parameters.
  3. Persist filter state in URL (bookmarkable).
  4. Show active filter count.

### Task 8.3 — Add Filters to Stage Tracking
- Same filter approach as pipe list.

### Task 8.4 — Add Filters to All Reports
- Apply standard filter set to every report page.

### Task 8.5 — Add Filters to Sticker Generation
- Filter pipes for bulk sticker printing by any standard filter.

### Task 8.6 — Show Basic Pipe Data on All Screens
- **Problem**: DN, Class, Product, Ladle ID not shown on pipe detail screens.
- **Plan**:
  1. On every screen that shows a pipe, display: DN, Class, Product Code, Ladle ID, Production Order.
  2. Pull from Product and ProductionOrder relations.

---

## Milestone 9: Reports Redesign

### Task 9.1 — Stage Performance Report
- **Plan**:
  1. Filters: all standard filters.
  2. Columns: Stage, Total Production, Accepted, Rejected, Hold, Accept%, Reject%, Hold%.
  3. Output modes: by Count (default), by Weight (default vs actual), by Length (default vs actual).
  4. Statistical indicators: Average, Max, Min, Std Deviation, Total Count.
  5. Charts: bar chart per stage, pie chart for accept/reject/hold.

### Task 9.2 — Machine Performance Report
- **Plan**:
  1. Same structure as Stage Performance but grouped by Machine.
  2. All standard filters.
  3. Charts: machine comparison bar chart.

### Task 9.3 — Mold Performance Report
- **Plan**:
  1. Same structure but grouped by Mold.
  2. All standard filters.

### Task 9.4 — Weight Savings Report
- **Plan**:
  1. Filters: all standard filters.
  2. Calculate: `saving = default_weight - actual_weight` per pipe.
  3. Aggregate by production order, product, period.
  4. Show total savings, average saving per pipe.

### Task 9.5 — Annealing Furnace Hourly Report
- **Plan**:
  1. Filter by date, shift, furnace.
  2. Group pipes by hour of entry into Annealing stage.
  3. Show pipe numbers, count per hour.
  4. Timeline/bar chart visualization.

### Task 9.6 — Mechanical Properties Report
- **Plan**:
  1. List mechanical test results with filters.
  2. Show: Pipe, Tensile (MPa & KgF/mm²), Elongation, Hardness, Nodularity, Images, Decision.
  3. Statistical summary: avg, min, max, std dev per property.
  4. Charts for property distributions.

### Task 9.7 — Daily Email Reports (Automated)
- **Plan**:
  1. Create a scheduled task (Flask CLI command or cron job / APScheduler).
  2. Generate daily production summary PDF.
  3. Send via SMTP to configured email list.
  4. Admin screen to manage recipient list and schedule.
  5. Add email config to settings (SMTP host, port, credentials).

### Task 9.8 — Add Charts/Visualizations to All Reports
- **Plan**:
  1. Use Chart.js (frontend) for interactive charts.
  2. Each report gets at least one chart: bar, pie, or line.
  3. Charts included in PDF exports using server-side rendering (matplotlib or reportlab).

---

## Milestone 10: Audit Trail & Change Log

### Task 10.1 — Create Audit Log Model
- **Plan**:
  1. New `AuditLog` model:
     - `id, table_name, record_id, action (CREATE/UPDATE/DELETE), field_name, old_value, new_value, reason, user_id, timestamp`.
  2. Index on `table_name + record_id` for fast lookups.

### Task 10.2 — Implement Auto-Logging on All Sensitive Models
- **Plan**:
  1. Use SQLAlchemy event listeners (`before_update`, `after_insert`, `before_delete`).
  2. Apply to: ChemicalAnalysis, MechanicalTest, Pipe, PipeStage, ProductionOrder, User.
  3. Capture old vs new values for every field change.
  4. Record current_user automatically.

### Task 10.3 — Add "Reason for Change" Prompt
- **Plan**:
  1. On edit forms for sensitive data, add a required "Reason" field.
  2. Store reason in audit log.
  3. Only required for updates, not creates.

### Task 10.4 — Audit Log Viewer Screen
- **Plan**:
  1. Admin screen to view audit logs.
  2. Filter by table, user, date range, action type.
  3. Show before/after comparison.
  4. Export to Excel.

### Task 10.5 — Track Created By / Modified By on All Records
- **Plan**:
  1. Add `created_by`, `created_at`, `modified_by`, `modified_at` to all major models.
  2. Auto-set via SQLAlchemy events or mixin class.
  3. Display on detail screens.

---

## Milestone 11: Permissions & Access Control Redesign

### Task 11.1 — Module/Screen/Action Level Permissions
- **Plan**:
  1. Create `Permission` model: `id, module, screen, action (view/create/edit/delete/approve)`.
  2. Create `RolePermission` junction table.
  3. Define modules: Chemical, Mechanical, Stages, Production Orders, Reports, Admin, Stickers, Chatbot.
  4. Middleware decorator: `@requires_permission('chemical', 'edit')`.

### Task 11.2 — Prevent Edit After Close
- **Plan**:
  1. Add `is_locked` flag to records (production orders, chemical analyses, etc.).
  2. Once final decision is made or order is completed, lock the record.
  3. Only users with `unlock` permission can edit locked records.
  4. Show lock icon in UI.

### Task 11.3 — Permission Admin Screen
- **Plan**:
  1. Matrix UI: roles (rows) × permissions (columns).
  2. Checkbox to grant/revoke.
  3. Predefined roles: Admin, Supervisor, Operator, Viewer.
  4. Custom roles supported.

---

## Milestone 12: Sticker & QR Code Redesign

### Task 12.1 — Show Final Decision on Sticker
- **Problem**: Currently shows ladle decision, not final pipe decision.
- **Plan**:
  1. Update sticker template to use `pipe.final_decision` instead of ladle decision.
  2. Display decision prominently with color coding.

### Task 12.2 — QR Code Links to Pipe Details Page
- **Plan**:
  1. QR encodes URL: `https://{domain}/pipes/{pipe_id}/details`.
  2. Create a public-facing pipe details page (or authenticated).
  3. Shows full stage tracking report when scanned.

### Task 12.3 — Add Barcode + Logo to Sticker
- **Plan**:
  1. Add company logo image field in sticker settings.
  2. Add barcode (Code128) in addition to QR code.
  3. Configurable placement.

### Task 12.4 — Sticker Design Configurator
- **Problem**: Font size, layout, spacing not configurable.
- **Plan**:
  1. Admin screen for sticker design settings:
     - Font sizes (title, body, code).
     - Element visibility toggles.
     - Color scheme.
     - Image/logo upload.
     - Layout preview.
  2. Store config in `app_settings.json` or DB.
  3. Live preview in admin screen.

### Task 12.5 — Bulk Sticker Printing with Filters
- **Plan**:
  1. Filter page: select pipes by production order, ladle, sales order, date range, etc.
  2. Generate all stickers in a single PDF.
  3. Print-optimized layout (multiple stickers per page).

---

## Milestone 13: Import / Export & Integration

### Task 13.1 — Import Historical Pipe Data
- **Plan**:
  1. Excel upload screen for bulk pipe import.
  2. Template Excel file for download.
  3. Validation before import (check required fields, duplicates).
  4. Preview import results before confirming.
  5. Map columns to fields.

### Task 13.2 — Import Customers & Sales Orders
- **Plan**:
  1. Excel import for customers (code, name).
  2. Excel import for sales orders (order number, customer, products, quantities).
  3. Validation and duplicate checking.

### Task 13.3 — Import Products
- **Plan**:
  1. Excel import for product definitions.
  2. Map to Product Configurator parameters.

### Task 13.4 — Full Pipe Traceability Export
- **Plan**:
  1. Export all pipe data in one Excel file:
     - Sheet 1: Pipe + Production Order + Product info.
     - Sheet 2: Chemical Analysis per ladle.
     - Sheet 3: Mechanical Tests per pipe.
     - Sheet 4: Stage tracking (all stages, dates, decisions, approvers).
  2. Also available as PDF report.
  3. Apply all standard filters before export.

### Task 13.5 — Database Query Manager
- **Plan**:
  1. Admin screen for running predefined queries against the database.
  2. Predefined query templates (e.g., "All pipes by customer", "Production by date range").
  3. Export results to Excel/CSV.
  4. Power BI friendly: expose a read-only REST API or provide direct DB connection instructions.

### Task 13.6 — REST API for ERP Integration
- **Plan**:
  1. Create `/api/v1/` blueprint.
  2. Endpoints:
     - `GET /api/v1/pipes` — list pipes with filters.
     - `GET /api/v1/pipes/{id}` — full pipe details.
     - `GET /api/v1/production-orders` — list orders.
     - `POST /api/v1/production-orders` — create order (from ERP).
     - `GET /api/v1/chemical-analyses` — list analyses.
     - `GET /api/v1/mechanical-tests` — list tests.
  3. API key authentication.
  4. JSON responses.
  5. Swagger/OpenAPI documentation.

### Task 13.7 — Power BI Integration Support
- **Plan**:
  1. Provide read-only API endpoints optimized for Power BI (flat JSON).
  2. Or: scheduled CSV/Excel export to a shared folder.
  3. Documentation for Power BI connection setup.

---

## Milestone 14: Document Attachments

### Task 14.1 — File Attachment System
- **Plan**:
  1. New `Attachment` model: `id, table_name, record_id, filename, filepath, file_type, uploaded_by, uploaded_at`.
  2. Upload directory: `static/uploads/attachments/`.
  3. Max file size: 10MB per file.
  4. Allowed types: PDF, JPG, PNG, XLSX, DOCX.

### Task 14.2 — Add Attachments to Chemical Analysis
- Attach lab reports, certificates, spectrometer output.

### Task 14.3 — Add Attachments to Mechanical Tests
- Attach test reports, images (in addition to the dedicated polished/etched images).

### Task 14.4 — Add Attachments to Stages
- Attach inspection photos, approval documents per stage.

---

## Milestone 15: Dashboard & KPI Indicators

### Task 15.1 — Production KPI Dashboard
- **Plan**:
  1. Cards: Total Production, Accepted, Rejected, Hold, RFT (Right First Time) %.
  2. Filter by date range, line, shift.
  3. Charts:
     - Production trend (line chart by day/week).
     - Accept/Reject/Hold pie chart.
     - Defect Pareto chart.
     - RFT by line/stage bar chart.
  4. Stage-level breakdown.
  5. Defect type breakdown.
  6. Scrap/reject rate by product, line, machine.

---

## Milestone 16: AI Service Fix & Enhancement

### Task 16.1 — Fix Gemini API Integration
- **Plan**:
  1. Move API key to `.env` file (remove from `app_settings.json`).
  2. Validate API key on startup.
  3. Add timeout and retry logic (max 2 retries).
  4. Graceful fallback: if AI unavailable, show "AI service unavailable" message.
  5. Update model name if needed (verify `gemini-3-pro-preview` is valid).

### Task 16.2 — AI Decision Report Enhancement
- **Problem**: AI doesn't show complete decision with reasons.
- **Plan**:
  1. Improve prompt to Gemini: include all element values, rules, and ask for structured output.
  2. AI response should include: Decision, Confidence, Reasoning per element, Recommendations.
  3. Display structured AI report on chemical analysis detail page.

---

## Milestone 17: Security Hardening

### Task 17.1 — Move Secrets to Environment Variables
- **Plan**:
  1. Move Gemini API key to `.env`.
  2. Move secret key to `.env`.
  3. Create `.env.example` template.
  4. Update `app_settings.json` to read from env vars.

### Task 17.2 — Remove Default Admin Password
- **Plan**:
  1. On first run, prompt for admin password or generate random one.
  2. Or require `ADMIN_PASSWORD` env var.
  3. Force password change on first login.

---

## Implementation Order (Recommended)

| Phase | Milestones | Estimated Scope |
|-------|-----------|-----------------|
| **Phase 1** | M1 (Critical Bugs), M2 (Rename), M17 (Security) | Fix what's broken first |
| **Phase 2** | M3 (Products), M4 (Customers) | Foundation data models |
| **Phase 3** | M5 (Decision Engine), M6 (Mechanical Redesign) | Core business logic |
| **Phase 4** | M7 (Stages), M8 (Filters) | UI/UX improvements |
| **Phase 5** | M10 (Audit), M11 (Permissions) | Governance & security |
| **Phase 6** | M9 (Reports), M15 (Dashboard) | Analytics & visibility |
| **Phase 7** | M12 (Stickers), M14 (Attachments) | Enhancements |
| **Phase 8** | M13 (Import/Export/API), M16 (AI) | Integration & AI |

---

## Notes

- Each task should be implemented with unit tests where applicable.
- Database migrations should use Flask-Migrate (Alembic) — needs to be added to the project.
- All UI changes must support Arabic/English bilingual interface.
- No existing data should be lost during migrations.
- Each milestone should be a separate git branch merged via PR.
