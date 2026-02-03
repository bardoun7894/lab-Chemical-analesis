"""
Stickers Routes - QR Code and Label Printing
Professional GCP Ductile Iron Pipes Label Design
"""
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required
from io import BytesIO
import qrcode
from PIL import Image, ImageDraw, ImageFont
import os
from app import db
from app.models.pipe import Pipe
from app.models.chemical import ChemicalAnalysis
from app.models.production_order import ProductionOrder

stickers_bp = Blueprint('stickers', __name__)


# Sticker sizes in mm (width, height) - Professional label sizes
STICKER_SIZES = {
    'small': (80, 50),      # Compact label
    'medium': (100, 60),    # Standard product label
    'large': (120, 80),     # Detailed label with all info
    'gcp': (140, 90),       # GCP Professional style (like the image)
}

# Convert mm to pixels (assuming 300 DPI)
MM_TO_PX = 300 / 25.4  # ~11.81 pixels per mm


@stickers_bp.route('/')
@login_required
def index():
    """Sticker printing page"""
    return render_template('stickers/index.html', sizes=STICKER_SIZES)


@stickers_bp.route('/search')
@login_required
def search_pipe():
    """Search for pipe by no_code, ladle_id, or pipe_code"""
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify({'pipes': []})

    pipes = Pipe.query.filter(
        db.or_(
            Pipe.no_code.ilike(f'%{query}%'),
            Pipe.ladle_id.ilike(f'%{query}%'),
            Pipe.pipe_code.ilike(f'%{query}%')
        )
    ).limit(20).all()

    result = []
    for pipe in pipes:
        # Get chemical analysis decision
        chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        decision = chem.decision if chem else 'N/A'

        # Get production order info
        order_number = ''
        customer = ''
        sales_order = ''
        product_code = ''
        product_description = ''
        if pipe.production_order:
            order_number = pipe.production_order.order_number or ''
            customer = pipe.production_order.customer_name or ''
            sales_order = pipe.production_order.sales_number or ''
            product_code = pipe.production_order.product_code or ''
            product_description = pipe.production_order.product_description or ''

        result.append({
            'id': pipe.id,
            'no_code': pipe.no_code,
            'pipe_code': pipe.pipe_code or f"{pipe.no_code}-{pipe.arrange_pipe or 1}-{pipe.ladle_id or ''}",
            'ladle_id': pipe.ladle_id,
            'diameter': pipe.diameter,
            'pipe_class': pipe.pipe_class,
            'production_date': pipe.production_date.isoformat() if pipe.production_date else None,
            'actual_weight': float(pipe.actual_weight) if pipe.actual_weight else None,
            'decision': decision,
            'order_number': order_number,
            'customer': customer,
            'sales_order': sales_order,
            'product_code': product_code,
            'product_description': product_description
        })

    return jsonify({'pipes': result})


@stickers_bp.route('/preview/<int:pipe_id>')
@login_required
def preview_sticker(pipe_id):
    """Preview sticker for a pipe"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get('size', 'medium')

    # Get chemical analysis
    chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
    decision = chem.decision if chem else 'N/A'

    return render_template('stickers/preview.html',
                          pipe=pipe,
                          decision=decision,
                          size=size,
                          sizes=STICKER_SIZES)


@stickers_bp.route('/generate/<int:pipe_id>')
@login_required
def generate_sticker(pipe_id):
    """Generate sticker image with QR code"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get('size', 'medium')
    custom_width = request.args.get('width', type=int)
    custom_height = request.args.get('height', type=int)

    # Determine size
    if custom_width and custom_height:
        width_mm, height_mm = custom_width, custom_height
    else:
        width_mm, height_mm = STICKER_SIZES.get(size, STICKER_SIZES['medium'])

    # Convert to pixels
    width_px = int(width_mm * MM_TO_PX)
    height_px = int(height_mm * MM_TO_PX)

    # Get chemical analysis
    chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
    decision = chem.decision if chem else 'N/A'

    # Generate sticker image
    sticker_buffer = create_sticker_image(pipe, decision, width_px, height_px)

    return send_file(
        sticker_buffer,
        mimetype='image/png',
        as_attachment=False,
        download_name=f'sticker_{pipe.no_code}.png'
    )


