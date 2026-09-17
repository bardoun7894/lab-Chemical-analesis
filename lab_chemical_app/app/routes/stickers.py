"""
Stickers Routes - QR Code and Label Printing
Professional GCP Ductile Iron Pipes Label Design
"""

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    flash,
    redirect,
    url_for,
    current_app,
)
from flask_login import login_required, current_user
from io import BytesIO
import qrcode
from PIL import Image, ImageDraw, ImageFont
import os
from app.services.permission_service import requires_permission
import json
from app import db
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder
from app.services.pipe_selection_service import (
    dedupe_pipe_ids,
    lookup_pipes,
    parse_order_ids,
)

stickers_bp = Blueprint("stickers", __name__)

# Path to app settings
APP_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app_settings.json"
)


def load_sticker_settings():
    """Load sticker settings from app_settings.json"""
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
            return settings.get("sticker", {})
    except Exception:
        return {}


def _ordered_stage_names(pipe):
    """Every stage this pipe answers to, in production order.

    `active_names()` is ordered by `sort_order`; a pipe can also carry rows
    for stages since retired, which have no place in that order and go last.
    Order matters here even though the verdict does not depend on it: the
    label reports WHICH stage a pipe is standing at, and "the first stage
    with no decision" is only a real answer if the walk is in sequence. The
    same walk over a set — which is what this used to be — names an
    arbitrary one.
    """
    from app.models.stage import ProductionStage

    names = list(ProductionStage.active_names())
    known = set(names)
    names.extend(sorted({s.stage_name for s in pipe.stages} - known))
    return names


def sticker_status(pipe):
    """The label's verdict and where the pipe is standing. Returns
    (verdict, stage_name or None, machine_label or None).

    The verdict is what it always was: PASS only if the pipe passed
    EVERYTHING through Finish. The lab decision (chemical + mechanical
    outcome for this pipe) is the first gate. After a lab ACCEPT, every
    stage — built-in AND custom (e.g. a client-added "curing") — must have a
    recorded accept decision:
      - any recorded Reject (any stage, incl. Delivery) -> NOT PASS
      - any recorded Hold / non-accept -> NOT PASS (HOLD)
      - a stage with no decision yet -> PENDING (pipe still in the pipeline)
    Delivery is the one exception: the sticker is printed BEFORE shipping,
    so a missing Delivery decision does not block PASS — but a recorded
    Delivery Hold/Reject still shows on the sticker.

    The stage is the one that explains the verdict: the stage that rejected
    it, the stage holding it, or the first stage still waiting on a
    decision. A PASS is standing nowhere, and a verdict that comes from the
    lab rather than the line has no stage either.

    The whole walk now completes before deciding, and a reject outranks a
    hold which outranks a pending. It used to return on whichever it met
    first while walking an unordered set, so a pipe carrying both a hold and
    a reject could label itself either way between two prints.
    """
    from app.models.pipe import PipeStage

    lab = pipe.lab_decision

    # A lab verdict is about the metal, not about a position on the line.
    if lab in ("REJECT", "BLOCKED"):
        return "NOT PASS", None, None
    if lab == "HOLD":
        return "NOT PASS (HOLD)", None, None
    if lab in (None, "", "WAITING", "FROZEN"):
        return "PENDING", None, None

    if pipe.final_decision_value == "REJECT":
        return "NOT PASS", None, None

    from app.models.stage import ProductionStage

    delivery_name = ProductionStage.name_for_code("delivery")
    rejected_at = held_at = waiting_at = None

    for stage_name in _ordered_stage_names(pipe):
        stage = pipe.get_stage(stage_name)
        decision = stage.decision if stage else None
        verdict = PipeStage.classify_decision(decision)

        if verdict == "reject":
            if rejected_at is None:
                rejected_at = stage_name
            continue

        if stage_name == delivery_name:
            # Printed before shipping, so a missing Delivery decision is not
            # a stall — but a recorded non-accept is still a problem.
            if decision and verdict != "accept" and held_at is None:
                held_at = stage_name
            continue

        if not decision:
            if waiting_at is None:
                waiting_at = stage_name
        elif verdict != "accept" and held_at is None:
            held_at = stage_name

    if rejected_at:
        return "NOT PASS", rejected_at, _stage_machine(pipe, rejected_at)
    if held_at:
        return "NOT PASS (HOLD)", held_at, _stage_machine(pipe, held_at)
    if waiting_at:
        return "PENDING", waiting_at, _stage_machine(pipe, waiting_at)
    return "PASS", None, None


def _stage_machine(pipe, stage_name):
    """The machine recorded on that stage row, or None.

    The machine lives on the stage, not on the pipe — `Pipe.machine_id` is
    unused — so most stages have none and only the ones that record one
    (CCM, chiefly) can name it.
    """
    stage = pipe.get_stage(stage_name)
    machine = getattr(stage, "machine", None) if stage else None
    if machine is None:
        return None
    return machine.machine_code or machine.machine_name or None


def sticker_decision(pipe):
    """The verdict printed in the label's Decision field. Unchanged by the
    print mode — the customer's label and the floor's label agree on it."""
    return sticker_status(pipe)[0]


