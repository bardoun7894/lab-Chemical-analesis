# Corrective Action (Cover) on Non-Conformance Register — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Cover" button + popup on every row of the Non-Conformance Register (`/reports/non-conformance`) so a manager can record a Root Cause, Corrective Action, Responsible person, Responsible Date, and Status against any non-conforming item, persist it, and see a status badge on the row at a glance.

**Architecture:** One new SQLite table `non_conformance_actions` keyed by `(source, source_code)`. One new POST endpoint `/reports/non-conformance/action/save` (CSRF-exempt, JSON). The existing GET endpoint loads all matching actions for the visible rows in one query and passes them to the template. One Bootstrap modal in the existing template, opened by per-row JS that pulls the action data from a JSON blob injected by the server.

**Tech Stack:** Flask, SQLAlchemy, SQLite, Jinja2, Bootstrap 5, vanilla JS fetch.

---

## Files Touched

**Create:**
- `app/models/nonconformance_action.py` — new `NonConformanceAction` model
- `tests/test_nonconformance_action.py` — unit tests for the model + route

**Modify:**
- `app/models/__init__.py` — export the new model
- `app/services/nonconformance_service.py` — add `load_actions(rows)` helper
- `app/routes/reports.py` — extend `non_conformance()` GET to load actions; add `non_conformance_action_save()` POST
- `app/templates/reports/non_conformance.html` — add Cover button + status badge + modal + small inline JS

No other files touched.

---

## Task 1: Add the `NonConformanceAction` model

**Files:**
- Create: `app/models/nonconformance_action.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_nonconformance_action.py`

- [ ] **Step 1: Write the failing model test**

Create `tests/test_nonconformance_action.py`:

```python
"""Unit tests for the NonConformanceAction model (cover action on the
Non-Conformance Register)."""
import unittest

from app import create_app, db
from app.models.nonconformance_action import NonConformanceAction


class NonConformanceActionModelTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_create_and_lookup(self):
        a = NonConformanceAction(
            source="chemical",
            source_code="L42",
            root_cause="Fe above upper limit",
            corrective_action="Add ferro-silicon; re-test.",
            responsible="A. Hassan",
            responsible_date=None,
            status="Open",
        )
        db.session.add(a)
        db.session.commit()

        got = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L42"
        ).first()
        self.assertIsNotNone(got)
        self.assertEqual(got.corrective_action, "Add ferro-silicon; re-test.")
        self.assertEqual(got.status, "Open")
        self.assertIsNotNone(got.created_at)
```

- [ ] **Step 2: Run the test — expect ImportError**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.models.nonconformance_action'`.

- [ ] **Step 3: Create the model**

Create `app/models/nonconformance_action.py`:

```python
"""
Corrective-action ("Cover") record attached to a non-conforming item on the
Non-Conformance Register.

The Register rolls up four different sources (chemistry, mechanical, stage,
pipe) into one list. To avoid coupling this feature to four different
underlying tables, the natural key is the same pair the Register already uses
to identify a row: ``(source, source_code)``.
"""
from datetime import datetime

from app import db


class NonConformanceAction(db.Model):
    """One corrective-action record per register row."""

    __tablename__ = "non_conformance_actions"

    source = db.Column(db.String(20), primary_key=True)
    source_code = db.Column(db.String(64), primary_key=True)

    root_cause = db.Column(db.Text, nullable=True)
    corrective_action = db.Column(db.Text, nullable=True)
    responsible = db.Column(db.String(120), nullable=True)
    responsible_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Open")

    created_by = db.Column(db.String(64), nullable=True)
    updated_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # The four statuses the manager can pick. Kept here so the model and the
    # UI agree; the route and the template import this constant.
    STATUSES = ("Open", "In Progress", "Done", "Cancelled")
    DEFAULT_STATUS = "Open"

    def to_dict(self):
        return {
            "source": self.source,
            "source_code": self.source_code,
            "root_cause": self.root_cause or "",
            "corrective_action": self.corrective_action or "",
            "responsible": self.responsible or "",
            "responsible_date": (
                self.responsible_date.isoformat() if self.responsible_date else ""
            ),
            "status": self.status or self.DEFAULT_STATUS,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<NonConformanceAction {self.source}:{self.source_code} {self.status}>"
```

- [ ] **Step 4: Register the model**

Edit `app/models/__init__.py`. Add the import alongside the existing audit import:

```python
from .nonconformance_action import NonConformanceAction
```

