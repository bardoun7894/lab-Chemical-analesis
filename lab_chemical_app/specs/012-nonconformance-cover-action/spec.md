# Feature Specification: Corrective Action (Cover) on Non-Conformance Register

**Branch**: `012-nonconformance-cover-action`
**Created**: 2026-08-17
**Status**: Draft

## Goal

Let a quality manager record a corrective action ("Cover") against any non-conforming item shown on the Non-Conformance Register (`/reports/non-conformance`), track its status over time, and follow up on open ones.

## User Story

A quality manager opens **Reports → Non-Conformance Register**. For each row that shows an out-of-tolerance, held, or rejected item, a new **Cover** button appears next to the existing **Act** button. Clicking **Cover** opens a popup with five fields (Root Cause, Corrective Action, Responsible, Responsible Date, Status). The manager fills them in and saves. The row then shows a small status badge so the list is scannable at a glance. On reload, opening **Cover** again shows the previously-saved values so the manager can update them as work progresses.

The manager does not need to navigate away from the register to log a corrective action. Existing deep-link to the source record (the **Act** button) stays unchanged.

## Acceptance Criteria

1. Every row in the register shows a **Cover** button immediately to the left of the **Act** button.
2. Clicking **Cover** opens a Bootstrap modal containing exactly five editable fields:
   - Root Cause (textarea)
   - Corrective Action (textarea)
   - Corrective Action Responsible (free-text name)
   - Responsible Date (date picker)
   - Status (select: `Open`, `In Progress`, `Done`, `Cancelled`)
3. If a corrective action was previously saved for that row, the modal opens pre-filled with those values; otherwise empty.
4. Saving the modal writes/updates the action and closes it. The page refreshes the row's status badge.
5. If an action exists, the row shows a small badge next to the **Cover** button with the current status (color-coded: `Open` = secondary, `In Progress` = warning, `Done` = success, `Cancelled` = dark). Hovering shows a tooltip with the assigned person's name and responsible date.
6. The popup, button label, and badge are bilingual (English / Arabic) — matching the rest of the register page.
7. Permission: anyone with the existing `reports.non_conformance` permission can create / edit cover actions.
8. Existing behaviour — filters, summary tiles, Excel export, the **Act** deep-link, and all four row sources (chemistry / mechanical / stage / pipe) — is unchanged.

## Data Model

One new SQLite table `non_conformance_action`:

| Column            | Type          | Notes                                            |
|-------------------|---------------|--------------------------------------------------|
| `source`          | varchar(20)   | `chemical` / `mechanical` / `stage` / `pipe`     |
| `source_code`     | varchar(64)   | matches `row.source_code` from the register      |
| `root_cause`      | text          | nullable                                         |
| `corrective_action` | text        | nullable                                         |
| `responsible`     | varchar(120)  | free-text name                                   |
| `responsible_date`| date          | nullable                                         |
| `status`          | varchar(20)   | `Open` / `In Progress` / `Done` / `Cancelled`    |
| `created_by`      | varchar(64)   | username from session                            |
| `updated_by`      | varchar(64)   | username from session                            |
| `created_at`      | datetime      | server default                                   |
| `updated_at`      | datetime      | on-update server default                         |

Primary key: composite `(source, source_code)`. One action per register row.

## Routes

| Method | Path                                              | Purpose                                              |
|--------|---------------------------------------------------|------------------------------------------------------|
| GET    | `/reports/non-conformance`                        | Existing — extended to also load all cover actions for the visible rows in one query and pass them to the template. |
| POST   | `/reports/non-conformance/action/save` (new)      | Upsert one cover action. Body: `source`, `source_code`, plus the 5 fields. Returns the saved row. |

## Files Touched

- `app/models/__init__.py` — register new `NonConformanceAction` model
- `app/models/nonconformance_action.py` (new) — the model
- `app/services/nonconformance_service.py` — add `load_actions(rows)` helper
- `app/routes/reports.py` — extend `non_conformance()` to load actions; add `non_conformance_action_save()` POST route
- `app/templates/reports/non_conformance.html` — add **Cover** button, status badge, modal
- DB migration — add the table (SQLite `CREATE TABLE` via `migration_service` or manual)

No other templates, services, routes, or models are modified.

## Out of Scope

- Notifications when status changes
- Per-status permission rules
- File attachments on the action
- Audit history of edits (only the latest values are stored)
- Showing actions in any other report
