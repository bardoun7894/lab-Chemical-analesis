"""Shared helpers for column/row-selectable Excel exports.

Every list-page export route defines its columns as a list of
``(key, header, value_fn)`` tuples. These helpers let the same route honour a
``cols=`` query param (which columns to write) and an ``ids=`` query param
(which rows to write) without duplicating the xlsxwriter boilerplate.

When neither param is present the export behaves exactly as before: all
columns, all filtered rows. This keeps existing links/bookmarks working.
"""

from io import BytesIO

from flask import send_file


def select_columns(columns, cols_arg):
    """Filter ``(key, header, value_fn)`` column defs by a ``cols=`` CSV.

    Returns all columns in canonical order when ``cols_arg`` is empty or only
    contains unknown keys. Order always follows the canonical ``columns`` order,
    never the order given in ``cols_arg``.
    """
    if not cols_arg:
        return list(columns)
    wanted = {c.strip() for c in cols_arg.split(",") if c.strip()}
    if not wanted:
        return list(columns)
    subset = [col for col in columns if col[0] in wanted]
    # Guard against a cols= that matches nothing (e.g. stale keys) — fall back
    # to the full set so the user never gets an empty sheet.
    return subset or list(columns)


def resolve_columns(all_columns, cols_arg):
    """Pick columns from a registry ``[(key, label, value_fn, default_bool)]``.

    - ``cols=`` present  -> exactly those keys, in registry order.
    - ``cols=`` absent   -> the ``default``-flagged subset (each entity decides
      what its default export/print contains — e.g. stages keeps its full rich
      set, others default to the on-screen columns).
    Returns ``[(key, label, value_fn)]`` ready for :func:`build_xlsx` /
    :func:`build_print`. Never returns empty (falls back to the full registry).
    """
    if cols_arg:
        wanted = {c.strip() for c in cols_arg.split(",") if c.strip()}
        subset = [(k, l, f) for (k, l, f, _d) in all_columns if k in wanted]
        if subset:
            return subset
    default = [(k, l, f) for (k, l, f, d) in all_columns if d]
    return default or [(k, l, f) for (k, l, f, _d) in all_columns]


def picker_meta(all_columns):
    """Return ``[(key, label, default_bool)]`` for rendering the Columns picker."""
    return [(k, l, bool(d)) for (k, l, _f, d) in all_columns]


def build_print(title, columns, rows):
    """Render the shared printable HTML table for the selected columns/rows."""
    from flask import render_template

    headers = [label for (_k, label, _f) in columns]
    data = []
    for row in rows:
        cells = []
        for (_k, _l, fn) in columns:
            v = fn(row)
            cells.append("" if v is None else v)
        data.append(cells)
    return render_template(
        "components/print_table.html",
        title=title, headers=headers, data=data, count=len(rows),
    )


def parse_ids(ids_arg):
    """Parse an ``ids=`` CSV into a list of ints, or ``None`` when absent."""
    if not ids_arg:
        return None
    out = [int(tok) for tok in (t.strip() for t in ids_arg.split(",")) if tok.isdigit()]
    return out or None


def filter_ids(query, id_column, ids_arg):
    """Apply ``id_column.in_(ids)`` to ``query`` when ``ids=`` is present."""
    ids = parse_ids(ids_arg)
    if ids:
        query = query.filter(id_column.in_(ids))
    return query


def build_xlsx(sheet_name, columns, rows, download_name):
    """Build an in-memory ``.xlsx`` from selected column defs and rows.

    ``columns`` is the already-selected subset (see :func:`select_columns`).
    Each ``value_fn`` receives one row and returns the cell value; ``None`` is
    written as an empty string. Returns a Flask ``send_file`` response.
    """
    import xlsxwriter

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet(sheet_name)
    bold = wb.add_format({"bold": True, "bg_color": "#D3D3D3"})

    for c, (_key, header, _fn) in enumerate(columns):
        ws.write(0, c, header, bold)
    for r, row in enumerate(rows, start=1):
        for c, (_key, _header, fn) in enumerate(columns):
            val = fn(row)
            ws.write(r, c, val if val is not None else "")

    wb.close()
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=download_name,
    )
