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


def sticker_decision(pipe):
    """Decision printed on the label: PASS only if the pipe passed EVERYTHING
    through Finish.

    The lab decision (chemical + mechanical outcome for this pipe) is the
    first gate. After a lab ACCEPT, every stage — built-in AND custom (e.g.
    a client-added "curing") — must have a recorded accept decision:
      - any recorded Reject (any stage, incl. Delivery) -> NOT PASS
      - any recorded Hold / non-accept -> NOT PASS (HOLD)
      - a stage with no decision yet -> PENDING (pipe still in the pipeline)
    Delivery is the one exception: the sticker is printed BEFORE shipping,
    so a missing Delivery decision does not block PASS — but a recorded
    Delivery Hold/Reject still shows on the sticker.
    """
    from app.models.pipe import PipeStage
    from app.models.stage import ProductionStage

    lab = pipe.lab_decision

    if lab in ("REJECT", "BLOCKED"):
        return "NOT PASS"
    if lab == "HOLD":
        return "NOT PASS (HOLD)"
    if lab in (None, "", "WAITING", "FROZEN"):
        return "PENDING"

    # lab == ACCEPT: PASS only when the pipe passed every stage through
    # Finish. Delivery is the exception — the sticker is printed before
    # shipping, so a missing Delivery decision does NOT block PASS; but if
    # Delivery DOES have a decision, it must be an accept (a delivery
    # problem shows on the sticker).
    if pipe.final_decision_value == "REJECT":
        return "NOT PASS"
    delivery_name = ProductionStage.name_for_code("delivery")
    stage_names = set(ProductionStage.active_names())
    stage_names.update(s.stage_name for s in pipe.stages)
    incomplete = False
    for stage_name in stage_names:
        stage = pipe.get_stage(stage_name)
        decision = stage.decision if stage else None
        verdict = PipeStage.classify_decision(decision)
        if verdict == "reject":
            return "NOT PASS"
        if stage_name == delivery_name:
            if decision and verdict != "accept":
                return "NOT PASS (HOLD)"
            continue
        if not decision:
            incomplete = True
        elif verdict != "accept":
            return "NOT PASS (HOLD)"
    if incomplete:
        return "PENDING"
    return "PASS"


def get_sticker_sizes():
    """Get sticker sizes from settings or use defaults"""
    sticker_settings = load_sticker_settings()
    sizes = sticker_settings.get("sizes", {})

    return {
        "small": tuple(sizes.get("small", [80, 50])),
        "medium": tuple(sizes.get("medium", [100, 60])),
        "large": tuple(sizes.get("large", [120, 80])),
        "gcp": tuple(sizes.get("gcp", [140, 90])),
    }


