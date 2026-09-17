"""The storekeeper's receiving screen.

The warehouse needs to book finished pipes into the other (ERP) system and
record who bought each one. Doing that through the pipe edit form or the Stage
Console would mean granting `stages.edit`, which is all-or-nothing across every
quality decision, defect and measurement on the pipe — far more than a
storekeeper should hold.

So this is a separate, deliberately tiny surface: scan a barcode, see who the
pipe is, fill four fields, save. The narrowness is the point. This module never
reuses `edit_pipe` or `update_stage`, because both write every field the
request happens to carry; here the four columns are named in the code and a
request carrying anything else is simply ignored. The allowlist is the security
boundary, not the fact that the template renders no other inputs.
"""

from datetime import date

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required, current_user

from app import db, get_locale
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.services.permission_service import requires_permission
from app.services.pipe_selection_service import (
    dedupe_pipe_ids,
    lookup_pipes,
    parse_order_ids,
    pipes_for_orders,
)


warehouse_bp = Blueprint("warehouse", __name__)


# The only columns this blueprint may ever write.
RECEIVING_FIELDS = ("delivery_date", "sales_order", "delivery_customer",
                    "delivery_receipt")


def _warehouse_status(pipe):
    """Normalize quality state for receiving metadata and policy messages."""
    if pipe.lab_decision in ("REJECT", "BLOCKED") or pipe.final_decision_value == "REJECT":
        return "REJECT"
    if pipe.lab_decision == "HOLD" or pipe.final_decision_value == "HOLD":
        return "HOLD"
    if pipe.final_decision_value == "ACCEPT":
        return "ACCEPT"
    return "PENDING"


def _blocking_reason(pipe, is_ar):
    """Why this pipe cannot be received yet, or None.

    Mirrors the rule the Stage Console already enforces on a Delivery save
    (stages.update_stage): a pipe on HOLD is not shippable, and one whose
    final decision is not ACCEPT has not been released by quality. Checked
    here on the GET as well so the storekeeper is told before typing rather
    than after submitting.
    """
    status = _warehouse_status(pipe)
    if status == "REJECT":
        return ("هذه الماسورة مرفوضة ولا يمكن تسليمها" if is_ar
                else "This pipe is rejected and cannot be delivered")
    if status == "HOLD":
        return ("هذه الماسورة محجوزة — لا يمكن تسليمها" if is_ar
                else "This pipe is on hold and cannot be delivered")
    if status != "ACCEPT":
        current = pipe.final_decision_value or ("قيد الانتظار" if is_ar else "pending")
        return (f"لم تُعتمد الماسورة من الجودة بعد (الحالة: {current})" if is_ar
                else f"Not released by quality yet (currently: {current})")
    return None


def _delivery_details(pipe):
    """Return prior delivery values when normal receiving must leave them alone."""
    stage = pipe.get_stage(ProductionStage.name_for_code("delivery"))
    if stage is None:
        return None
    delivery_date = stage.delivery_date or stage.stage_date
    values = (delivery_date, stage.sales_order, stage.delivery_customer,
              stage.delivery_receipt)
    if not any(value not in (None, "") for value in values):
        return None
    return {
        "date": delivery_date.isoformat() if delivery_date else "",
        "sales_order": stage.sales_order or "",
        "customer": stage.delivery_customer or "",
        "receipt": stage.delivery_receipt or "",
    }


def _normal_receiving_reason(pipe, is_ar):
    delivery = _delivery_details(pipe)
    if delivery:
        values = [delivery["date"], delivery["receipt"], delivery["sales_order"],
                  delivery["customer"]]
        summary = " · ".join(value for value in values if value)
        prefix = "تم تسليمها من قبل" if is_ar else "Already delivered"
        return f"{prefix}: {summary}" if summary else prefix
    return _blocking_reason(pipe, is_ar)


def _receiving_values(form, is_ar):
    """Parse the four fields once. Returns (values, error).

    Parsed once and applied to every pipe on the load, so a whole truck cannot
    end up half-dated because the date was re-read per pipe.
    """
    raw_date = (form.get("delivery_date") or "").strip()
    delivery_date = None
    if raw_date:
        try:
            delivery_date = date.fromisoformat(raw_date)
        except ValueError:
            return None, ("تاريخ غير صالح" if is_ar else "Invalid delivery date")

    return {
        "delivery_date": delivery_date,
        "sales_order": (form.get("sales_order") or "").strip() or None,
        "delivery_customer": (form.get("delivery_customer") or "").strip() or None,
        "delivery_receipt": (form.get("delivery_receipt") or "").strip() or None,
    }, None


