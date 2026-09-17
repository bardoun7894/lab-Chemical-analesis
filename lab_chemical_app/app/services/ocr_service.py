"""
OCR Service — Extract chemical element values from spectrometer printout images
using Gemini Vision API (multimodal).

No external OCR dependencies needed — uses the same Gemini model already
configured for text generation, but with image input via inlineData.
"""
import base64
import json
import re
import requests
from io import BytesIO
from PIL import Image


# Element field mapping: AI output key → ChemicalAnalysis model field
ELEMENT_MAP = {
    'c': 'carbon', 'carbon': 'carbon', 'C': 'carbon',
    'si': 'silicon', 'silicon': 'silicon', 'Si': 'silicon',
    'mg': 'magnesium', 'magnesium': 'magnesium', 'Mg': 'magnesium',
    'cu': 'copper', 'copper': 'copper', 'Cu': 'copper',
    'cr': 'chromium', 'chromium': 'chromium', 'Cr': 'chromium',
    's': 'sulfur', 'sulfur': 'sulfur', 'S': 'sulfur',
    'mn': 'manganese', 'manganese': 'manganese', 'Mn': 'manganese',
    'p': 'phosphorus', 'phosphorus': 'phosphorus', 'P': 'phosphorus',
    'pb': 'lead', 'lead': 'lead', 'Pb': 'lead',
    'al': 'aluminum', 'aluminum': 'aluminum', 'Al': 'aluminum',
}

# Reasonable ranges for validation (reject obviously wrong OCR values)
ELEMENT_RANGES = {
    'carbon':     (0.0, 10.0),
    'silicon':    (0.0, 10.0),
    'magnesium':  (0.0, 1.0),
    'copper':     (0.0, 1.0),
    'chromium':   (0.0, 1.0),
    'sulfur':     (0.0, 1.0),
    'manganese':  (0.0, 2.0),
    'phosphorus': (0.0, 1.0),
    'lead':       (0.0, 0.1),
    'aluminum':   (0.0, 1.0),
}


EXTRACTION_PROMPT = """You are analyzing a spectrometer printout from a ductile iron pipe foundry.

Extract the RESULT percentage values for these chemical elements from the image:
- C (Carbon)
- Si (Silicon)
- Mg (Magnesium)
- Cu (Copper)
- Cr (Chromium)
- S (Sulfur)
- Mn (Manganese)
- P (Phosphorus)
- Pb (Lead)
- Al (Aluminum)

WHICH ROW TO READ — work down this list and stop at the first case that matches
the sheet in front of you. Different machines print different layouts, so all of
these are valid; only the order of preference is fixed.

1. A row highlighted in green (or light-green / pistachio). Some sheets print
   several individual burns in blue or plain white and then one green row that
   is the average of them. That green row wins over everything else, including
   a row labelled AVG.
2. A row labelled AVG, AVE, Average, Mean, or المتوسط.
3. Exactly one row of readings and nothing else — read that row.
4. Several unlabelled reading rows with no green row and no AVG row — compute
   the arithmetic mean of those reading rows yourself, per element.

NEVER read from: standard-deviation / SD / RSD / uncertainty rows, rows that are
all zeros, specification-limit rows (min/max), or any total/count row.

OTHER RULES:
- Read each value from the cell directly under its own element header. Element
  headers may carry a channel suffix (C2, Si1, Mg0, Cu5, Mn3, P1) — match on the
  element symbol and ignore the digit.
- The sheet may print many more columns than the ten asked for. Ignore the rest.
- Values are percentages (e.g. C = 3.52 means 3.52%).
- Use dots for decimal points, not commas.
- If a value is not visible, or the chosen row is unreadable, or you are not
  confident, set that element to null. Never guess and never substitute a value
  from a different row.
- Return ONLY valid JSON, no markdown, no explanation.

Return this exact JSON shape. The 99.9s below are placeholders showing the
format only — they are not data. Never copy them into your answer; every number
you return must come from the row you chose in the image, or be null.
{"carbon": 99.9, "silicon": 99.9, "magnesium": 99.9, "copper": 99.9, "chromium": 99.9, "sulfur": 99.9, "manganese": 99.9, "phosphorus": 99.9, "lead": 99.9, "aluminum": 99.9}
"""


def _get_ocr_provider():
    """Which provider to use for vision/OCR: 'gemini' (default) or 'openrouter'.

    Admin-configurable (Admin > AI Settings > OCR provider) so the vision path
    can be switched independently of the text provider.
    """
    try:
        from app.services.ai_service import get_ai_settings
        return (get_ai_settings().get('ocr_provider') or 'gemini').lower()
    except Exception:
        return 'gemini'


def _get_gemini_vision_key():
    """Gemini key for vision (env first, then AI settings)."""
    import os
    key = os.environ.get('GEMINI_API_KEY', '')
    if key:
        return key
    try:
        from app.services.ai_service import get_ai_settings
        settings = get_ai_settings()
        return settings.get('gemini_api_key', '')
    except Exception:
        return ''