Add `NonConformanceAction` to the `__all__` list (place it next to `AuditLog` so the alphabetical-ish order is preserved).

- [ ] **Step 5: Run the test — expect PASS**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py -v
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git add app/models/nonconformance_action.py app/models/__init__.py tests/test_nonconformance_action.py && git commit -m "feat: add NonConformanceAction model for cover actions"
```

---

## Task 2: Add `load_actions()` helper to the service

**Files:**
- Modify: `app/services/nonconformance_service.py`
- Test: extend `tests/test_nonconformance_action.py`

- [ ] **Step 1: Add the failing helper test**

Append to `tests/test_nonconformance_action.py`:

```python
    def test_load_actions_for_rows(self):
        from app.services import nonconformance_service

        db.session.add(
            NonConformanceAction(
                source="chemical", source_code="L1", status="Done", responsible="x"
            )
        )
        db.session.add(
            NonConformanceAction(
                source="mechanical", source_code="P9", status="Open"
            )
        )
        db.session.commit()

        rows = [
            {"source": "chemical", "source_code": "L1"},
            {"source": "mechanical", "source_code": "P9"},
            {"source": "stage", "source_code": "P12"},  # no action
        ]
        out = nonconformance_service.load_actions(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[("chemical", "L1")].status, "Done")
        self.assertEqual(out[("mechanical", "P9")].to_dict()["responsible"], "")
        self.assertNotIn(("stage", "P12"), out)
```

- [ ] **Step 2: Run the test — expect AttributeError**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py -v
```

Expected: `AttributeError: module 'app.services.nonconformance_service' has no attribute 'load_actions'`.

- [ ] **Step 3: Add the helper**

In `app/services/nonconformance_service.py`, add at the end (before any final blank lines, after `export_columns`):

```python
def load_actions(rows):
    """Return ``{(source, source_code): NonConformanceAction}`` for the
    given register rows. Rows without an action are simply omitted — the
    caller treats a missing key as "no cover action yet"."""
    if not rows:
        return {}
    keys = {(r["source"], r["source_code"]) for r in rows}
    sources = {k[0] for k in keys}
    codes = {k[1] for k in keys}
    found = (
        NonConformanceAction.query.filter(
            NonConformanceAction.source.in_(sources),
            NonConformanceAction.source_code.in_(codes),
        ).all()
    )
    return {(a.source, a.source_code): a for a in found}
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git add app/services/nonconformance_service.py tests/test_nonconformance_action.py && git commit -m "feat: add load_actions helper for non-conformance register"
```

---

## Task 3: Wire actions into the GET route

**Files:**
- Modify: `app/routes/reports.py`

- [ ] **Step 1: Extend `non_conformance()` to load actions and pass them to the template**

In `app/routes/reports.py`, inside the existing `non_conformance()` function, between the `rows, summary = ...` line and the `if request.args.get("format") == "print":` line, add:

```python
    from app.models.nonconformance_action import NonConformanceAction

    actions = nonconformance_service.load_actions(rows)
```

Then in the `render_template(...)` call, add one line:

```python
        actions_by_key={k: v.to_dict() for k, v in actions.items()},
```

The full call becomes:

```python
    return render_template(
        "reports/non_conformance.html",
        rows=rows,
        summary=summary,
        filters=filters,
        stage_names=ProductionStage.active_names(),
        source_labels=nonconformance_service.SOURCE_LABELS,
        picker_columns=export_service.picker_meta(
            nonconformance_service.export_columns()
        ),
        is_reject=nonconformance_service.is_reject,
        actions_by_key={k: v.to_dict() for k, v in actions.items()},
    )
```

- [ ] **Step 2: Sanity-check import + render**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -c "from app.routes.reports import non_conformance; print('ok')"
```

Expected: `ok` (no error).

- [ ] **Step 3: Commit**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git add app/routes/reports.py && git commit -m "feat: load cover actions into non-conformance register view"
```

---

## Task 4: Add the save POST route

**Files:**
- Modify: `app/routes/reports.py`
- Test: extend `tests/test_nonconformance_action.py`

- [ ] **Step 1: Add the failing route test**

Append to `tests/test_nonconformance_action.py`:

