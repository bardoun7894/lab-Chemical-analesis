"""
Pipe OCR — Extract pipe data from pipe labels, marking sheets, or production
documents using Gemini Vision.
"""
import json
import re
from app.services.ocr_service import call_gemini_vision_api, _detect_mime


PIPE_EXTRACTION_PROMPT = """You are analyzing a pipe production document, label, or marking sheet from a ductile iron pipe factory.

Extract the following information from the image:
- no_code: Pipe number/code (e.g., N8739, P101)
- diameter: Pipe diameter DN in mm (e.g., 100, 300, 500, 600, 800)
- pipe_class: Pipe class (K9, C25, C40, C50, C64, Fittings)
- actual_weight: Actual weight in kg
- iso_weight: ISO/standard weight in kg
- mold_number: Mold number
- ladle_id: Ladle ID (8-14 digit number)
- production_date: Production date (YYYY-MM-DD format)
- shift: Shift number (1, 2, or 3)
- thickness: Wall thickness (Socket) in mm
- thickness_spigot: Spigot thickness in mm

RULES:
1. Use dots for decimal points
2. For diameter, extract only the number (e.g., if "DN300" extract 300)
3. For dates, convert to YYYY-MM-DD format
4. If a value is not visible or unclear, set it to null
5. Return ONLY valid JSON

Return this JSON format:
{"no_code": "N8739", "diameter": 300, "pipe_class": "K9", "actual_weight": 144.0, "iso_weight": 150.0, "mold_number": "1111", "ladle_id": "4713012026", "production_date": "2026-04-12", "shift": 1, "thickness": 7.5, "thickness_spigot": 7.2}
"""


PRODUCTION_ORDER_PROMPT = """You are analyzing a production order, purchase order, or sales order document from a ductile iron pipe factory.

Extract the following information from the image:
- order_number: Order/PO number
- customer_name: Customer name
- sales_number: Sales order number
- target_quantity: Target quantity (number of pipes)
- diameter: Pipe diameter DN in mm
- pipe_class: Pipe class (K9, C25, C40)
- product_code: Product code
- order_date: Order date (YYYY-MM-DD format)
- notes: Any special notes or specifications

RULES:
1. Convert dates to YYYY-MM-DD format
2. Extract numbers without units where applicable
3. If a value is not visible, set it to null
4. Return ONLY valid JSON

Return this JSON format:
{"order_number": "PO-20260412-001", "customer_name": "Fluid System", "sales_number": "SO000100", "target_quantity": 100, "diameter": 300, "pipe_class": "K9", "product_code": "10C40Z1360LHC00", "order_date": "2026-04-12", "notes": null}
"""


def extract_pipe_from_image(image_bytes, filename='image.jpg'):
    """Extract pipe data from a pipe label/marking image."""
    mime_type = _detect_mime(filename)
    raw_text = call_gemini_vision_api(image_bytes, PIPE_EXTRACTION_PROMPT, mime_type)

    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', cleaned, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
        else:
            raise ValueError(f'Could not parse response: {cleaned[:300]}')

    # Type-coerce fields
    result = {}
    for key, value in parsed.items():
        if value is None:
            result[key] = None
        elif key in ('diameter', 'shift'):
            try:
                result[key] = int(value)
            except (TypeError, ValueError):
                result[key] = None
        elif key in ('actual_weight', 'iso_weight', 'thickness', 'thickness_spigot', 'target_quantity'):
            try:
                result[key] = round(float(value), 2)
            except (TypeError, ValueError):
                result[key] = None
        else:
            result[key] = str(value).strip() if value else None

    return result


def extract_order_from_image(image_bytes, filename='image.jpg'):
    """Extract production order data from an order document image."""
    mime_type = _detect_mime(filename)
    raw_text = call_gemini_vision_api(image_bytes, PRODUCTION_ORDER_PROMPT, mime_type)

    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', cleaned, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
        else:
            raise ValueError(f'Could not parse response: {cleaned[:300]}')

    result = {}
    for key, value in parsed.items():
        if value is None:
            result[key] = None
        elif key in ('diameter', 'target_quantity'):
            try:
                result[key] = int(value)
            except (TypeError, ValueError):
                result[key] = None
        else:
            result[key] = str(value).strip() if value else None

    return result