# Default sticker sizes in mm (width, height) - can be overridden by settings
STICKER_SIZES = {
    "small": (80, 50),  # Compact label
    "medium": (100, 60),  # Standard product label
    "large": (120, 80),  # Detailed label with all info
    "gcp": (140, 90),  # GCP Professional style (like the image)
}

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

    pipes = (
        Pipe.query.filter(
            db.or_(
                Pipe.no_code.ilike(f"%{query}%"),
                Pipe.ladle_id.ilike(f"%{query}%"),
                Pipe.pipe_code.ilike(f"%{query}%"),
            )
        )
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
    size = request.args.get("size", "medium")

    decision = sticker_decision(pipe)

    # Get dynamic sticker sizes from settings
    sizes = get_sticker_sizes()

    return render_template(
        "stickers/preview.html",
        pipe=pipe,
        decision=decision or "N/A",
        size=size,
        sizes=sizes,
    )


@stickers_bp.route("/generate/<int:pipe_id>")
@login_required
@requires_permission('stickers', 'generate')
def generate_sticker(pipe_id):
    """Generate sticker image with QR code"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get("size", "medium")
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

    decision = sticker_decision(pipe)

    # Generate sticker image
    sticker_buffer = create_sticker_image(pipe, decision or "N/A", width_px, height_px)

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
    size = request.args.get("size", "medium")
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

    decision = sticker_decision(pipe)

    # Generate sticker image
    sticker_buffer = create_sticker_image(pipe, decision or "N/A", width_px, height_px)

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

    # QR encodes URL to pipe details page (scannable link)
    from flask import request as flask_request

    base_url = flask_request.host_url.rstrip("/")
    pipe_url = f"{base_url}/stages/{pipe.id}"

    qr_data = pipe_url

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
    order_id = request.args.get("production_order_id", type=int)
    ladle_id = request.args.get("ladle_id")
    sales_order = request.args.get("sales_order")
    customer = request.args.get("customer")
    final_decision = request.args.get("final_decision")

    query = Pipe.query
    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if order_id:
        query = query.filter(Pipe.production_order_id == order_id)
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
    orders = (
        ProductionOrder.query.order_by(ProductionOrder.order_number.desc())
        .limit(100)
        .all()
    )

    return render_template(
        "stickers/bulk.html",
        pipes=pipes,
        orders=orders,
        filters={
            "date_from": date_from,
            "date_to": date_to,
            "production_order_id": order_id,
            "ladle_id": ladle_id,
            "sales_order": sales_order,
            "customer": customer,
            "final_decision": final_decision,
        },
        sizes=get_sticker_sizes(),
    )


@stickers_bp.route("/bulk/generate", methods=["POST"])
@login_required
@requires_permission('stickers', 'bulk_print')
def bulk_generate():
    """Generate a single PDF with all filtered stickers tiled on pages."""
    from app.services.qr_service import create_batch_stickers

    pipe_ids = request.form.getlist("pipe_ids") or request.form.getlist("pipe_ids[]")
    size = request.form.get("size", "medium")
    if not pipe_ids:
        return jsonify({"error": "No pipes selected"}), 400

    pipes = Pipe.query.filter(Pipe.id.in_(pipe_ids)).order_by(Pipe.no_code).all()
    pipes_info = []
    for pipe in pipes:
        pipes_info.append(
            {
                "no_code": pipe.no_code,
                "ladle_id": pipe.ladle_id,
                "diameter": pipe.diameter,
                "pipe_class": pipe.pipe_class,
                "production_date": pipe.production_date.isoformat()
                if pipe.production_date
                else "",
                "weight": float(pipe.actual_weight) if pipe.actual_weight else "",
                "decision": sticker_decision(pipe),
                "order_number": pipe.production_order.order_number
                if pipe.production_order
                else "",
                "customer": pipe.production_order.customer_name
                if pipe.production_order
                else "",
                "stages": "",
            }
        )
    pdf = create_batch_stickers(pipes_info, size)
    return send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="bulk_stickers.pdf",
    )


@stickers_bp.route("/batch", methods=["POST"])
@login_required
@requires_permission('stickers', 'batch')
def batch_print():
    """Generate multiple stickers for batch printing"""
    pipe_ids = request.json.get("pipe_ids", [])
    size = request.json.get("size", "medium")

    if not pipe_ids:
        return jsonify({"error": "No pipes selected"}), 400

    # Get all pipes
    pipes = Pipe.query.filter(Pipe.id.in_(pipe_ids)).all()

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


def create_sticker_image(pipe, decision, width_px, height_px):
    """Render the GCP pipe label.

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
    show_website = s.get("show_website", True)
    show_barcode = s.get("show_barcode", True)
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
    product_length = 6
    if pipe.production_order:
        po = pipe.production_order
        order_number = po.order_number or ""
        customer = po.customer_name or ""
        sales_order = po.sales_number or ""
        product_code = po.product_code or ""
        product_description = po.product_description or ""
        product_length = po.product_length or product_length

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

    if show_qr:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=1,
        )
        try:
            from flask import request as flask_request

            base_url = flask_request.host_url.rstrip("/")
        except Exception:
            base_url = ""
        qr.add_data(f"{base_url}/stages/{pipe.id}")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_size = header_box_h
        qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)
        img.paste(qr_img, ((width_px - qr_size) // 2, header_y))

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

    # --- Rule under header -------------------------------------------------
    rule_y = header_y + header_box_h + int(pad * 0.5)
    draw.line([(content_x0, rule_y), (content_x1, rule_y)], fill="black", width=2)

    # --- Barcode (left) + title/product code (centered) --------------------
    band_y = rule_y + int(pad * 0.6)
    barcode_w = 0
    if show_barcode:
        try:
            import barcode as _bc
            from barcode.writer import ImageWriter

            buf = BytesIO()
            _bc.get("code128", barcode_value, writer=ImageWriter()).write(
                buf,
                options={
                    "module_height": 9.0,
                    "font_size": 7,
                    "text_distance": 3.0,
                    "quiet_zone": 1.0,
                },
            )
            buf.seek(0)
            bc = _fit_image(
                Image.open(buf), int(content_w * 0.26), int(height_px * 0.15)
            )
            img.paste(bc, (content_x0, band_y))
            barcode_w = bc.width
        except Exception as exc:
            # Swallowing this silently is how the missing python-barcode
            # dependency hid for a month: the label just quietly lost its
            # Code128 strip and looked like the old layout.
            barcode_w = 0
            current_app.logger.warning(
                "sticker: Code128 barcode not rendered for %s (%s: %s)",
                barcode_value, type(exc).__name__, exc,
            )

    # Title + code get the space between the barcode and the right padding,
    # centered on the sticker but never allowed to overlap the barcode.
    center_x0 = content_x0 + barcode_w + (pad if barcode_w else 0)
    center_w = content_x1 - center_x0
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
        for line in _wrap(draw, product_description, desc_font, content_w, max_lines=2):
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
    ]
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