```python
    def test_save_action_endpoint_upserts(self):
        from app.models.user import User

        u = User(username="qa", full_name="QA User", role="viewer")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(u.id)
            sess["_fresh"] = True

        # First save: creates a row
        r1 = self.client.post(
            "/reports/non-conformance/action/save",
            json={
                "source": "chemical",
                "source_code": "L7",
                "root_cause": "high Fe",
                "corrective_action": "add FeSi",
                "responsible": "Mona",
                "responsible_date": "2026-08-20",
                "status": "In Progress",
            },
        )
        self.assertEqual(r1.status_code, 200, r1.get_data(as_text=True))
        body = r1.get_json()
        self.assertEqual(body["status"], "In Progress")

        from app.models.nonconformance_action import NonConformanceAction

        a = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L7"
        ).first()
        self.assertIsNotNone(a)
        self.assertEqual(a.corrective_action, "add FeSi")

        # Second save with same key + new status: updates, does not duplicate
        r2 = self.client.post(
            "/reports/non-conformance/action/save",
            json={
                "source": "chemical",
                "source_code": "L7",
                "root_cause": "high Fe",
                "corrective_action": "add FeSi + re-test",
                "responsible": "Mona",
                "responsible_date": "2026-08-20",
                "status": "Done",
            },
        )
        self.assertEqual(r2.status_code, 200)
        all_rows = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L7"
        ).all()
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0].status, "Done")
        self.assertEqual(all_rows[0].corrective_action, "add FeSi + re-test")
```

- [ ] **Step 2: Run the test — expect 404**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py::NonConformanceActionModelTestCase::test_save_action_endpoint_upserts -v
```

Expected: 404 (route does not exist yet).

- [ ] **Step 3: Add the route**

In `app/routes/reports.py`, immediately after the `non_conformance()` function (before `non_conformance_export`), add:

```python
@reports_bp.route("/non-conformance/action/save", methods=["POST"])
@csrf.exempt
@login_required
@requires_permission("reports", "non_conformance")
def non_conformance_action_save():
    """Upsert one cover action for a non-conformance register row."""
    from datetime import datetime

    from app.models.nonconformance_action import NonConformanceAction

    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "").strip()
    source_code = (payload.get("source_code") or "").strip()
    if source not in ("chemical", "mechanical", "stage", "pipe") or not source_code:
        return jsonify({"error": "source and source_code are required"}), 400

    status_value = (payload.get("status") or NonConformanceAction.DEFAULT_STATUS).strip()
    if status_value not in NonConformanceAction.STATUSES:
        status_value = NonConformanceAction.DEFAULT_STATUS

    responsible_date = None
    rd = (payload.get("responsible_date") or "").strip()
    if rd:
        try:
            responsible_date = datetime.strptime(rd, "%Y-%m-%d").date()
        except ValueError:
            responsible_date = None

    action = NonConformanceAction.query.filter_by(
        source=source, source_code=source_code
    ).first()
    username = current_user.username if current_user.is_authenticated else None

    if action is None:
        action = NonConformanceAction(
            source=source,
            source_code=source_code,
            created_by=username,
        )
        db.session.add(action)

    action.root_cause = (payload.get("root_cause") or "").strip() or None
    action.corrective_action = (payload.get("corrective_action") or "").strip() or None
    action.responsible = (payload.get("responsible") or "").strip() or None
    action.responsible_date = responsible_date
    action.status = status_value
    action.updated_by = username

    db.session.commit()
    return jsonify({"success": True, "status": status_value, "action": action.to_dict()})
```

Add the missing imports at the top of `app/routes/reports.py` if they are not already imported. Specifically ensure these names are available near the top of the file:

```python
from flask import jsonify, request
from flask_login import current_user, login_required
```

`db` should already be importable — confirm with:

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && grep -n "^from app import db\|^from app.models" app/routes/reports.py | head -20
```

If `db` is not yet imported, add `from app import db` near the other top imports.