@stickers_bp.route('/download/<int:pipe_id>')
@login_required
def download_sticker(pipe_id):
    """Download sticker image"""
    pipe = Pipe.query.get_or_404(pipe_id)
    size = request.args.get('size', 'medium')
    custom_width = request.args.get('width', type=int)
    custom_height = request.args.get('height', type=int)

    # Determine size
    if custom_width and custom_height:
        width_mm, height_mm = custom_width, custom_height
    else:
        width_mm, height_mm = STICKER_SIZES.get(size, STICKER_SIZES['medium'])

    # Convert to pixels
    width_px = int(width_mm * MM_TO_PX)
    height_px = int(height_mm * MM_TO_PX)

    # Get chemical analysis
    chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
    decision = chem.decision if chem else 'N/A'

    # Generate sticker image
    sticker_buffer = create_sticker_image(pipe, decision, width_px, height_px)

    return send_file(
        sticker_buffer,
        mimetype='image/png',
        as_attachment=True,
        download_name=f'sticker_{pipe.no_code}.png'
    )


@stickers_bp.route('/qr/<int:pipe_id>')
@login_required
def generate_qr(pipe_id):
    """Generate QR code only - includes ALL stage information"""
    pipe = Pipe.query.get_or_404(pipe_id)

    # Get chemical analysis
    chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
    decision = chem.decision if chem else 'N/A'

    # Get production order info
    order_number = ''
    customer = ''
    if pipe.production_order:
        order_number = pipe.production_order.order_number
        customer = pipe.production_order.customer_name or ''

    # Get ALL stages info
    stages_info = []
    for stage_name in pipe.STAGES:
        stage = pipe.get_stage(stage_name)
        if stage and stage.decision:
            stages_info.append(f"{stage_name}:{stage.decision[:1]}")  # CCM:A, Zinc:A, etc.

    # Build comprehensive QR code data
    qr_data = f"""NC:{pipe.no_code}
L:{pipe.ladle_id}
DN:{pipe.diameter}
T:{pipe.pipe_class}
D:{pipe.production_date}
W:{pipe.actual_weight}kg
DEC:{decision}
ORD:{order_number}
CUST:{customer}
STAGES:{','.join(stages_info)}"""

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
    qr_image.save(buffer, format='PNG')
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype='image/png',
        as_attachment=False,
        download_name=f'qr_{pipe.no_code}.png'
    )


@stickers_bp.route('/batch', methods=['POST'])
@login_required
def batch_print():
    """Generate multiple stickers for batch printing"""
    pipe_ids = request.json.get('pipe_ids', [])
    size = request.json.get('size', 'medium')

    if not pipe_ids:
        return jsonify({'error': 'No pipes selected'}), 400

    # Get all pipes
    pipes = Pipe.query.filter(Pipe.id.in_(pipe_ids)).all()

    stickers_data = []
    for pipe in pipes:
        chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        decision = chem.decision if chem else 'N/A'

        stickers_data.append({
            'no_code': pipe.no_code,
            'ladle_id': pipe.ladle_id,
            'diameter': pipe.diameter,
            'pipe_class': pipe.pipe_class,
            'production_date': pipe.production_date.isoformat() if pipe.production_date else None,
            'weight': float(pipe.actual_weight) if pipe.actual_weight else None,
            'decision': decision
        })

    return jsonify({'stickers': stickers_data, 'size': size})


