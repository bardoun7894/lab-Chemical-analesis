"""
Stickers Routes - QR Code and Label Printing
"""
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required
from io import BytesIO
import qrcode
from PIL import Image, ImageDraw, ImageFont
from app import db
from app.models.pipe import Pipe
from app.models.chemical import ChemicalAnalysis

stickers_bp = Blueprint('stickers', __name__)


# Sticker sizes in mm (width, height)
STICKER_SIZES = {
    'small': (50, 30),
    'medium': (70, 50),
    'large': (100, 70),
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
    """Search for pipe by no_code or ladle_id"""
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify({'pipes': []})

    pipes = Pipe.query.filter(
        db.or_(
            Pipe.no_code.ilike(f'%{query}%'),
            Pipe.ladle_id.ilike(f'%{query}%')
        )
    ).limit(20).all()

    result = []
    for pipe in pipes:
        # Get chemical analysis decision
        chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
        decision = chem.decision if chem else 'N/A'

        result.append({
            'id': pipe.id,
            'no_code': pipe.no_code,
            'ladle_id': pipe.ladle_id,
            'diameter': pipe.diameter,
            'pipe_type': pipe.pipe_type,
            'production_date': pipe.production_date.isoformat() if pipe.production_date else None,
            'actual_weight': float(pipe.actual_weight) if pipe.actual_weight else None,
            'decision': decision
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
    """Generate QR code only"""
    pipe = Pipe.query.get_or_404(pipe_id)

    # Get chemical analysis
    chem = ChemicalAnalysis.query.filter_by(ladle_id=pipe.ladle_id).first()
    decision = chem.decision if chem else 'N/A'

    # QR code data
    qr_data = f"""No.Code:{pipe.no_code}
Ladle:{pipe.ladle_id}
DN:{pipe.diameter}
Type:{pipe.pipe_type}
Date:{pipe.production_date}
Weight:{pipe.actual_weight}kg
Decision:{decision}"""

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
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
            'pipe_type': pipe.pipe_type,
            'production_date': pipe.production_date.isoformat() if pipe.production_date else None,
            'weight': float(pipe.actual_weight) if pipe.actual_weight else None,
            'decision': decision
        })

    return jsonify({'stickers': stickers_data, 'size': size})


def create_sticker_image(pipe, decision, width_px, height_px):
    """Create sticker image with pipe info and QR code"""
    # Create white background
    img = Image.new('RGB', (width_px, height_px), 'white')
    draw = ImageDraw.Draw(img)

    # Try to load fonts (use default if not available)
    try:
        font_large = ImageFont.truetype("arial.ttf", int(height_px * 0.08))
        font_medium = ImageFont.truetype("arial.ttf", int(height_px * 0.06))
        font_small = ImageFont.truetype("arial.ttf", int(height_px * 0.05))
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # QR code data
    qr_data = f"NC:{pipe.no_code}|L:{pipe.ladle_id}|DN:{pipe.diameter}|T:{pipe.pipe_type}|D:{decision}"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=1,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")

    # Resize QR code
    qr_size = int(min(width_px, height_px) * 0.4)
    qr_image = qr_image.resize((qr_size, qr_size))

    # Place QR code on right side
    qr_x = width_px - qr_size - int(width_px * 0.02)
    qr_y = int(height_px * 0.15)
    img.paste(qr_image, (qr_x, qr_y))

    # Draw text on left side
    x_offset = int(width_px * 0.03)
    y_offset = int(height_px * 0.05)
    line_height = int(height_px * 0.12)

    # Title
    draw.text((x_offset, y_offset), "PIPE STICKER", font=font_large, fill='black')
    y_offset += line_height

    # Draw border around sticker
    draw.rectangle([(2, 2), (width_px-3, height_px-3)], outline='black', width=2)

    # Pipe info
    info_lines = [
        f"No. Code: {pipe.no_code or 'N/A'}",
        f"Ladle#: {pipe.ladle_id or 'N/A'}",
        f"DN: {pipe.diameter or 'N/A'}  Type: {pipe.pipe_type or 'N/A'}",
        f"Date: {pipe.production_date or 'N/A'}",
        f"Weight: {pipe.actual_weight or 'N/A'} kg",
    ]

    for line in info_lines:
        draw.text((x_offset, y_offset), line, font=font_small, fill='black')
        y_offset += int(line_height * 0.7)

    # Decision with color
    decision_color = 'green' if decision == 'ACCEPT' else 'red' if decision == 'REJECT' else 'orange'
    y_offset += int(line_height * 0.3)
    draw.text((x_offset, y_offset), f"Decision: {decision}", font=font_medium, fill=decision_color)

    # Save to buffer
    buffer = BytesIO()
    img.save(buffer, format='PNG', dpi=(300, 300))
    buffer.seek(0)

    return buffer
