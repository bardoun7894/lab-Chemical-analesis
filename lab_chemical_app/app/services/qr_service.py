"""
QR Code Service

Generates QR codes and sticker images for pipes.
"""

from io import BytesIO
from typing import Optional, Dict
import qrcode
from PIL import Image, ImageDraw, ImageFont


# Sticker sizes in mm
STICKER_SIZES = {
    'small': (50, 30),
    'medium': (70, 50),
    'large': (100, 70),
}

# Convert mm to pixels (300 DPI)
MM_TO_PX = 300 / 25.4


def generate_qr_code(data: str, size: int = 200, border: int = 2) -> Image.Image:
    """
    Generate QR code image.

    Args:
        data: Data to encode in QR code
        size: Size of QR code in pixels
        border: Border size in QR modules

    Returns:
        PIL Image object
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    qr_image = qr.make_image(fill_color="black", back_color="white")
    qr_image = qr_image.resize((size, size))

    return qr_image


def create_pipe_qr_data(pipe_info: Dict) -> str:
    """
    Create QR code data string for a pipe.

    Args:
        pipe_info: Dict with pipe information

    Returns:
        Formatted string for QR code
    """
    fields = [
        f"NC:{pipe_info.get('no_code', '')}",
        f"L:{pipe_info.get('ladle_id', '')}",
        f"DN:{pipe_info.get('diameter', '')}",
        f"T:{pipe_info.get('pipe_type', '')}",
        f"D:{pipe_info.get('production_date', '')}",
        f"W:{pipe_info.get('weight', '')}kg",
        f"DEC:{pipe_info.get('decision', '')}"
    ]
    return '|'.join(fields)


def create_sticker_image(
    pipe_info: Dict,
    size_name: str = 'medium',
    custom_width: Optional[int] = None,
    custom_height: Optional[int] = None
) -> BytesIO:
    """
    Create sticker image with pipe info and QR code.

    Args:
        pipe_info: Dict with pipe information
        size_name: Preset size name ('small', 'medium', 'large')
        custom_width: Custom width in mm (overrides size_name)
        custom_height: Custom height in mm (overrides size_name)

    Returns:
        BytesIO buffer containing PNG image
    """
    # Determine dimensions
    if custom_width and custom_height:
        width_mm, height_mm = custom_width, custom_height
    else:
        width_mm, height_mm = STICKER_SIZES.get(size_name, STICKER_SIZES['medium'])

    # Convert to pixels
    width_px = int(width_mm * MM_TO_PX)
    height_px = int(height_mm * MM_TO_PX)

    # Create white background
    img = Image.new('RGB', (width_px, height_px), 'white')
    draw = ImageDraw.Draw(img)

    # Try to load fonts
    try:
        font_large = ImageFont.truetype("arial.ttf", int(height_px * 0.10))
        font_medium = ImageFont.truetype("arial.ttf", int(height_px * 0.07))
        font_small = ImageFont.truetype("arial.ttf", int(height_px * 0.055))
    except:
        font_large = ImageFont.load_default()
        font_medium = font_large
        font_small = font_large

    # Generate QR code
    qr_data = create_pipe_qr_data(pipe_info)
    qr_size = int(min(width_px, height_px) * 0.45)
    qr_image = generate_qr_code(qr_data, qr_size)

    # Place QR code on right side
    qr_x = width_px - qr_size - int(width_px * 0.03)
    qr_y = int(height_px * 0.20)
    img.paste(qr_image, (qr_x, qr_y))

    # Draw border
    draw.rectangle([(2, 2), (width_px-3, height_px-3)], outline='black', width=2)

    # Draw text on left side
    x_offset = int(width_px * 0.03)
    y_offset = int(height_px * 0.05)
    line_height = int(height_px * 0.11)

    # Title
    draw.text((x_offset, y_offset), "PIPE LABEL", font=font_large, fill='black')
    y_offset += line_height

    # Separator line
    draw.line([(x_offset, y_offset), (qr_x - 10, y_offset)], fill='black', width=1)
    y_offset += int(line_height * 0.3)

    # Pipe info lines
    info_lines = [
        f"No. Code: {pipe_info.get('no_code', 'N/A')}",
        f"Ladle#: {pipe_info.get('ladle_id', 'N/A')}",
        f"DN: {pipe_info.get('diameter', 'N/A')}  Type: {pipe_info.get('pipe_type', 'N/A')}",
        f"Date: {pipe_info.get('production_date', 'N/A')}",
        f"Weight: {pipe_info.get('weight', 'N/A')} kg",
    ]

    for line in info_lines:
        draw.text((x_offset, y_offset), line, font=font_small, fill='black')
        y_offset += int(line_height * 0.65)

    # Decision with color
    decision = pipe_info.get('decision', 'N/A')
    if decision == 'ACCEPT':
        decision_color = 'green'
    elif decision == 'REJECT':
        decision_color = 'red'
    else:
        decision_color = 'orange'

    y_offset += int(line_height * 0.2)
    draw.text((x_offset, y_offset), f"Decision: {decision}", font=font_medium, fill=decision_color)

    # Save to buffer
    buffer = BytesIO()
    img.save(buffer, format='PNG', dpi=(300, 300))
    buffer.seek(0)

    return buffer


def create_batch_stickers(
    pipes: list,
    size_name: str = 'medium',
    per_page: int = 4
) -> BytesIO:
    """
    Create PDF with multiple stickers for batch printing.

    Args:
        pipes: List of pipe info dicts
        size_name: Sticker size
        per_page: Number of stickers per page

    Returns:
        BytesIO buffer containing PDF
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    page_width, page_height = A4

    # Get sticker size
    width_mm, height_mm = STICKER_SIZES.get(size_name, STICKER_SIZES['medium'])

    # Calculate grid
    margin = 10 * mm
    cols = int((page_width - 2 * margin) / (width_mm * mm + 5 * mm))
    rows = int((page_height - 2 * margin) / (height_mm * mm + 5 * mm))

    if cols < 1:
        cols = 1
    if rows < 1:
        rows = 1

    stickers_per_page = cols * rows

    current_page = 0
    sticker_idx = 0

    for i, pipe_info in enumerate(pipes):
        # Calculate position
        page_idx = i % stickers_per_page
        col = page_idx % cols
        row = page_idx // cols

        x = margin + col * (width_mm * mm + 5 * mm)
        y = page_height - margin - (row + 1) * (height_mm * mm + 5 * mm)

        # Generate sticker image
        sticker_buffer = create_sticker_image(pipe_info, size_name)
        sticker_img = Image.open(sticker_buffer)

        # Save temp image for ReportLab
        temp_buffer = BytesIO()
        sticker_img.save(temp_buffer, format='PNG')
        temp_buffer.seek(0)

        # Draw on canvas
        c.drawImage(
            temp_buffer,
            x, y,
            width=width_mm * mm,
            height=height_mm * mm
        )

        # New page if needed
        if page_idx == stickers_per_page - 1 and i < len(pipes) - 1:
            c.showPage()

    c.save()
    buffer.seek(0)

    return buffer


def parse_qr_data(qr_string: str) -> Dict:
    """
    Parse QR code data back to dict.

    Args:
        qr_string: QR code data string

    Returns:
        Dict with parsed values
    """
    result = {}
    parts = qr_string.split('|')

    for part in parts:
        if ':' in part:
            key, value = part.split(':', 1)
            if key == 'NC':
                result['no_code'] = value
            elif key == 'L':
                result['ladle_id'] = value
            elif key == 'DN':
                result['diameter'] = value
            elif key == 'T':
                result['pipe_type'] = value
            elif key == 'D':
                result['production_date'] = value
            elif key == 'W':
                result['weight'] = value.replace('kg', '')
            elif key == 'DEC':
                result['decision'] = value

    return result
