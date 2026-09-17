# Tasks — Spec 007: Complete Permissions Matrix

## Task 1: Update seed_default_permissions() in permission.py
**Module:** Backend
**File:** `app/models/permission.py`
**Description:** Add missing screens to `modules` and `role_defaults` dictionaries
- Add `stages: ['delivery', 'shift_dashboard']`
- Add `reports: ['daily_production', 'chemical_analysis', 'defect_summary']`
- Add `stickers: ['batch']`
- Add `orders: ['progress', 'print_stickers']`
- Add `analytics: ['pivot', 'kpis', 'dashboards', 'query_manager', 'import']`
- Add `chatbot: ['view', 'send']`
- Add `main: ['dashboard', 'attachments']`
- Add to admin: `products, product_parameters, molds, defect_types, decision_types, element_rules, customers, machines, email_settings, ai_settings, sticker_settings, mechanical_rules`
- Update role_defaults for each new screen

## Task 2: Run sync script on server
**Module:** DevOps
**Description:** Deploy updated permission.py, then SSH into the server and run a Python script that:
1. Creates missing Permission rows (no duplicates)
2. Assigns default RolePermission rows for new screens
3. Does NOT delete existing rows

## Task 3: Verify
**Module:** QA
**Description:** Load `/admin/permissions` and confirm all new screens appear in the matrix with correct role checkboxes toggled.
