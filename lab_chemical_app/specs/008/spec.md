# Spec 008 — Enforce Permissions on Routes

## Problem
The permission matrix at `/admin/permissions` lets admins toggle checkboxes, but **no route in the application actually checks permissions**. The `@requires_permission` decorator exists in `permission_service.py` but is never imported or used.

Currently routes use:
- `@admin_required` — only blocks non-admin users from **admin** pages
- Direct role list checks like `User.role.in_(["operator", "supervisor", "admin"])` — hardcoded, ignores the permission matrix
- Nothing — some routes are completely open

## Goal
Apply `@requires_permission` to every route so the permission matrix actually controls access.

## Design Decisions
1. **Don't remove `@admin_required`** from admin routes — it's an additional safety layer
2. **Don't remove existing role checks** — add `@requires_permission` alongside them
3. For routes that already check `role in [...]`, replace that logic with `@requires_permission` where the screen matches
4. Non-admin blueprints (chemical, mechanical, stages, reports, stickers, orders) get `@requires_permission` per-action
5. Fallback: if no permission row exists for a module/screen, `has_permission` returns `True` (fail-open) — safe to deploy incrementally

## Files to Modify
- `app/routes/chemical.py`
- `app/routes/mechanical.py`
- `app/routes/stages.py`
- `app/routes/reports.py`
- `app/routes/orders.py`
- `app/routes/stickers.py`