def _write_receiving(pipe, values):
    """Write the four allowlisted columns onto the pipe's Delivery row."""
    delivery_name = ProductionStage.name_for_code("delivery")
    stage = pipe.get_stage(delivery_name)
    if stage is None:
        stage = PipeStage(pipe_id=pipe.id, stage_name=delivery_name)
        db.session.add(stage)

    stage.delivery_date = values["delivery_date"]
    if values["delivery_date"] is not None:
        # The Delivery row's date IS the delivery date — same convention the
        # pipe form uses, so the Deliveries report keeps sorting correctly.
        stage.stage_date = values["delivery_date"]

    stage.sales_order = values["sales_order"]
    stage.delivery_customer = values["delivery_customer"]
    stage.delivery_receipt = values["delivery_receipt"]
    stage.updated_by_id = current_user.id
    return stage


MAX_MATCHES = 20


def _find_pipes(raw):
    """Resolve what the storekeeper typed or scanned. Returns (pipe, matches).

    Exact wins outright: a scanned warehouse barcode, then a pipe code, then
    the batch no. code — checked in that order because the barcode is the one
    that is guaranteed unique, and a numeric barcode could otherwise be read
    as something else.

    Anything that is not an exact hit is treated as a prefix worth listing.
    Reading "PIP1" off a label and getting a list of the pipes it could be is
    far more use than being told the code does not exist.
    """
    matches = lookup_pipes(raw)[:MAX_MATCHES]
    # One partial hit is not ambiguous — open it rather than making the
    # storekeeper click a list of one.
    if len(matches) == 1:
        return matches[0], []
    return None, matches


@warehouse_bp.route("/", methods=["GET", "POST"])
@login_required
@requires_permission("warehouse", "scan")
def scan():
    """Find a pipe by warehouse barcode or pipe code, and open its card.

    A handheld reader types the digits and usually sends Enter, so the page is
    a single autofocused box — but the same box takes a pipe code typed by
    hand, because not every pipe carries a barcode yet. Nothing found
    re-renders the page with a message rather than 404ing: a storekeeper
    scanning the wrong label should be told, not thrown to an error page.
    """
    is_ar = get_locale() == "ar"
    raw = (request.form.get("barcode") if request.method == "POST"
           else request.args.get("barcode")) or ""
    raw = raw.strip()
    matches = []
    not_found = False
    try:
        selected_order_ids = parse_order_ids(request.values.getlist("order_ids"))
    except ValueError:
        selected_order_ids = []
        flash("معرّف أمر إنتاج غير صالح" if is_ar else "Invalid production order", "error")

    orders = (
        ProductionOrder.query.order_by(ProductionOrder.order_number.desc())
        .limit(200)
        .all()
    )
    selection_by_id = {
        pipe.id: _pipe_json(pipe, is_ar)
        for pipe in pipes_for_orders(selected_order_ids)
    }

    if raw:
        # Deliberately not run through clean_barcode: a scan that fails
        # validation should report "no pipe carries this code", not lecture
        # the storekeeper about digits — they cannot change what the label
        # says, and a pipe code is not digits anyway.
        pipe, matches = _find_pipes(raw)
        if pipe is not None:
            row = _pipe_json(pipe, is_ar)
            row["selected"] = row["selectable"]
            selection_by_id[pipe.id] = row
        else:
            for match in matches:
                selection_by_id.setdefault(match.id, _pipe_json(match, is_ar))
        not_found = pipe is None and not matches

    return render_template(
        "warehouse/scan.html",
        scanned=raw,
        matches=matches,
        capped=len(matches) >= MAX_MATCHES,
        not_found=not_found,
        is_ar=is_ar,
        orders=orders,
        selected_order_ids=selected_order_ids,
        selection_rows=list(selection_by_id.values()),
        selection_lookup_hidden_fields=[
            ("order_ids", order_id) for order_id in selected_order_ids
        ],
    )


