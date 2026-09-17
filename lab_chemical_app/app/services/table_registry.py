"""Curated column registries for the Table Tools field picker.

Each registry is a list of ``(key, label, value_fn, default)`` tuples:

- ``key``     stable identifier sent in ``?cols=`` and matched to ``th[data-col]``
              on the list page (so a default column also toggles on screen).
- ``label``   header text shown in the picker, the ``.xlsx`` and the print view.
- ``value_fn` ``row -> cell value`` (``None`` renders blank).
- ``default`` whether the field is in the default export/print set (and the
              picker checkbox is checked by default).

Fields mirror the entity **detail pages** (all chemical elements, equivalents,
measurements, reasons, …) — deliberately excluding blobs, FK ids and redundant
day/month/year, so the picker and the file stay clean. Stages/pipes builds its
registry inline in the route because its element + per-stage columns need the
batched per-ladle / per-pipe data.
"""


def _d(v):
    return str(v) if v else ""


def chemical_columns():
    return [
        ("date", "Date", lambda a: _d(a.test_date), True),
        ("furnace", "Furnace", lambda a: a.furnace.furnace_code if a.furnace else "", True),
        ("ladle_no", "Ladle#", lambda a: a.ladle_no, True),
        ("ladle_id", "Ladle ID", lambda a: a.ladle_id or "", True),
        ("c", "C", lambda a: a.carbon, True),
        ("si", "Si", lambda a: a.silicon, True),
        ("mg", "Mg", lambda a: a.magnesium, True),
        ("s", "S", lambda a: a.sulfur, True),
        ("ce", "CE", lambda a: a.carbon_equivalent, True),
        ("decision", "Decision", lambda a: a.decision or "", True),
        # --- more fields (from the detail page) ---
        ("cu", "Cu", lambda a: a.copper, False),
        ("cr", "Cr", lambda a: a.chromium, False),
        ("mn", "Mn", lambda a: a.manganese, False),
        ("p", "P", lambda a: a.phosphorus, False),
        ("pb", "Pb", lambda a: a.lead, False),
        ("al", "Al", lambda a: a.aluminum, False),
        ("mne", "MnE", lambda a: a.manganese_equivalent, False),
        ("mge", "MgE", lambda a: a.magnesium_equivalent, False),
        ("reason", "Reason", lambda a: a.reason or "", False),
        ("engineer_notes", "Engineer Notes", lambda a: a.engineer_notes or "", False),
        ("notes", "Notes", lambda a: a.notes or "", False),
        ("defect_reason", "Defect Reason", lambda a: a.defect_reason or "", False),
        ("created_at", "Created", lambda a: _d(a.created_at), False),
    ]


def _tensile(t):
    if t.tensile_mpa is not None:
        return t.tensile_mpa
    if t.tensile_strength is not None:
        return t.tensile_strength * 9.8
    return ""


def mechanical_columns():
    return [
        ("date", "Date", lambda t: _d(t.test_date), True),
        ("pipe_code", "Pipe Code", lambda t: t.pipe_code or t.code or "", True),
        ("dn", "DN", lambda t: t.diameter, True),
        ("ladle_id", "Ladle ID", lambda t: t.ladle_id or "", True),
        ("tensile", "Tensile (MPa)", _tensile, True),
        ("elongation", "Elongation", lambda t: t.elongation, True),
        ("hardness", "Hardness", lambda t: t.hardness, True),
        ("nodularity", "Nodularity", lambda t: t.nodularity_percent, True),
        ("shift", "Shift", lambda t: t.shift, True),
        ("tester", "Tester", lambda t: t.tester_name or "", True),
        ("status", "Status", lambda t: t.status or "", True),
        ("decision", "Decision", lambda t: t.decision or "", True),
        # --- more fields (from the detail page) ---
        ("test_number", "Test #", lambda t: t.test_number, False),
        ("sample_thickness", "Sample Thk", lambda t: t.sample_thickness, False),
        ("d1", "D1", lambda t: t.d1, False),
        ("d2", "D2", lambda t: t.d2, False),
        ("d3", "D3", lambda t: t.d3, False),
        ("avg_dimension", "Avg Dim", lambda t: t.avg_dimension, False),
        ("original_length", "Orig Length", lambda t: t.original_length, False),
        ("final_length", "Final Length", lambda t: t.final_length, False),
        ("force_kgf", "Force (KgF)", lambda t: t.force_kgf, False),
        ("tensile_strength", "Tensile (KgF/mm2)", lambda t: t.tensile_strength, False),
        ("nodule_count", "Nodule Count", lambda t: t.nodule_count, False),
        ("microstructure", "Microstructure", lambda t: t.microstructure or "", False),
        ("percent_85", "%85", lambda t: t.percent_85, False),
        ("percent_70", "%70", lambda t: t.percent_70, False),
        ("percent_40", "%40", lambda t: t.percent_40, False),
        ("carbides", "Carbides", lambda t: t.carbides, False),
        ("retest_reason", "Retest Reason", lambda t: t.retest_reason or "", False),
        ("reason", "Reason", lambda t: t.reason or "", False),
        ("comments", "Comments", lambda t: t.comments or "", False),
        ("created_at", "Created", lambda t: _d(t.created_at), False),
    ]