def line_position(pipe):
    """Which stage the pipe is standing at right now, or None.

    A physical question, deliberately answered without consulting the
    verdict: wherever the pipe is on the line, the floor wants to be told
    where to go and find it. In order of what stops the walk:

      - the stage that rejected it, then the stage holding it — that is
        where the pipe was set aside;
      - otherwise the first stage still owing a decision — the pipe
        cleared everything before it and is queued there;
      - otherwise the last stage it cleared — it finished the line and is
        standing at the end of it.

    Delivery never counts as "owing a decision": it is decided after the
    label is printed, so a finished pipe would otherwise report itself as
    standing at Delivery while it sits in the yard.

    Only None when the line has recorded nothing at all about this pipe.
    """
    from app.models.pipe import PipeStage
    from app.models.stage import ProductionStage

    delivery_name = ProductionStage.name_for_code("delivery")
    rejected = held = waiting = last = None

    for stage_name in _ordered_stage_names(pipe):
        stage = pipe.get_stage(stage_name)
        decision = stage.decision if stage else None

        if not decision:
            if waiting is None and stage_name != delivery_name:
                waiting = stage_name
            continue

        last = stage_name
        verdict = PipeStage.classify_decision(decision)
        if verdict == "reject":
            if rejected is None:
                rejected = stage_name
        elif verdict != "accept" and held is None:
            held = stage_name

    return rejected or held or waiting or last


def sticker_position(pipe, internal=False):
    """Where the pipe is standing, for the label's Stage row, or None.

    Internal prints only: which stage a pipe is at, and the machine it is
    on, are the shop floor's business and not the customer's.

    Every internal label carries the row. It used to carry it only when the
    verdict itself came from a stage, which left two holes on exactly the
    prints the floor needs: a finished pipe (nothing explained its PASS)
    printed identically to the customer's label, and a pipe whose lab
    result is still WAITING printed a bare "PENDING" — the label that
    started this whole row, because PENDING alone tells the floor nothing
    it can act on.

    This is a row of its own rather than a suffix on Decision because the
    verdict has to stay short and readable — the value font shrinks to fit
    its column, and "NOT PASS (HOLD) — Annealing (AF1)" in the space meant
    for "PASS" prints too small to read on a 80x50mm label.
    """
    if not internal:
        return None
    stage_name = line_position(pipe)
    if not stage_name:
        return None
    machine = _stage_machine(pipe, stage_name)
    return f"{stage_name} ({machine})" if machine else stage_name


def wants_internal():
    """Is this print for the shop floor rather than the customer?

    A choice made per print, not a stored setting: the same pipe gets an
    internal label on the floor and an external one when it ships. The
    floor prints far more labels than ship, so the label answers "where is
    it" by default — the customer print is the explicit choice. Only an
    explicit `mode=external` opts out; anything else (missing, unknown,
    misspelled) stays internal.
    """
    mode = (request.args.get("mode") or request.form.get("mode") or "").strip().lower()
    return mode != "external"


def get_sticker_sizes():
    """Get sticker sizes from settings or use defaults"""
    sticker_settings = load_sticker_settings()
    sizes = sticker_settings.get("sizes", {})

    return {
        "card": tuple(sizes.get("card", [100, 90])),
        "small": tuple(sizes.get("small", [80, 50])),
        "medium": tuple(sizes.get("medium", [100, 60])),
        "large": tuple(sizes.get("large", [120, 80])),
        "gcp": tuple(sizes.get("gcp", [140, 90])),
    }


# Default sticker sizes in mm (width, height) - can be overridden by settings
STICKER_SIZES = {
    # The stock actually in use is a 100x100mm card. `medium` printed 100x60
    # on it and left the bottom 40% blank paper. This fills the card but stops
    # 10mm short of the edge: the cards are hand-cut, and a border drawn at
    # the paper's edge is a border the trimming eats.
    "card": (100, 90),  # 100x100mm card stock, with a safe margin
    "small": (80, 50),  # Compact label
    "medium": (100, 60),  # Standard product label
    "large": (120, 80),  # Detailed label with all info
    "gcp": (140, 90),  # GCP Professional style (like the image)
}

# What the print screens offer first. The card is the paper on the floor.
DEFAULT_STICKER_SIZE = "card"

# Convert mm to pixels (assuming 300 DPI - can be overridden by settings)
MM_TO_PX = 300 / 25.4  # ~11.81 pixels per mm


@stickers_bp.route("/")
@login_required
@requires_permission('stickers', 'list')
def index():
    """Sticker printing page"""
    # Get dynamic sticker sizes from settings
    sizes = get_sticker_sizes()
    return render_template("stickers/index.html", sizes=sizes)


