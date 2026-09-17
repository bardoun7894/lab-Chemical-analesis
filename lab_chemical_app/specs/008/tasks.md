# Tasks — Spec 008: Enforce Permissions on Routes

## Task 1: Add @requires_permission to chemical routes
**File:** `app/routes/chemical.py`
- [x] Import: `from app.services.permission_service import requires_permission`
- [x] Apply `@requires_permission('chemical', 'list')` to list routes
- [x] Apply `@requires_permission('chemical', 'add')` to add routes
- [x] Apply `@requires_permission('chemical', 'edit')` to edit routes
- [x] Apply `@requires_permission('chemical', 'delete')` to delete routes
- [x] Apply `@requires_permission('chemical', 'export')` to export routes
- [x] Apply `@requires_permission('chemical', 'auto_decide')` to auto-decide route

## Task 2: Add @requires_permission to mechanical routes
**File:** `app/routes/mechanical.py`
- [x] Import and apply per-action decorators (list, add, edit, delete, export, retest)

## Task 3: Add @requires_permission to stages routes
**File:** `app/routes/stages.py`
- [x] Import and apply per-action decorators (list, add, edit, delete, tracking, delivery, shift_dashboard)
- [x] Remove or keep alongside existing `User.role.in_([...])` checks inside the route body

## Task 4: Add @requires_permission to reports routes
**File:** `app/routes/reports.py`
- [x] Import and apply per-action decorators (list, view, export, daily_production, chemical_analysis, defect_summary)

## Task 5: Add @requires_permission to orders routes
**File:** `app/routes/orders.py`
- [x] Import and apply per-action decorators (list, add, edit, delete, progress, print_stickers)

## Task 6: Add @requires_permission to stickers routes
**File:** `app/routes/stickers.py`
- [x] Import and apply per-action decorators (list, generate, bulk_print, batch)

## Task 7: Deploy & Verify
**Actions:**
- [ ] SCP all modified files to server
- [ ] Rebuild Docker image
- [ ] Test: log in as operator, try to access a route where permission is unchecked in the matrix — should get "Access denied" flash
