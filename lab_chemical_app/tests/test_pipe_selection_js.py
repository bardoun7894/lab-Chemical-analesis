"""Source-level guards for selector persistence when no browser runner is available."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_warehouse_persistence_stores_ids_and_reconciles_server_metadata():
    source = (ROOT / "app/static/js/pipe_selection.js").read_text(encoding="utf-8")

    assert "data-selection-reconcile-url" in (
        ROOT / "app/templates/shared/_pipe_selection.html"
    ).read_text(encoding="utf-8")
    assert "basket.map(function (pipe) { return pipe.id; })" in source
    assert "sessionStorage.removeItem(storageKey)" in source
    assert "reconcileUrl" in source
    assert "pipe.selectable !== false" in source


def test_server_selected_rows_render_without_javascript():
    partial = (
        ROOT / "app/templates/shared/_pipe_selection.html"
    ).read_text(encoding="utf-8")

    assert "data-server-selected" in partial
    assert 'form="{{ selection_target_form }}"' in partial