def create_sticker_image(pipe, decision, width_px, height_px):
    """Create professional GCP-style sticker image matching the company design"""
    # Create white background
    img = Image.new('RGB', (width_px, height_px), 'white')
    draw = ImageDraw.Draw(img)

    # Try to load fonts (use default if not available)
    try:
        font_title = ImageFont.truetype("arialbd.ttf", int(height_px * 0.07))
        font_large = ImageFont.truetype("arialbd.ttf", int(height_px * 0.065))
        font_medium = ImageFont.truetype("arial.ttf", int(height_px * 0.055))
        font_small = ImageFont.truetype("arial.ttf", int(height_px * 0.045))
        font_code = ImageFont.truetype("arialbd.ttf", int(height_px * 0.06))
    except:
        font_title = ImageFont.load_default()
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_code = ImageFont.load_default()

    # Get production order info
    order_number = ''
    customer = ''
    sales_order = ''
    product_code = ''
    product_description = ''
    product_length = 6  # Default length in meters

    if pipe.production_order:
        order_number = pipe.production_order.order_number or ''
        customer = pipe.production_order.customer_name or ''
        sales_order = pipe.production_order.sales_number or ''
        product_code = pipe.production_order.product_code or ''
        product_description = pipe.production_order.product_description or ''
        if pipe.production_order.product_length:
            product_length = pipe.production_order.product_length

    # Generate Product Code if not available (format: P{DN}C{Class}Z{coating}...)
    if not product_code:
        dn = pipe.diameter or 0
        pc = pipe.pipe_class or 'K9'
        product_code = f"P{dn}{pc}Z1{pipe.arrange_pipe or 1}SCB"

    # Generate product description if not available
    if not product_description:
        dn = pipe.diameter or 0
        pc = pipe.pipe_class or 'K9'
        product_description = f"Core Pipe DN{dn} Class {pc} Length {int(product_length)} Meters Zinc Coating 130 gm/m2"

    # Pipe code
    pipe_code = pipe.pipe_code or f"{pipe.no_code}-{pipe.arrange_pipe or 1}-{pipe.ladle_id or ''}"

    # Build QR code data - comprehensive pipe information
    qr_data = f"""PC:{product_code}
PIPE:{pipe_code}
DN:{pipe.diameter}
CLASS:{pipe.pipe_class}
ORD:{order_number}
SALES:{sales_order}
CUST:{customer}
DEC:{decision}"""

    # Generate QR code
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")

    # Calculate layout dimensions
    margin = int(width_px * 0.03)
    header_height = int(height_px * 0.28)  # Increased for logo
    qr_size = int(height_px * 0.22)

    # Load GCP logo
    logo_loaded = False
    try:
        # Get the path to the logo file
        import flask
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(app_root, 'static', 'images', 'gcp_logo.jpg')
        if os.path.exists(logo_path):
            logo_img = Image.open(logo_path)
            # Calculate logo size (fit in left section)
            logo_max_width = int(width_px * 0.28)
            logo_max_height = int(header_height * 0.85)
            # Maintain aspect ratio
            logo_ratio = min(logo_max_width / logo_img.width, logo_max_height / logo_img.height)
            logo_new_width = int(logo_img.width * logo_ratio)
            logo_new_height = int(logo_img.height * logo_ratio)
            logo_img = logo_img.resize((logo_new_width, logo_new_height), Image.Resampling.LANCZOS)
            logo_loaded = True
    except Exception as e:
        logo_loaded = False

    # Resize QR code
    qr_image = qr_image.resize((qr_size, qr_size))

    # Draw border around sticker
    draw.rectangle([(2, 2), (width_px-3, height_px-3)], outline='black', width=3)

    # Draw header divider line
    draw.line([(margin, header_height), (width_px - margin, header_height)], fill='black', width=2)

    # === HEADER SECTION ===
    # Left: Company Logo
    logo_x = margin + 5
    logo_y = int(header_height * 0.08)

    if logo_loaded:
        # Paste the actual GCP logo image
        img.paste(logo_img, (logo_x, logo_y))
    else:
        # Fallback: Draw GCP text logo
        draw.text((logo_x, logo_y), "GCP", font=font_title, fill='#000000')
        draw.text((logo_x, logo_y + int(height_px * 0.08)), "Ductile Iron Pipes", font=font_small, fill='#666666')

    # Center: QR Code (positioned explicitly in the middle)
    qr_x = (width_px - qr_size) // 2
    qr_y = int(header_height * 0.05)
    img.paste(qr_image, (qr_x, qr_y))

    # Right: Recycling symbol area
    recycle_x = width_px - margin - int(width_px * 0.15)
    recycle_y = int(header_height * 0.2)
    
    # Load and paste recycling image
    try:
        if 'app_root' not in locals():
            import flask
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        recycle_path = os.path.join(app_root, 'static', 'images', 'recycle.jpg')
        if os.path.exists(recycle_path):
            recycle_img = Image.open(recycle_path)
            
            # Calculate recycle image size (fit in right section)
            recycle_max_width = int(width_px * 0.15)
            recycle_max_height = int(header_height * 0.85)
            
            # Maintain aspect ratio
            recycle_ratio = min(recycle_max_width / recycle_img.width, recycle_max_height / recycle_img.height)
            recycle_new_width = int(recycle_img.width * recycle_ratio)
            recycle_new_height = int(recycle_img.height * recycle_ratio)
            
            recycle_img = recycle_img.resize((recycle_new_width, recycle_new_height), Image.Resampling.LANCZOS)
            img.paste(recycle_img, (recycle_x, recycle_y))
        else:
            # Fallback if image not found
            draw.text((recycle_x, recycle_y), "♻", font=font_title, fill='#228B22')
    except Exception as e:
        # Fallback on error
        print(f"Error loading recycle image: {e}")
        draw.text((recycle_x, recycle_y), "♻", font=font_title, fill='#228B22')

    # === PRODUCT CODE SECTION (below header) ===
    code_y = header_height + int(height_px * 0.02)
    # Product code centered and bold
    code_text = product_code
    try:
        code_bbox = draw.textbbox((0, 0), code_text, font=font_code)
        code_width = code_bbox[2] - code_bbox[0]
    except:
        code_width = len(code_text) * 8
    code_x = (width_px - code_width) // 2
    draw.text((code_x, code_y), code_text, font=font_code, fill='black')

    # === DESCRIPTION SECTION ===
    desc_y = code_y + int(height_px * 0.08)

    # English description (Centered)
    desc_en = product_description[:80] + "..." if len(product_description) > 80 else product_description
    try:
        desc_bbox = draw.textbbox((0, 0), desc_en, font=font_small)
        desc_width = desc_bbox[2] - desc_bbox[0]
    except Exception:
        desc_width = len(desc_en) * 6
        
    desc_x = (width_px - desc_width) // 2
    draw.text((desc_x, desc_y), desc_en, font=font_small, fill='black')
    
    # Arabic description removed as requested

    # === INFO SECTION (bottom area) ===
    info_y = desc_y + int(height_px * 0.08)
    col1_x = margin
    col2_x = width_px // 2

    # Draw info in two columns
    info_items = [
        (f"Pipe No: {pipe.no_code or 'N/A'}", col1_x),
        (f"Customer: {customer[:20] if customer else 'N/A'}", col2_x),
        (f"Pipe Code: {pipe_code}", col1_x),
        (f"Sales Order: {sales_order or 'N/A'}", col2_x),
        (f"Production: {order_number or 'N/A'}", col1_x),
        (f"DN: {pipe.diameter or '?'} | Class: {pipe.pipe_class or '?'}", col2_x),
    ]

    current_y = info_y
    for i, (text, x) in enumerate(info_items):
        if i % 2 == 0 and i > 0:
            current_y += int(height_px * 0.055)
        draw.text((x, current_y), text, font=font_small, fill='#333333')

    # === FOOTER - Website ===
    footer_y = height_px - int(height_px * 0.1)
    draw.line([(margin, footer_y - 5), (width_px - margin, footer_y - 5)], fill='#CCCCCC', width=1)
    website = "www.gcpipes.com"
    try:
        web_bbox = draw.textbbox((0, 0), website, font=font_medium)
        web_width = web_bbox[2] - web_bbox[0]
    except:
        web_width = len(website) * 8
    web_x = (width_px - web_width) // 2
    draw.text((web_x, footer_y), website, font=font_medium, fill='#0066CC')

    # Save to buffer
    buffer = BytesIO()
    img.save(buffer, format='PNG', dpi=(300, 300))
    buffer.seek(0)

    return buffer
