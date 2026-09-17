"""
Mechanical Test OCR — Extract test measurements from mechanical test
certificates/printouts using Gemini Vision.
"""
import json
import re
from app.services.ocr_service import call_vision_api, _detect_mime


MECHANICAL_EXTRACTION_PROMPT = """You are analyzing a mechanical test report/certificate from a ductile iron pipe factory.

Extract the following measurement values from the image:
- sample_thickness: Sample thickness in mm
- d1: Diameter measurement 1 in mm
- d2: Diameter measurement 2 in mm
- d3: Diameter measurement 3 in mm
- force_kgf: Force in Kgf (kilogram-force)
- final_length: Final gauge length Lf in mm
- nodularity_percent: Nodularity percentage (%Nd)
- hardness: Hardness in HB (Brinell)
- carbides: Carbides percentage
- nodule_count: Nodule count (NC)

RULES:
1. Use dots for decimal points
2. If a value is not visible or unclear, set it to null
3. Return ONLY valid JSON, no markdown

Return this JSON format:
{"sample_thickness": 3.8, "d1": 3.7, "d2": 3.9, "d3": 3.7, "force_kgf": 500.0, "final_length": 48.0, "nodularity_percent": 85.0, "hardness": 220, "carbides": 0.9, "nodule_count": 405}
"""


def extract_mechanical_from_image(image_bytes, filename='image.jpg'):
    """Extract mechanical test values from a test certificate image.

    Uses the admin-configurable "microstructure_image" prompt override when
    set; otherwise falls back to MECHANICAL_EXTRACTION_PROMPT.
    """
    from app.services.ai_service import get_prompt_template
    prompt = get_prompt_template("microstructure_image", MECHANICAL_EXTRACTION_PROMPT)
    mime_type = _detect_mime(filename)
    raw_text = call_vision_api(image_bytes, prompt, mime_type)

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
            continue
        try:
            result[key] = round(float(value), 3)
        except (TypeError, ValueError):
            result[key] = None

    return result
