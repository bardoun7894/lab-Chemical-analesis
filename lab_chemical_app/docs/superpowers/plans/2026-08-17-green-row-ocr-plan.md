# Green Highlighted Row OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach chemical lab-report OCR to select a green/pistachio highlighted result row while preserving the existing `AVG` row behavior.

**Architecture:** Keep the current image upload, provider dispatch, JSON schema, parser, range validation, and form auto-fill unchanged. Update only `EXTRACTION_PROMPT` in the shared OCR service and add unit tests that verify the prompt contract and parser compatibility without making network calls.

**Tech Stack:** Python, pytest, Gemini/OpenRouter vision prompt, existing `app.services.ocr_service` parser.

---

### Task 1: Add OCR regression tests

**Files:**
- Create: `tests/test_ocr_service.py`
- Test: `app/services/ocr_service.py`

- [ ] **Step 1: Write the failing prompt-contract test**

```python
from app.services.ocr_service import EXTRACTION_PROMPT


def test_extraction_prompt_supports_avg_and_green_highlighted_rows():
    prompt = EXTRACTION_PROMPT.lower()

    assert "avg" in prompt
    assert "green, light-green, or pistachio highlighted row" in prompt
    assert "authoritative result/average row" in prompt
    assert "otherwise use the avg/average row path above" in prompt
```

- [ ] **Step 2: Write the parser compatibility test**

```python
from app.services import ocr_service


def test_extract_elements_preserves_existing_json_mapping(monkeypatch):
    monkeypatch.setattr(
        ocr_service,
        "call_vision_api",
        lambda image_bytes, prompt, mime_type: (
            '{"carbon": 3.9569, "silicon": 2.2606, "magnesium": 0.0516, '
            '"copper": 0.0284, "chromium": 0.0311, "sulfur": 0.0147, '
            '"manganese": 0.1966, "phosphorus": 0.0161, "lead": 0.001, '
            '"aluminum": 0.0102}'
        ),
    )

    result = ocr_service.extract_elements_from_image(b"image-bytes", "report.png")

    assert result == {
        "carbon": 3.9569,
        "silicon": 2.2606,
        "magnesium": 0.0516,
        "copper": 0.0284,
        "chromium": 0.0311,
        "sulfur": 0.0147,
        "manganese": 0.1966,
        "phosphorus": 0.0161,
        "lead": 0.001,
        "aluminum": 0.0102,
    }
```

- [ ] **Step 3: Run the new tests and confirm the prompt test fails before implementation**

Run: `.venv/bin/python -m pytest tests/test_ocr_service.py -q`

Expected before the prompt change: the parser test passes and the prompt-contract test fails because the existing prompt does not mention pistachio/highlighted rows.

### Task 2: Update the shared extraction prompt

**Files:**
- Modify: `app/services/ocr_service.py:45-68`

- [ ] **Step 1: Add the colored-row instructions without changing the JSON schema**

Extend the existing `IMPORTANT RULES` section with:

```text
6. Some reports are spreadsheet-like and use color coding instead of an AVG label. If a green, light-green, or pistachio highlighted row is visible, treat that highlighted row as the authoritative result/average row, even if an AVG row is also present.
7. If no green highlighted row is present, use the AVG/average row path above. Read each requested value from the cell directly under its matching element header. Ignore other measurement rows, SD/standard-deviation rows, limits, and surrounding non-result rows.
8. When using the colored-row path, if the highlighted row is not visible or a cell value is not reliable, return null for that element.
```

Keep rules 1-5, the requested element list, and the exact JSON response format unchanged.

- [ ] **Step 2: Run the focused OCR tests**

Run: `.venv/bin/python -m pytest tests/test_ocr_service.py -q`

Expected: all OCR tests pass without network calls.

- [ ] **Step 3: Run the complete test suite**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: all tests pass; no OCR or chemical-form regression is introduced.

- [ ] **Step 4: Review the final diff**

Run: `git diff -- app/services/ocr_service.py tests/test_ocr_service.py`

Confirm that only the prompt text and focused tests changed; no provider, parser, database, or form behavior changed.

- [ ] **Step 5: Commit the implementation**

```bash
git add app/services/ocr_service.py tests/test_ocr_service.py
git commit -m "feat(ocr): recognize green highlighted lab result rows"
```