def delivery_columns():
    return [
        ("pipe_code", "Pipe Code", lambda d: (d.pipe.pipe_code or d.pipe.no_code) if d.pipe else "", True),
        ("dn", "DN", lambda d: (d.pipe.diameter if d.pipe and d.pipe.diameter is not None else ""), True),
        ("sales_order", "Sales Order", lambda d: d.sales_order or "", True),
        ("customer", "Customer", lambda d: d.delivery_customer or "", True),
        ("receipt", "Delivery Receipt", lambda d: d.delivery_receipt or "", True),
        ("bundle", "Bundle#", lambda d: d.bundle_number or "", True),
        ("delivery_date", "Delivery Date", lambda d: _d(d.delivery_date), True),
        ("final", "Final Decision", lambda d: (d.pipe.final_decision_value or "") if d.pipe else "", True),
        # --- more fields ---
        ("pipe_class", "Class", lambda d: (d.pipe.pipe_class or "") if d.pipe else "", False),
        ("stage_date", "Stage Date", lambda d: _d(d.stage_date), False),
        ("shift_responsible", "Shift", lambda d: d.shift_responsible or "", False),
        ("thickness_socket", "Thk Socket", lambda d: d.thickness_socket, False),
        ("thickness_spigot", "Thk Spigot", lambda d: d.thickness_spigot, False),
        ("notes", "Notes", lambda d: d.notes or "", False),
        ("created_at", "Created", lambda d: _d(d.created_at), False),
    ]


def order_columns():
    return [
        ("order_no", "Order #", lambda o: o.order_number, True),
        ("customer", "Customer", lambda o: o.customer_name or "", True),
        ("diameter", "DN", lambda o: o.diameter, True),
        ("target_quantity", "Target Qty", lambda o: o.target_quantity, True),
        ("produced", "Produced", lambda o: o.produced_quantity, True),
        ("status", "Status", lambda o: o.status or "", True),
        ("order_date", "Order Date", lambda o: _d(o.order_date), True),
        # --- more fields ---
        ("customer_code", "Customer Code", lambda o: o.customer_code or "", False),
        ("sales_number", "Sales #", lambda o: o.sales_number or "", False),
        ("pipe_class", "Class", lambda o: o.pipe_class or "", False),
        ("product_code", "Product Code", lambda o: o.product_code or "", False),
        ("product_description", "Product Desc", lambda o: o.product_description or "", False),
        ("product_weight", "Product Weight", lambda o: o.product_weight, False),
        ("product_length", "Product Length", lambda o: o.product_length, False),
        ("priority", "Priority", lambda o: o.priority or "", False),
        ("start_date", "Start Date", lambda o: _d(o.start_date), False),
        ("expected_end_date", "Expected End", lambda o: _d(o.expected_end_date), False),
        ("actual_end_date", "Actual End", lambda o: _d(o.actual_end_date), False),
        ("notes", "Notes", lambda o: o.notes or "", False),
        ("created_at", "Created", lambda o: _d(o.created_at), False),
    ]