- [ ] **Step 4: Run the test — expect PASS**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/test_nonconformance_action.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git add app/routes/reports.py tests/test_nonconformance_action.py && git commit -m "feat: POST /reports/non-conformance/action/save upserts cover action"
```

---

## Task 5: Add the Cover button, badge, and modal to the template

**Files:**
- Modify: `app/templates/reports/non_conformance.html`

- [ ] **Step 1: Replace the row action cell**

In `app/templates/reports/non_conformance.html`, find the existing `<td class="text-nowrap d-print-hide">` that holds the **Act** button. Replace its contents (and only its contents — keep the `<td>` wrapper) with:

```jinja
                                {% set key = row.source ~ '||' ~ row.source_code %}
                                {% set action = actions_by_key.get((row.source, row.source_code)) %}
                                {% set status_colors = {'Open': 'secondary', 'In Progress': 'warning', 'Done': 'success', 'Cancelled': 'dark'} %}
                                <div class="d-inline-flex align-items-center gap-1">
                                    {% if action %}
                                    {% set s_color = status_colors.get(action.status, 'secondary') %}
                                    <span class="badge bg-{{ s_color }}"
                                          title="{{ action.responsible }}{% if action.responsible_date %} — {{ action.responsible_date }}{% endif %}"
                                          data-bs-toggle="tooltip">
                                        {{ action.status }}
                                    </span>
                                    {% endif %}
                                    <button type="button"
                                            class="btn btn-sm btn-outline-warning"
                                            data-bs-toggle="modal"
                                            data-bs-target="#coverModal"
                                            data-source="{{ row.source }}"
                                            data-source-code="{{ row.source_code }}"
                                            data-root-cause="{{ action.root_cause if action else '' }}"
                                            data-corrective-action="{{ action.corrective_action if action else '' }}"
                                            data-responsible="{{ action.responsible if action else '' }}"
                                            data-responsible-date="{{ action.responsible_date if action else '' }}"
                                            data-status="{{ action.status if action else 'Open' }}">
                                        <i class="bi bi-clipboard-check"></i>
                                        {{ 'تغطية' if is_ar else 'Cover' }}
                                    </button>
                                    <a href="{{ row.url }}" class="btn btn-sm btn-outline-primary">
                                        <i class="bi bi-box-arrow-up-right"></i>
                                        {{ 'اتخاذ إجراء' if is_ar else 'Act' }}
                                    </a>
                                </div>
```

- [ ] **Step 2: Append the modal + JS at the end of the template**

Just before the final `{% endblock %}` of `app/templates/reports/non_conformance.html`, append:

```jinja

<!-- Cover (Corrective Action) modal -->
<div class="modal fade" id="coverModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">
                    <i class="bi bi-clipboard-check"></i>
                    {{ 'إجراء تصحيحي (تغطية)' if is_ar else 'Corrective Action (Cover)' }}
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <form id="coverForm">
                    <input type="hidden" id="coverSource" name="source">
                    <input type="hidden" id="coverSourceCode" name="source_code">

                    <div class="mb-3">
                        <label class="form-label">
                            {{ 'السبب الجذري' if is_ar else 'Root Cause' }}
                        </label>
                        <textarea id="coverRootCause" name="root_cause"
                                  class="form-control" rows="3"></textarea>
                    </div>

                    <div class="mb-3">
                        <label class="form-label">
                            {{ 'الإجراء التصحيحي' if is_ar else 'Corrective Action' }}
                        </label>
                        <textarea id="coverCorrectiveAction" name="corrective_action"
                                  class="form-control" rows="3"></textarea>
                    </div>

                    <div class="row g-2">
                        <div class="col-md-6 mb-3">
                            <label class="form-label">
                                {{ 'المسؤول عن الإجراء' if is_ar else 'Corrective Action Responsible' }}
                            </label>
                            <input type="text" id="coverResponsible" name="responsible"
                                   class="form-control" maxlength="120">
                        </div>
                        <div class="col-md-6 mb-3">
                            <label class="form-label">
                                {{ 'تاريخ الاستحقاق' if is_ar else 'Responsible Date' }}
                            </label>
                            <input type="date" id="coverResponsibleDate" name="responsible_date"
                                   class="form-control">
                        </div>
                    </div>

                    <div class="mb-3">
                        <label class="form-label">
                            {{ 'الحالة' if is_ar else 'Status' }}
                        </label>
                        <select id="coverStatus" name="status" class="form-select">
                            <option value="Open">{{ 'مفتوح' if is_ar else 'Open' }}</option>
                            <option value="In Progress">{{ 'قيد التنفيذ' if is_ar else 'In Progress' }}</option>
                            <option value="Done">{{ 'منتهي' if is_ar else 'Done' }}</option>
                            <option value="Cancelled">{{ 'ملغي' if is_ar else 'Cancelled' }}</option>
                        </select>
                    </div>
                </form>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
                    {{ 'إلغاء' if is_ar else 'Cancel' }}
                </button>
                <button type="button" class="btn btn-success" id="coverSaveBtn">
                    <i class="bi bi-check-lg"></i>
                    {{ 'حفظ' if is_ar else 'Save' }}
                </button>
            </div>
        </div>
    </div>
</div>