@stickers_bp.route("/search")
@login_required
@requires_permission('stickers', 'list')
def search_pipe():
    """Search for pipe by no_code, ladle_id, or pipe_code"""
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify({"pipes": []})

    pipes = lookup_pipes(query)[:20]
    if not pipes:
        # Ladle lookup predates the shared selector and remains useful on the
        # sticker screen; it is a sticker convenience, not a shared identity.
        pipes = (
            Pipe.query.filter(Pipe.ladle_id.ilike(f"%{query}%"))
            .order_by(Pipe.production_date.desc(), Pipe.id.desc())
            .limit(20)
            .all()
        )

    result = []
    for pipe in pipes:
        decision = sticker_decision(pipe)

        # Get production order info
        order_number = ""
        customer = ""
        sales_order = ""
        product_code = ""
        product_description = ""
        if pipe.production_order:
            order_number = pipe.production_order.order_number or ""
            customer = pipe.production_order.customer_name or ""
            sales_order = pipe.production_order.sales_number or ""
            product_code = pipe.production_order.product_code or ""
            product_description = pipe.production_order.product_description or ""

        result.append(
            {
                "id": pipe.id,
                "no_code": pipe.no_code,
                "pipe_code": pipe.pipe_code
                or f"{pipe.no_code}-{pipe.ladle_id or ''}-{pipe.arrange_pipe or 1}",
                "ladle_id": pipe.ladle_id,
                "diameter": pipe.diameter,
                "pipe_class": pipe.pipe_class,
                "production_date": pipe.production_date.isoformat()
                if pipe.production_date
                else None,
                "actual_weight": float(pipe.actual_weight)
                if pipe.actual_weight
                else None,
                "decision": decision,
                "status": decision or "PENDING",
                "reason": sticker_position(pipe, internal=True) or "",
                "selectable": True,
                "order": order_number,
                "code": pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                "dn": f"DN{pipe.diameter}" if pipe.diameter else "",
                "order_number": order_number,
                "customer": customer,
                "sales_order": sales_order,
                "product_code": product_code,
                "product_description": product_description,
            }
        )

    return jsonify({"pipes": result})


@stickers_bp.route("/preview/<int:pipe_id>")
@login_required
@requires_permission('stickers', 'list')
def preview_sticker(pipe_id):
    """Preview sticker for a pipe"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get("size", DEFAULT_STICKER_SIZE)
    internal = wants_internal()

    decision = sticker_decision(pipe)
    position = sticker_position(pipe, internal=internal)

    # Get dynamic sticker sizes from settings
    sizes = get_sticker_sizes()

    return render_template(
        "stickers/preview.html",
        pipe=pipe,
        decision=decision or "N/A",
        position=position,
        size=size,
        sizes=sizes,
        internal=internal,
    )


@stickers_bp.route("/generate/<int:pipe_id>")
@login_required
@requires_permission('stickers', 'generate')
def generate_sticker(pipe_id):
    """Generate sticker image with QR code"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get("size", DEFAULT_STICKER_SIZE)
    custom_width = request.args.get("width", type=int)
    custom_height = request.args.get("height", type=int)

    # Get dynamic sticker sizes and settings
    sizes = get_sticker_sizes()
    sticker_settings = load_sticker_settings()
    dpi = sticker_settings.get("dpi", 300)
    mm_to_px = dpi / 25.4

    # Determine size
    if custom_width and custom_height:
        width_mm, height_mm = custom_width, custom_height
    else:
        width_mm, height_mm = sizes.get(size, sizes["medium"])

    # Convert to pixels
    width_px = int(width_mm * mm_to_px)
    height_px = int(height_mm * mm_to_px)

    internal = wants_internal()
    decision = sticker_decision(pipe)

    # Generate sticker image
    sticker_buffer = create_sticker_image(
        pipe, decision or "N/A", width_px, height_px,
        position=sticker_position(pipe, internal=internal),
    )

    return send_file(
        sticker_buffer,
        mimetype="image/png",
        as_attachment=False,
        download_name=f"sticker_{pipe.no_code}.png",
    )