@warehouse_bp.route("/pipe/<int:id>", methods=["GET"])
@login_required
@requires_permission("warehouse", "scan")
def receive(id):
    """One pipe's receiving card: who it is, and the four fields to fill."""
    pipe = Pipe.query.get_or_404(id)
    is_ar = get_locale() == "ar"
    stage = pipe.get_stage(ProductionStage.name_for_code("delivery"))
    return render_template(
        "warehouse/receive.html",
        pipe=pipe,
        stage=stage,
        delivery=_delivery_details(pipe),
        blocked=_blocking_reason(pipe, is_ar),
        is_ar=is_ar,
    )


@warehouse_bp.route("/pipe/<int:id>", methods=["POST"])
@login_required
@requires_permission("warehouse", "receive")
def save(id):
    """Write the four delivery fields. Nothing else, whatever is posted.

    Field-level audit is automatic: PipeStage is registered with the
    SQLAlchemy audit listeners, so each change lands in AuditLog with its old
    and new value and the user who made it. Note PipeStageHistory carries no
    delivery columns, so AuditLog is the only trail for these four.
    """
    pipe = Pipe.query.filter_by(id=id).with_for_update().first_or_404()
    is_ar = get_locale() == "ar"

    if _delivery_details(pipe):
        flash(("تم تسليم هذه الماسورة من قبل. استخدم إجراء التصحيح الصريح."
               if is_ar else
               "This pipe was already delivered. Use the explicit correction action."),
              "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    blocked = _blocking_reason(pipe, is_ar)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    values, error = _receiving_values(request.form, is_ar)
    if error:
        flash(error, "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    _write_receiving(pipe, values)
    db.session.commit()
    flash("تم حفظ بيانات التسليم" if is_ar else "Delivery details saved", "success")
    return redirect(url_for("warehouse.receive", id=pipe.id))


@warehouse_bp.route("/pipe/<int:id>/correct", methods=["POST"])
@login_required
@requires_permission("warehouse", "receive")
def correct(id):
    """Explicitly correct an existing delivery, with a mandatory audit reason."""
    pipe = Pipe.query.filter_by(id=id).with_for_update().first_or_404()
    is_ar = get_locale() == "ar"
    if not _delivery_details(pipe):
        flash(("لا توجد بيانات تسليم لتصحيحها" if is_ar
               else "There is no existing delivery to correct"), "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    audit_reason = (request.form.get("audit_reason") or "").strip()
    if not audit_reason:
        flash(("سبب التصحيح مطلوب" if is_ar
               else "A correction reason is required"), "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    values, error = _receiving_values(request.form, is_ar)
    if error:
        flash(error, "error")
        return redirect(url_for("warehouse.receive", id=pipe.id))

    _write_receiving(pipe, values)
    db.session.commit()
    flash(("تم تصحيح بيانات التسليم" if is_ar
           else "Delivery correction saved"), "success")
    return redirect(url_for("warehouse.receive", id=pipe.id))


def _pipe_json(pipe, is_ar):
    """One pipe as the scan list needs it: who it is, and whether it can go."""
    delivery = _delivery_details(pipe)
    reason = _normal_receiving_reason(pipe, is_ar)
    return {
        "id": pipe.id,
        "code": pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
        "no_code": pipe.no_code or "",
        "barcode": pipe.warehouse_barcode or "",
        "dn": f"DN{pipe.diameter}" if pipe.diameter else "",
        "pipe_class": pipe.pipe_class or "",
        "produced": (pipe.production_date.strftime("%Y-%m-%d")
                     if pipe.production_date else ""),
        "decision": pipe.final_decision_value or "",
        "status": "DELIVERED" if delivery else _warehouse_status(pipe),
        "reason": reason,
        "blocked": reason,
        "selectable": reason is None,
        "delivery": delivery,
        "order": (pipe.production_order.order_number
                  if pipe.production_order else ""),
        "url": url_for("warehouse.receive", id=pipe.id),
    }


@warehouse_bp.route("/lookup", methods=["GET"])
@login_required
@requires_permission("warehouse", "scan")
def lookup():
    """The same resolution `scan` does, without leaving the page.

    A truck is loaded one label at a time and booked out under a single sales
    order, customer and receipt. Making the storekeeper walk to a card and
    back for each of a hundred pipes would mean typing those three values a
    hundred times, so the scanning list stays put and only this answers.

    Deliberately the same `_find_pipes` the full-page scan uses — one
    resolution rule, so a barcode cannot mean one pipe here and another there.
    """
    is_ar = get_locale() == "ar"
    raw = (request.args.get("barcode") or "").strip()
    if not raw:
        return jsonify({"ok": False, "not_found": True, "matches": []})

    pipe, matches = _find_pipes(raw)
    if pipe is not None:
        return jsonify({"ok": True, "pipe": _pipe_json(pipe, is_ar)})

    return jsonify({
        "ok": False,
        "not_found": not matches,
        "capped": len(matches) >= MAX_MATCHES,
        "matches": [_pipe_json(p, is_ar) for p in matches],
    })


@warehouse_bp.route("/selection", methods=["GET"])
@login_required
@requires_permission("warehouse", "scan")
def selection():
    """Reload persisted basket IDs with current receiving metadata."""
    is_ar = get_locale() == "ar"
    try:
        ids = dedupe_pipe_ids(request.args.getlist("pipe_ids"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    rows = {pipe.id: pipe for pipe in Pipe.query.filter(Pipe.id.in_(ids)).all()}
    return jsonify({
        "pipes": [_pipe_json(rows[pipe_id], is_ar) for pipe_id in ids if pipe_id in rows],
        "missing_ids": [pipe_id for pipe_id in ids if pipe_id not in rows],
    })


@warehouse_bp.route("/batch", methods=["POST"])
@login_required
@requires_permission("warehouse", "receive")
def save_batch():
    """Write the same four values to every pipe on the scanned list.

    The quality gate is re-checked per pipe here rather than trusted from the
    scan: the list is assembled in the browser, and a pipe can be put on hold
    between the scan and the save. Any blocked pipe rejects the entire load
    and is named, so a partial truck is never recorded silently.
    """
    is_ar = get_locale() == "ar"

    try:
        ids = dedupe_pipe_ids(request.form.getlist("pipe_ids"))
    except ValueError:
        flash(("لم يتم الحفظ: معرّف ماسورة غير صالح" if is_ar
               else "Load not saved: invalid pipe identifier"), "error")
        return redirect(url_for("warehouse.scan"))

    if not ids:
        flash("لم يتم مسح أي ماسورة" if is_ar else "No pipes were scanned", "error")
        return redirect(url_for("warehouse.scan"))

    values, error = _receiving_values(request.form, is_ar)
    if error:
        flash(error, "error")
        return redirect(url_for("warehouse.scan"))

    loaded = (
        Pipe.query.filter(Pipe.id.in_(ids))
        .order_by(Pipe.id)
        .with_for_update()
        .all()
    )
    pipes = {pipe.id: pipe for pipe in loaded}
    missing = [pipe_id for pipe_id in ids if pipe_id not in pipes]
    if missing:
        names = ", ".join(f"#{pipe_id}" for pipe_id in missing)
        flash((f"لم يتم الحفظ: مواسير غير موجودة {names}" if is_ar
               else f"Load not saved: missing pipe(s) {names}"), "error")
        return redirect(url_for("warehouse.scan"))

    refused = []
    for pipe_id in ids:
        pipe = pipes.get(pipe_id)
        reason = _normal_receiving_reason(pipe, is_ar)
        if reason:
            refused.append(pipe.pipe_code or pipe.no_code or f"#{pipe.id}")

    if refused:
        names = "، ".join(refused) if is_ar else ", ".join(refused)
        flash((f"لم يتم الحفظ — راجع المواسير: {names}" if is_ar
               else f"Load not saved — pipe status changed or unavailable: {names}"),
              "error")
        return redirect(url_for("warehouse.scan"))

    for pipe_id in ids:
        pipe = pipes[pipe_id]
        _write_receiving(pipe, values)

    db.session.commit()
    saved = len(ids)
    flash((f"تم حفظ بيانات التسليم لـ {saved} ماسورة" if is_ar
           else f"Delivery details saved for {saved} pipe(s)"), "success")
    return redirect(url_for("warehouse.scan", saved=saved))