def _get_vision_model():
    try:
        from app.services.ai_service import get_ai_settings
        settings = get_ai_settings()
        return settings.get('vision_model') or settings.get('gemini_model') or 'gemini-2.0-flash'
    except Exception:
        return 'gemini-2.0-flash'


def _resize_image(image_bytes, max_bytes=4_000_000):
    """Resize image if it exceeds the max size for Gemini inline data."""
    if len(image_bytes) <= max_bytes:
        return image_bytes
    img = Image.open(BytesIO(image_bytes))
    quality = 85
    while quality > 20:
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
        quality -= 10
    # Last resort: shrink dimensions
    img.thumbnail((1600, 1600))
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=70)
    return buf.getvalue()


def _detect_mime(filename):
    ext = (filename or '').rsplit('.', 1)[-1].lower()
    return {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'gif': 'image/gif',
        'bmp': 'image/bmp', 'webp': 'image/webp',
    }.get(ext, 'image/jpeg')


def call_gemini_vision_api(image_bytes, text_prompt, mime_type='image/jpeg'):
    """Call Gemini API with multimodal input (text + image).

    Returns the raw text response from the model.
    """
    api_key = _get_gemini_vision_key()
    if not api_key:
        raise ValueError('Gemini API key is not configured. Set GEMINI_API_KEY or configure in Admin > AI Settings.')

    model = _get_vision_model()
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'

    # Resize if needed
    image_bytes = _resize_image(image_bytes)
    b64_image = base64.b64encode(image_bytes).decode('utf-8')

    payload = {
        'contents': [{
            'parts': [
                {'text': text_prompt},
                {'inlineData': {'mimeType': mime_type, 'data': b64_image}},
            ]
        }],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 1000,
        },
    }

    response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=60)
    response.raise_for_status()

    data = response.json()
    # Extract text from Gemini response
    try:
        text = data['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        raise ValueError(f'Unexpected Gemini response format: {json.dumps(data)[:500]}')

    return text


def call_openrouter_vision_api(image_bytes, text_prompt, mime_type='image/jpeg'):
    """Call OpenRouter (chat/completions) with a vision-capable model.

    Uses the OCR-specific model if set, else the general OpenRouter model, else
    a sane vision default. The general openrouter_model (e.g. google/gemini-2.5-pro)
    is multimodal, so it works for OCR too.
    """
    import os
    from app.services.ai_service import get_ai_settings
    settings = get_ai_settings()
    api_key = os.environ.get('OPENROUTER_API_KEY', '') or settings.get('openrouter_api_key', '')
    if not api_key:
        raise ValueError('OpenRouter API key is not configured. Set it in Admin > AI Settings.')

    model = (settings.get('ocr_openrouter_model')
             or settings.get('openrouter_model')
             or 'google/gemini-2.5-flash')

    image_bytes = _resize_image(image_bytes)
    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f'data:{mime_type};base64,{b64_image}'

    payload = {
        'model': model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': text_prompt},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ],
        }],
        'temperature': 0.1,
        'max_tokens': 1000,
    }

    response = requests.post(
        'https://openrouter.ai/api/v1/chat/completions',
        json=payload,
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError):
        raise ValueError(f'Unexpected OpenRouter response format: {json.dumps(data)[:500]}')


def call_vision_api(image_bytes, text_prompt, mime_type='image/jpeg'):
    """Dispatch a vision/OCR call to the configured provider.

    Honours the admin ``ocr_provider`` setting (gemini | openrouter) so OCR can
    be switched independently of the text-generation provider.
    """
    if _get_ocr_provider() == 'openrouter':
        return call_openrouter_vision_api(image_bytes, text_prompt, mime_type)
    return call_gemini_vision_api(image_bytes, text_prompt, mime_type)


def extract_elements_from_image(image_bytes, filename='image.jpg'):
    """Extract chemical element percentages from a spectrometer printout image.

    Returns dict: {carbon: float, silicon: float, ...} with null for undetected values.
    """
    mime_type = _detect_mime(filename)

    # Call the configured vision provider (Gemini or OpenRouter)
    raw_text = call_vision_api(image_bytes, EXTRACTION_PROMPT, mime_type)

    # Parse JSON from response — strip markdown fences if present
    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r'\{[^}]+\}', cleaned, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
        else:
            raise ValueError(f'Could not parse AI response as JSON: {cleaned[:300]}')

    # Normalize keys and validate values
    result = {}
    for raw_key, value in parsed.items():
        normalized_key = ELEMENT_MAP.get(raw_key) or ELEMENT_MAP.get(raw_key.lower())
        if not normalized_key:
            continue
        if value is None:
            result[normalized_key] = None
            continue
        try:
            fval = float(value)
        except (TypeError, ValueError):
            result[normalized_key] = None
            continue
        # Sanity check
        lo, hi = ELEMENT_RANGES.get(normalized_key, (0, 100))
        if lo <= fval <= hi:
            result[normalized_key] = round(fval, 4)
        else:
            result[normalized_key] = None  # out of range, likely OCR error

    return result