@stickers_bp.route("/download/<int:pipe_id>")
@login_required
@requires_permission('stickers', 'list')
def download_sticker(pipe_id):
    """Download sticker image"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get("size", DEFAULT_STICKER_SIZE)
    custom_width = request.args.get("width", type=int)
    custom_height = request.args.get("height", type=int)

    # Get dynamic sticker sizes and settings
    sizes = get_sticker_sizes()
    sticker_settings = load_sticker_settings()
    dpi = sticker_settings.get("dpi", 300)
    mm_to_px = dpi / 25.4

    # Determine size
    if custom_width and custom_height:
        width_mm, height_mm = custom_width, custom_height
    else:
        width_mm, height_mm = sizes.get(size, sizes["medium"])

    # Convert to pixels
    width_px = int(width_mm * mm_to_px)
    height_px = int(height_mm * mm_to_px)

    internal = wants_internal()
    decision = sticker_decision(pipe)

    # Generate sticker image
    sticker_buffer = create_sticker_image(
        pipe, decision or "N/A", width_px, height_px,
        position=sticker_position(pipe, internal=internal),
    )

    return send_file(
        sticker_buffer,
        mimetype="image/png",
        as_attachment=True,
        download_name=f"sticker_{pipe.no_code}.png",
    )


@stickers_bp.route("/qr/<int:pipe_id>")
@login_required
@requires_permission('stickers', 'list')
def generate_qr(pipe_id):
    """Generate QR code only - includes ALL stage information"""
    pipe = Pipe.query.get_or_404(pipe_id)

    # Get ALL stages info
    stages_info = []
    from app.models.stage import ProductionStage
    for stage_name in ProductionStage.active_names():
        stage = pipe.get_stage(stage_name)
        if stage and stage.decision:
            stages_info.append(
                f"{stage_name}:{stage.decision[:1]}"
            )  # CCM:A, Zinc:A, etc.

    # QR encodes URL to pipe details page (scannable link).
    #
    # url_for(_external=True), not host_url: host_url is scheme and host only,
    # so under the /lab_chemical mount it dropped the prefix and every scan
    # landed on https://www.mbardouni.dev/stages/<id> — a 404. url_for builds
    # from SCRIPT_NAME and the forwarded proto, so it is right both mounted and
    # bare on :9999.
    qr_data = url_for("stages.view", id=pipe.id, _external=True)

    # Generate QR code
    qr = qrcode.QRCode(
        version=2,  # Increased for more data
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)

    qr_image = qr.make_image(fill_color="black", back_color="white")

    # Save to buffer
    buffer = BytesIO()
    qr_image.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png",
        as_attachment=False,
        download_name=f"qr_{pipe.no_code}.png",
    )


@stickers_bp.route("/bulk", methods=["GET"])
@login_required
@requires_permission('stickers', 'bulk_print')
def bulk_print():
    """Bulk sticker printing screen with filters."""
    if not current_user.can_edit:
        flash("You do not have permission for bulk sticker printing.", "error")
        return redirect(url_for("stickers.index"))

    """

    Filters: production_order, ladle_id, sales_order, customer, date_from/to, final_decision.
    Returns a preview of matching pipes; the actual PDF is generated via /bulk/generate.
    """
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    raw_order_ids = request.args.getlist("order_ids")
    legacy_order_id = request.args.get("production_order_id", type=int)
    if not raw_order_ids and legacy_order_id:
        raw_order_ids = [legacy_order_id]
    try:
        order_ids = parse_order_ids(raw_order_ids)
    except ValueError:
        order_ids = []
        flash("Invalid production order.", "error")
    ladle_id = request.args.get("ladle_id")
    sales_order = request.args.get("sales_order")
    customer = request.args.get("customer")
    final_decision = request.args.get("final_decision")
    mode = request.args.get("mode")
    lookup_term = (request.args.get("pipe_lookup") or "").strip()

    query = Pipe.query
    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if order_ids:
        query = query.filter(Pipe.production_order_id.in_(order_ids))
    if ladle_id:
        query = query.filter(Pipe.ladle_id == ladle_id)
    if final_decision:
        query = query.filter(Pipe.final_decision_value == final_decision)
    if sales_order or customer:
        query = query.join(ProductionOrder)
        if sales_order:
            query = query.filter(ProductionOrder.sales_number == sales_order)
        if customer:
            query = query.filter(ProductionOrder.customer_name.ilike(f"%{customer}%"))

    pipes = query.order_by(Pipe.production_date.desc()).limit(500).all()
    lookup_matches = lookup_pipes(lookup_term) if lookup_term else []
    selection_pipes = list(pipes)
    selected_lookup = len(lookup_matches) == 1
    known_ids = {pipe.id for pipe in selection_pipes}
    for pipe in lookup_matches:
        if pipe.id not in known_ids:
            selection_pipes.append(pipe)
            known_ids.add(pipe.id)
    # The label's verdict is walked out of the stage rows, so fetch them for
    # the whole list at once instead of once per row.
    Pipe.prefill_stage_maps(selection_pipes)
    orders = (
        ProductionOrder.query.order_by(ProductionOrder.order_number.desc())
        .limit(100)
        .all()
    )

    # What each row will actually print. The Decision column beside it is
    # Pipe.final_decision_value — the lab's verdict on the metal, stored the
    # moment the analysis lands. The label's verdict is a different question:
    # it walks the line and only says PASS once every stage has accepted. So
    # a row reading ACCEPT prints "PENDING — Annealing" when the pipe is a
    # good casting still queued at Annealing, and the two disagreeing on
    # screen used to look like a bug in the label.
    labels = {
        pipe.id: (sticker_decision(pipe), sticker_position(pipe, internal=True))
        for pipe in selection_pipes
    }
    preserved_filters = [
        (name, value)
        for name, value in (
            ("date_from", date_from),
            ("date_to", date_to),
            ("ladle_id", ladle_id),
            ("sales_order", sales_order),
            ("customer", customer),
            ("final_decision", final_decision),
            ("mode", mode),
        )
        if value
    ]
    lookup_hidden_fields = preserved_filters + [
        ("order_ids", order_id) for order_id in order_ids
    ]

    return render_template(
        "stickers/bulk.html",
        pipes=pipes,
        labels=labels,
        orders=orders,
        filters={
            "date_from": date_from,
            "date_to": date_to,
            "production_order_id": order_ids[0] if len(order_ids) == 1 else None,
            "order_ids": order_ids,
            "ladle_id": ladle_id,
            "sales_order": sales_order,
            "customer": customer,
            "final_decision": final_decision,
            "mode": mode,
        },
        sizes=get_sticker_sizes(),
        internal=wants_internal(),
        selection_rows=[
            _selection_pipe_json(
                pipe,
                labels[pipe.id],
                selected=(pipe in pipes or (selected_lookup and pipe in lookup_matches)),
            )
            for pipe in selection_pipes
        ],
        selection_order_hidden_fields=preserved_filters,
        selection_lookup_hidden_fields=lookup_hidden_fields,
    )


def _selection_pipe_json(pipe, label, selected=True):
    """Serialize a pipe for the shared selector without applying print policy."""
    verdict, position = label
    return {
        "id": pipe.id,
        "code": pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
        "no_code": pipe.no_code or "",
        "barcode": pipe.warehouse_barcode or "",
        "dn": f"DN{pipe.diameter}" if pipe.diameter else "",
        "pipe_class": pipe.pipe_class or "",
        "order": (pipe.production_order.order_number
                  if pipe.production_order else ""),
        "status": verdict or "PENDING",
        "reason": position or "",
        "selectable": True,
        "selected": selected,
        "url": url_for("stickers.preview_sticker", pipe_id=pipe.id),
    }


def render_batch(pipes, size=DEFAULT_STICKER_SIZE, internal=False):
    """Render a list of pipes as sticker PNGs, plus the size they were drawn at.

    The single entry point for batch printing, so a sheet of labels is the
    same picture as the one the preview screen shows. Returns
    (list_of_png_buffers, width_mm, height_mm).
    """
    sizes = get_sticker_sizes()
    width_mm, height_mm = sizes.get(size, sizes.get(DEFAULT_STICKER_SIZE, (100, 90)))
    dpi = load_sticker_settings().get("dpi", 300)
    mm_to_px = dpi / 25.4
    width_px = int(width_mm * mm_to_px)
    height_px = int(height_mm * mm_to_px)

    pngs = [
        create_sticker_image(
            pipe, sticker_decision(pipe), width_px, height_px,
            position=sticker_position(pipe, internal=internal),
        )
        for pipe in pipes
    ]
    return pngs, width_mm, height_mm


def _load_selected_pipes(raw_ids):
    """Strictly reload one submitted sticker basket in first-seen order."""
    try:
        pipe_ids = dedupe_pipe_ids(raw_ids)
    except ValueError as error:
        return [], str(error)
    if not pipe_ids:
        return [], "No pipes selected"

    rows = {
        pipe.id: pipe
        for pipe in Pipe.query.filter(Pipe.id.in_(pipe_ids)).all()
    }
    missing = [pipe_id for pipe_id in pipe_ids if pipe_id not in rows]
    if missing:
        return [], "Pipe ID(s) no longer exist: " + ", ".join(map(str, missing))
    return [rows[pipe_id] for pipe_id in pipe_ids], None


@stickers_bp.route("/bulk/generate", methods=["POST"])
@login_required
@requires_permission('stickers', 'bulk_print')
def bulk_generate():
    """Generate a single PDF with all filtered stickers tiled on pages."""
    from app.services.qr_service import create_batch_stickers

    pipe_ids = request.form.getlist("pipe_ids") or request.form.getlist("pipe_ids[]")
    size = request.form.get("size", DEFAULT_STICKER_SIZE)
    pipes, error = _load_selected_pipes(pipe_ids)
    if error:
        return jsonify({"error": error}), 400

    pngs, width_mm, height_mm = render_batch(pipes, size, internal=wants_internal())
    pdf = create_batch_stickers(pngs, width_mm, height_mm)
    return send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="bulk_stickers.pdf",
    )


@stickers_bp.route("/bulk/print", methods=["POST"])
@login_required
@requires_permission('stickers', 'bulk_print')
def bulk_print_sheet():
    """The same sheet as the PDF, in a page that opens the printer itself.

    Bulk printing only ever offered a PDF download: print a run of labels
    and you first save a file, find it, open it, then print. The PDF stays
    — it is what gets kept and mailed — but the floor's normal case is a
    stack of cards coming out of the printer now.

    Same pipes, same order, same `render_batch`, so the paper is the same
    picture either way.
    """
    import base64

    pipe_ids = request.form.getlist("pipe_ids") or request.form.getlist("pipe_ids[]")
    size = request.form.get("size", DEFAULT_STICKER_SIZE)
    pipes, error = _load_selected_pipes(pipe_ids)
    if error:
        flash(error, "error")
        return redirect(url_for("stickers.bulk_print"))

    pngs, width_mm, height_mm = render_batch(pipes, size, internal=wants_internal())

    return render_template(
        "stickers/print_sheet.html",
        images=[
            "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
            for png in pngs
        ],
        width_mm=width_mm,
        height_mm=height_mm,
    )


@stickers_bp.route("/batch", methods=["POST"])
@login_required
@requires_permission('stickers', 'batch')
def batch_print():
    """Generate multiple stickers for batch printing"""
    pipe_ids = request.json.get("pipe_ids", [])
    size = request.json.get("size", DEFAULT_STICKER_SIZE)

    pipes, error = _load_selected_pipes(pipe_ids)
    if error:
        return jsonify({"error": error}), 400

    stickers_data = []
    for pipe in pipes:
        decision = sticker_decision(pipe)

        stickers_data.append(
            {
                "no_code": pipe.no_code,
                "ladle_id": pipe.ladle_id,
                "diameter": pipe.diameter,
                "pipe_class": pipe.pipe_class,
                "production_date": pipe.production_date.isoformat()
                if pipe.production_date
                else None,
                "weight": float(pipe.actual_weight) if pipe.actual_weight else None,
                "decision": decision,
            }
        )

    return jsonify({"stickers": stickers_data, "size": size})


FONT_PATHS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "arialbd.ttf",
]
FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "arial.ttf",
]


_warned_no_truetype = False


def _load_font(size, bold=False):
    """Load a TrueType font at the given size, falling back across known paths.

    The bitmap fallback is a last resort, not a normal outcome: it ignores
    `size`, so the whole label collapses to unreadable fixed-size text. That
    shipped unnoticed for a month because this used to fail silently — warn
    once per process instead (see fonts-dejavu-core in the Dockerfile).
    """
    global _warned_no_truetype
    size = max(int(size), 6)
    for path in (FONT_PATHS_BOLD if bold else FONT_PATHS_REGULAR):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    if not _warned_no_truetype:
        _warned_no_truetype = True
        current_app.logger.warning(
            "sticker: no TrueType font found in %s — falling back to PIL's "
            "bitmap default; labels will render small and unstyled. Install "
            "fonts-dejavu-core.",
            FONT_PATHS_BOLD + FONT_PATHS_REGULAR,
        )
    return ImageFont.load_default()


def _text_w(draw, text, font):
    if not text:
        return 0
    return draw.textbbox((0, 0), text, font=font)[2]


def _fit_font(draw, text, max_width, start_size, bold=False, min_size=8):
    """Largest font size (<= start_size) at which `text` fits in max_width."""
    size = int(start_size)
    while size > min_size:
        font = _load_font(size, bold=bold)
        if _text_w(draw, text, font) <= max_width:
            return font
        size -= 1
    return _load_font(min_size, bold=bold)


def _wrap(draw, text, font, max_width, max_lines=2):
    """Greedy word-wrap; last line gets an ellipsis if it overflows."""
    if not text:
        return []
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if _text_w(draw, trial, font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines)) < len(text):
        last = lines[-1]
        while last and _text_w(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _fit_image(im, max_w, max_h):
    ratio = min(max_w / im.width, max_h / im.height)
    return im.resize(
        (max(int(im.width * ratio), 1), max(int(im.height * ratio), 1)),
        Image.Resampling.LANCZOS,
    )


def _asset(*parts):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "static", "images", *parts)


def _sticker_barcode_value(pipe):
    """The value the sticker's Code128 barcode encodes.

    Prefer the product's scannable barcode number; fall back to the pipe code
    when the pipe has no product or the product has no barcode (DrAlaa
    2026-08-13: the barcode always rendered pipe_code, so it looked "fixed").
    """
    pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.ladle_id or ''}-{pipe.arrange_pipe or 1}"
    if pipe.production_order is not None and pipe.production_order.product is not None:
        return pipe.production_order.product.barcode or pipe_code
    return pipe_code


def _warehouse_barcode_value(pipe):
    """The value the sticker's second (right-hand) Code128 encodes.

    The left barcode carries the *product* number, which every pipe of that
    product shares. This one is the pipe's own warehouse code, so the
    storekeeper's scanner resolves to exactly one pipe. Absent until someone
    types it at the Finish stage, in which case no second barcode is drawn.
    """
    return pipe.warehouse_barcode or None


def _code128(value, max_w, max_h, label):
    """Render `value` as a fitted Code128 image, or None if it cannot be.

    Failures are logged rather than swallowed: a missing python-barcode hid
    for a month behind a bare except, and the label simply lost its strip
    with nothing to show why.
    """
    if not value:
        return None
    try:
        import barcode as _bc
        from barcode.writer import ImageWriter

        buf = BytesIO()
        _bc.get("code128", str(value), writer=ImageWriter()).write(
            buf,
            options={
                "module_height": 9.0,
                "font_size": 7,
                "text_distance": 3.0,
                "quiet_zone": 1.0,
            },
        )
        buf.seek(0)
        return _fit_image(Image.open(buf), max_w, max_h)
    except Exception as exc:
        current_app.logger.warning(
            "sticker: %s Code128 not rendered for %s (%s: %s)",
            label, value, type(exc).__name__, exc,
        )
        return None


def _sticker_code_image(value, size):
    """Render the configured QR or Data Matrix symbol for a sticker."""
    s = load_sticker_settings()
    code_type = (s.get("code_type") or ("qr" if s.get("show_qr", True) else "none")).lower()
    if code_type == "data_matrix":
        try:
            from pylibdmtx.pylibdmtx import encode
            encoded = encode(str(value).encode("utf-8"))
            code = Image.frombytes("RGB", (encoded.width, encoded.height), encoded.pixels)
            return _fit_image(code, size, size).resize((size, size), Image.Resampling.NEAREST)
        except Exception as exc:
            current_app.logger.warning("sticker: Data Matrix unavailable (%s: %s)", type(exc).__name__, exc)
            return None
    if code_type != "qr":
        return None
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=1)
    qr.add_data(value)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB").resize(
        (size, size), Image.Resampling.NEAREST
    )


def create_sticker_image(pipe, decision, width_px, height_px, position=None):
    """Render the GCP pipe label.

    `position` is the internal print's extra Stage row — where the pipe is
    standing. It costs the layout nothing: the grid is already sized to the
    taller column, and the left one carries five rows, so the right column's
    fifth slot is space that was being left empty.

    Laid out on a padded grid so nothing overflows the sticker at any size:
    header (logo | QR | recycle) -> rule -> barcode | title + product code ->
    description -> two-column field grid -> website footer. Every text run is
    either shrunk to fit its column or wrapped, so long values (product codes,
    customer names, descriptions) can never bleed past the border.
    """
    s = load_sticker_settings()
    website_url = s.get("website_url", "www.gcpipes.com")
    website_color = s.get("website_color", "#0066CC")
    text_color = s.get("text_color", "#333333")
    show_logo = s.get("show_logo", True)
    show_recycle = s.get("show_recycle", True)
    show_qr = s.get("show_qr", True)
    code_type = (s.get("code_type") or ("qr" if show_qr else "none")).lower()
    company_name = (s.get("company_name") or "").strip()
    show_website = s.get("show_website", True)
    show_barcode = s.get("show_barcode", True)
    show_warehouse_barcode = s.get("show_warehouse_barcode", True)
    title_text = s.get("title_text", "DIP")
    show_title = s.get("show_title", True)
    show_product_code = s.get("show_product_code", True)
    show_description = s.get("show_description", True)
    # Per-field visibility for the two-column data grid (all default on)
    show_fields = s.get("show_fields", {})

    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)

    # --- Grid -------------------------------------------------------------
    pad = int(min(width_px, height_px) * 0.045)  # uniform inner padding
    content_x0 = pad
    content_x1 = width_px - pad
    content_w = content_x1 - content_x0

    # --- Data -------------------------------------------------------------
    order_number = customer = sales_order = product_code = product_description = ""
    application = ""
    product_length = 6
    if pipe.production_order:
        po = pipe.production_order
        order_number = po.order_number or ""
        customer = po.customer_name or ""
        sales_order = po.sales_number or ""
        product_code = po.product_code or ""
        product_description = po.product_description or ""
        product_length = po.product_length or product_length
        # Which standard the run is built to, from the order's Application
        # block. Empty for orders that predate it or had nothing ticked.
        application = ", ".join(po.standard_labels())

    dn = pipe.diameter or 0
    pclass = pipe.pipe_class or "K9"
    if not product_code:
        product_code = f"P{dn}{pclass}Z1{pipe.arrange_pipe or 1}SCB"
    if not product_description:
        product_description = (
            f"Ductile Iron - Centrifugally Casted Socket Spigot Pipe DN{dn} "
            f"Class {pclass} Length {int(product_length)} Meters Zinc Coating 130 gm/m2"
        )
    pipe_code = (
        pipe.pipe_code or f"{pipe.no_code}-{pipe.ladle_id or ''}-{pipe.arrange_pipe or 1}"
    )

    # The scannable barcode should encode the product's barcode number when one
    # is set, not the pipe code (see _sticker_barcode_value).
    barcode_value = _sticker_barcode_value(pipe)

    # --- Border -----------------------------------------------------------
    border = max(int(min(width_px, height_px) * 0.006), 2)
    draw.rectangle(
        [(border, border), (width_px - border - 1, height_px - border - 1)],
        outline="black",
        width=border,
    )

    # --- Header: logo | QR | recycle --------------------------------------
    header_h = int(height_px * 0.24)
    header_y = pad
    header_box_h = header_h - int(pad * 0.4)

    if show_logo:
        try:
            logo = Image.open(_asset("gcp_logo.jpg"))
            logo = _fit_image(logo, int(content_w * 0.26), header_box_h)
            img.paste(logo, (content_x0, header_y + (header_box_h - logo.height) // 2))
        except Exception:
            draw.text(
                (content_x0, header_y),
                "GCP",
                font=_load_font(header_box_h * 0.55, bold=True),
                fill="black",
            )

    if show_qr and code_type in {"qr", "data_matrix"}:
        # See generate_qr: url_for carries the mount prefix, host_url does not.
        # The renderer is also called from batch printing, so keep the guard —
        # outside a request context there is no host to build an absolute URL
        # from, and a relative path is better than no QR at all.
        try:
            pipe_url = url_for("stages.view", id=pipe.id, _external=True)
        except Exception:
            pipe_url = f"/stages/{pipe.id}"
        qr_size = header_box_h
        code_img = _sticker_code_image(pipe_url, qr_size)
        if code_img is not None:
            img.paste(code_img, ((width_px - qr_size) // 2, header_y))

    if show_recycle:
        try:
            rec = Image.open(_asset("recycle.jpg"))
            rec = _fit_image(rec, int(content_w * 0.12), int(header_box_h * 0.85))
            img.paste(
                rec,
                (content_x1 - rec.width, header_y + (header_box_h - rec.height) // 2),
            )
        except Exception:
            pass

    if company_name:
        company_font = _fit_font(draw, company_name, int(content_w * 0.42), height_px * 0.045, bold=True)
        draw.text((content_x0, header_y + header_box_h - int(company_font.size * 1.15)), company_name,
                  font=company_font, fill=text_color)

    # --- Rule under header -------------------------------------------------
    rule_y = header_y + header_box_h + int(pad * 0.5)
    draw.line([(content_x0, rule_y), (content_x1, rule_y)], fill="black", width=2)

    # --- Barcodes (product left, warehouse right) + title/code (centered) --
    band_y = rule_y + int(pad * 0.6)
    bc_max_w = int(content_w * 0.26)
    bc_max_h = int(height_px * 0.15)

    barcode_w = 0
    if show_barcode:
        bc = _code128(barcode_value, bc_max_w, bc_max_h, "product")
        if bc is not None:
            img.paste(bc, (content_x0, band_y))
            barcode_w = bc.width

    # The pipe's own warehouse code, right-aligned. Drawn before the title so
    # the centering below can account for the space it takes.
    warehouse_w = 0
    if show_warehouse_barcode:
        wbc = _code128(
            _warehouse_barcode_value(pipe), bc_max_w, bc_max_h, "warehouse"
        )
        if wbc is not None:
            img.paste(wbc, (content_x1 - wbc.width, band_y))
            warehouse_w = wbc.width

    # Title + code get whatever is left between the two strips, centered in
    # that gap rather than on the sticker, so neither barcode is overlapped.
    center_x0 = content_x0 + barcode_w + (pad if barcode_w else 0)
    center_x1 = content_x1 - warehouse_w - (pad if warehouse_w else 0)
    center_w = max(center_x1 - center_x0, 1)
    center_mid = center_x0 + center_w // 2

    title_font = _fit_font(draw, title_text, center_w, height_px * 0.105, bold=True)
    if show_title and title_text:
        draw.text(
            (center_mid - _text_w(draw, title_text, title_font) // 2, band_y),
            title_text,
            font=title_font,
            fill="black",
        )
    code_y = band_y + int(height_px * 0.10)
    if show_product_code:
        code_font = _fit_font(draw, product_code, center_w, height_px * 0.085, bold=True)
        draw.text(
            (center_mid - _text_w(draw, product_code, code_font) // 2, code_y),
            product_code,
            font=code_font,
            fill="black",
        )

    # --- Description (wrapped, full width) ---------------------------------
    desc_y = max(band_y + int(height_px * 0.175), code_y + int(height_px * 0.09))
    if show_description:
        desc_font = _load_font(height_px * 0.042)
        # Three lines on the tall card, two on the short labels. A product
        # description is one long sentence and was being cut mid-word at
        # "...internal finish Cemen…" on stock that had the room for it.
        desc_lines = 3 if height_px >= width_px * 0.75 else 2
        for line in _wrap(draw, product_description, desc_font, content_w,
                          max_lines=desc_lines):
            draw.text(
                (content_x0 + (content_w - _text_w(draw, line, desc_font)) // 2, desc_y),
                line,
                font=desc_font,
                fill="black",
            )
            desc_y += int(height_px * 0.05)

    # --- Two-column field grid ---------------------------------------------
    def _vis(key):
        return show_fields.get(key, True)

    left = [
        ("Pipe No.", pipe.no_code or "N/A", "pipe_no"),
        ("Pipe Code", pipe_code, "pipe_code"),
        ("Diameter", f"DN{dn}" if dn else "N/A", "diameter"),
        ("Class", pclass, "class"),
        # Dropped rather than printed as "N/A": a label claiming no standard
        # is worse than one that simply does not mention one.
        ("Application", application, "application"),
    ]
    left = [row for row in left if row[1] != ""]
    right = [
        ("Production", order_number or "N/A", "production"),
        ("Sales Order", sales_order or "N/A", "sales_order"),
        ("Customer", customer or "N/A", "customer"),
        (
            "Decision",
            # The label never shows WHO confirmed — just the outcome.
            decision,
            "decision",
        ),
    ]
    # Internal prints only, and only while the pipe is standing somewhere.
    if position:
        right.append(("Stage", position, "stage_position"))
    left = [(lab, val) for lab, val, key in left if _vis(key)]
    right = [(lab, val) for lab, val, key in right if _vis(key)]

    col_gap = int(content_w * 0.04)
    col_w = (content_w - col_gap) // 2

    # The grid gets exactly the height left between the description and the
    # footer, so rows compress instead of colliding with the website line.
    footer_h = int(height_px * 0.075) if show_website else 0
    footer_top = height_px - pad - footer_h
    grid_y = desc_y + int(pad * 0.4)
    grid_h = max(footer_top - int(pad * 0.5) - grid_y, int(height_px * 0.2))
    rows = max(len(left), len(right))
    if rows == 0:
        # All grid fields hidden — nothing to draw in the grid area.
        left = right = []
        grid_y = desc_y + int(pad * 0.4)
        row_h = label_w = font_size = 0
        label_font = None
    else:
        row_h = grid_h // rows
        font_size = min(height_px * 0.052, row_h * 0.78)

        label_font = _load_font(font_size, bold=True)
        # One label column width for both halves so values line up like the design.
        label_w = max(
            _text_w(draw, f"{lab}:", label_font) for lab, _ in (left + right)
        ) + int(pad * 0.6)

    for col_i, column in enumerate((left, right)):
        col_x = content_x0 + col_i * (col_w + col_gap)
        value_x = col_x + label_w
        value_max_w = col_w - label_w
        for row_i, (label, value) in enumerate(column):
            y = grid_y + row_i * row_h
            draw.text((col_x, y), f"{label}:", font=label_font, fill="black")
            vfont = _fit_font(draw, str(value), value_max_w, font_size, min_size=7)
            draw.text((value_x, y), str(value), font=vfont, fill=text_color)

    # --- Footer ------------------------------------------------------------
    if show_website:
        web_font = _fit_font(
            draw, website_url, content_w, height_px * 0.058, bold=True
        )
        draw.line(
            [(content_x0, footer_top), (content_x1, footer_top)],
            fill="#CCCCCC",
            width=1,
        )
        draw.text(
            (
                content_x0 + (content_w - _text_w(draw, website_url, web_font)) // 2,
                footer_top + int(pad * 0.35),
            ),
            website_url,
            font=web_font,
            fill=website_color,
        )

    dpi = s.get("dpi", 300)
    buffer = BytesIO()
    img.save(buffer, format="PNG", dpi=(dpi, dpi))
    buffer.seek(0)
    return buffer
