# Spec 007 — Complete Permissions Matrix

## Goal
Add missing screens to the permission matrix at `/admin/permissions` so the page displays all available application screens and their role-based access.

## Background
The permission system at `app/models/permission.py` has a `seed_default_permissions()` function that defines modules and screens. The current list is incomplete:
- **stages**: missing `delivery`, `shift_dashboard`
- **reports**: missing `daily_production`, `chemical_analysis`, `defect_summary`
- **stickers**: missing `batch`
- **orders**: missing `progress`, `print_stickers`
- **analytics**: entire module missing (`pivot`, `kpis`, `dashboards`, `query_manager`, `import`)
- **chatbot**: entire module missing (`view`, `send`)
- **main**: entire module missing (`dashboard`, `attachments`)
- **admin**: missing `products`, `product_parameters`, `molds`, `defect_types`, `decision_types`, `element_rules`, `customers`, `machines`, `email_settings`, `ai_settings`, `sticker_settings`, `mechanical_rules`

## What's Needed
1. Update `modules` dict in `seed_default_permissions()` with all missing screens
2. Update `role_defaults` dict with appropriate role-to-permission assignments
3. Run a one-time sync script on the server to insert missing Permission rows and RolePermission rows **without** deleting existing ones
4. Deploy the code update so future seeds are complete
