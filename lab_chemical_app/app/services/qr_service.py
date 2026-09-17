"""
QR Code Service

QR codes, and page layout for batch sticker printing.

This module used to carry a second sticker renderer of its own — different
sizes, different fonts, no barcode — and every batch PDF went through it, so
printing a sheet gave you a different label from printing one pipe. The label
now has exactly one renderer, stickers.create_sticker_image; what is left here
is the QR helper and the tiling that arranges finished images on a page.
"""

from io import BytesIO
from typing import Dict

import qrcode
from PIL import Image


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
    Includes production order and all stages info.

    Args:
        pipe_info: Dict with pipe information

    Returns:
        Formatted string for QR code
    """
    fields = [
        f"NC:{pipe_info.get('no_code', '')}",
        f"L:{pipe_info.get('ladle_id', '')}",
        f"DN:{pipe_info.get('diameter', '')}",
        f"T:{pipe_info.get('pipe_class', '')}",
        f"D:{pipe_info.get('production_date', '')}",
        f"W:{pipe_info.get('weight', '')}kg",
        f"DEC:{pipe_info.get('decision', '')}",
        f"ORD:{pipe_info.get('order_number', '')}",
        f"CUST:{pipe_info.get('customer', '')}",
        f"STG:{pipe_info.get('stages', '')}"
    ]
    return '|'.join(fields)


def create_batch_stickers(sticker_pngs, width_mm, height_mm, gap_mm=5):
    """Tile already-rendered sticker PNGs onto A4 pages.

    Takes finished images rather than pipe data on purpose: the label is
    rendered once, by stickers.create_sticker_image, so a batch print and a
    single print are the same picture. This function decides only where each
    one goes on the page.

    Args:
        sticker_pngs: BytesIO PNG buffers, one per sticker, in print order.
        width_mm, height_mm: the size each was rendered at.
        gap_mm: whitespace between stickers, for the cutting line.

    Returns a BytesIO holding the PDF.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    margin = 10 * mm
    cell_w = width_mm * mm + gap_mm * mm
    cell_h = height_mm * mm + gap_mm * mm
    cols = max(int((page_width - 2 * margin) / cell_w), 1)
    rows = max(int((page_height - 2 * margin) / cell_h), 1)
    per_page = cols * rows

    for i, png in enumerate(sticker_pngs):
        slot = i % per_page
        col, row = slot % cols, slot // cols
        x = margin + col * cell_w
        y = page_height - margin - (row + 1) * cell_h

        png.seek(0)
        c.drawImage(
            ImageReader(png), x, y,
            width=width_mm * mm, height=height_mm * mm,
        )

        if slot == per_page - 1 and i < len(sticker_pngs) - 1:
            c.showPage()

    c.save()
    buffer.seek(0)
    return buffer


def parse_qr_data(qr_string: str) -> Dict:
    """
    Parse QR code data back to dict.
    Handles enhanced QR data with order and stages info.

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
                result['pipe_class'] = value
            elif key == 'D':
                result['production_date'] = value
            elif key == 'W':
                result['weight'] = value.replace('kg', '')
            elif key == 'DEC':
                result['decision'] = value
            elif key == 'ORD':
                result['order_number'] = value
            elif key == 'CUST':
                result['customer'] = value
            elif key == 'STG':
                result['stages'] = value

    return result