<script>
const COVER_CSRF = document.querySelector('meta[name="csrf-token"]')?.content || '';
const COVER_SAVE_URL = "{{ url_for('reports.non_conformance_action_save') }}";
const COVER_LOCALE_OK = {{ 'true' if is_ar else 'false' }};
const COVER_OK_TEXT = "{{ 'تم الحفظ' if is_ar else 'Saved' }}";
const COVER_ERR_TEXT = "{{ 'فشل الحفظ' if is_ar else 'Save failed' }}";

document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('coverModal');
    if (!modal) return;

    modal.addEventListener('show.bs.modal', function (event) {
        const btn = event.relatedTarget;
        document.getElementById('coverSource').value = btn.dataset.source || '';
        document.getElementById('coverSourceCode').value = btn.dataset.sourceCode || '';
        document.getElementById('coverRootCause').value = btn.dataset.rootCause || '';
        document.getElementById('coverCorrectiveAction').value = btn.dataset.correctiveAction || '';
        document.getElementById('coverResponsible').value = btn.dataset.responsible || '';
        document.getElementById('coverResponsibleDate').value = btn.dataset.responsibleDate || '';
        document.getElementById('coverStatus').value = btn.dataset.status || 'Open';
    });

    document.getElementById('coverSaveBtn').addEventListener('click', async function () {
        const payload = {
            source: document.getElementById('coverSource').value,
            source_code: document.getElementById('coverSourceCode').value,
            root_cause: document.getElementById('coverRootCause').value,
            corrective_action: document.getElementById('coverCorrectiveAction').value,
            responsible: document.getElementById('coverResponsible').value,
            responsible_date: document.getElementById('coverResponsibleDate').value,
            status: document.getElementById('coverStatus').value,
        };
        try {
            const resp = await fetch(COVER_SAVE_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': COVER_CSRF,
                },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) throw new Error(resp.status);
            // Reload so the row badge reflects the new status.
            location.reload();
        } catch (e) {
            alert(COVER_ERR_TEXT + ': ' + e);
        }
    });

    // Bootstrap tooltips (status badges)
    if (window.bootstrap && bootstrap.Tooltip) {
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
            new bootstrap.Tooltip(el);
        });
    }
});
</script>
```

- [ ] **Step 3: Smoke-render the page**

Boot the app and load `/reports/non-conformance` in a browser. Confirm:
- Each row now has a **Cover** button to the left of **Act**.
- If no action exists yet, only the Cover + Act buttons show.
- Clicking Cover opens the modal pre-filled with empty values.
- Saving reloads the page and the row now shows a small status badge to the left of Cover.

If the test app is running:

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && FLASK_CONFIG=development python main.py
```

then open `http://127.0.0.1:5000/reports/non-conformance`.

- [ ] **Step 4: Commit**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git add app/templates/reports/non_conformance.html && git commit -m "feat: add Cover button + modal + status badge to non-conformance register"
```

---

## Task 6: Run the full test suite

- [ ] **Step 1: Run all tests**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && python -m pytest tests/ -v
```

Expected: all existing tests still pass, plus the 3 new tests in `test_nonconformance_action.py`.

- [ ] **Step 2: Lint / typecheck if available**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && (command -v ruff >/dev/null && ruff check app tests) || echo "ruff not installed — skipping"
```

- [ ] **Step 3: Final commit if any incidental cleanup was made**

```bash
cd /Users/mohamedbardouni/projects/lab_chemical && git status
```

If anything is dirty, commit with a clear message.

---

## Self-Review

- **Spec coverage:**
  - AC1 (Cover button left of Act) → Task 5 Step 1
  - AC2 (5 fields in modal) → Task 5 Step 2
  - AC3 (pre-filled when action exists) → Task 5 Step 1 (data-* attrs) + Step 2 (modal `show.bs.modal` listener)
  - AC4 (save + refresh badge) → Task 4 + Task 5 Step 2 (`location.reload()`)
  - AC5 (status badge with tooltip) → Task 5 Step 1
  - AC6 (bilingual) → Task 5 Step 1 + Step 2 (`{% if is_ar %}`)
  - AC7 (same permission as view) → Task 4 (`@requires_permission("reports", "non_conformance")`)
  - AC8 (filters / export / Act / sources unchanged) → only the `<td>` body and an append at end of template are modified
- **Placeholders:** none.
- **Type consistency:** `NonConformanceAction.STATUSES` is the single source of truth; route validates against it; template options match.
