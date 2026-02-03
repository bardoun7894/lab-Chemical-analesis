"""
AI Service - Google Gemini integration for auto-generating analysis notes
Uses Gemini Flash 2.0 for fast responses

Supports:
- Chemical Analysis: auto-fill reason, has_defect, notes
- Mechanical Tests: auto-fill decision, reason, comments
- Dashboard: daily AI summary
- Reports: AI-generated summaries
"""
import os
import json
import requests


# Gemini API Configuration
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_STREAM_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:streamGenerateContent"


def get_api_key():
    """Get Gemini API key from environment"""
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    return api_key


# =============================================================================
# MECHANICAL TEST AI ANALYSIS
# =============================================================================

def generate_mechanical_analysis(test_values):
    """
    Generate AI analysis for mechanical test results.

    Args:
        test_values: dict with tensile_strength, elongation, hardness,
                    nodularity_percent, carbides, etc.

    Returns:
        dict with 'decision', 'reason', 'comments', 'has_defect'
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        return {'error': str(e), 'decision': '', 'reason': '', 'comments': ''}

    prompt = build_mechanical_prompt(test_values)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_mechanical_response(response)
    except Exception as e:
        return {'error': str(e), 'decision': '', 'reason': '', 'comments': ''}


def build_mechanical_prompt(test_values):
    """Build prompt for mechanical test analysis"""

    values_text = ", ".join([
        f"{k}={v}" for k, v in test_values.items()
        if v is not None and v != ''
    ])

    prompt = f"""أنت خبير جودة في مصنع أنابيب حديد دكتايل. حلل نتائج الاختبار الميكانيكي.

نتائج الاختبار: {values_text}

المواصفات القياسية:
- قوة الشد (Tensile): >= 420 MPa
- الاستطالة (Elongation): >= 10%
- الصلادة (Hardness): 130-230 HB
- النودولارية (Nodularity): >= 80%
- الكربيدات (Carbides): < 2%

أجب JSON فقط:
{{"decision":"ACCEPT أو REJECT","reason":"سبب القرار بالعربية","comments":"ملاحظات وتوصيات بالعربية","has_defect":true/false}}"""

    return prompt


def parse_mechanical_response(response):
    """Parse Gemini response for mechanical test"""
    try:
        candidates = response.get('candidates', [])
        if not candidates:
            return {'decision': '', 'reason': '', 'comments': '', 'has_defect': False}

        text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')

        # Clean markdown
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]

        result = json.loads(text.strip())
        return {
            'decision': result.get('decision', ''),
            'reason': result.get('reason', ''),
            'comments': result.get('comments', ''),
            'has_defect': bool(result.get('has_defect', False)),
            'error': None
        }
    except Exception as e:
        return {'decision': '', 'reason': '', 'comments': '', 'has_defect': False, 'error': str(e)}


def generate_mechanical_stream(test_values):
    """Generate mechanical test analysis with streaming"""
    try:
        api_key = get_api_key()
    except ValueError as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return

    prompt = build_mechanical_prompt(test_values)

    try:
        response = call_gemini_api_stream(api_key, prompt)
        full_content = ""

        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str == '[DONE]':
                        break
                    try:
                        data = json.loads(data_str)
                        candidates = data.get('candidates', [])
                        if candidates:
                            text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                            if text:
                                full_content += text
                                yield f"data: {json.dumps({'chunk': text, 'type': 'content'})}\n\n"
                    except json.JSONDecodeError:
                        pass

        result = parse_mechanical_response({'candidates': [{'content': {'parts': [{'text': full_content}]}}]})
        yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


# =============================================================================
# DASHBOARD AI SUMMARY
# =============================================================================

def generate_dashboard_summary(stats):
    """
    Generate AI daily summary for dashboard.

    Args:
        stats: dict with today's statistics

    Returns:
        dict with 'summary', 'alerts', 'recommendations'
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        return {'error': str(e), 'summary': '', 'alerts': [], 'recommendations': []}

    prompt = build_dashboard_prompt(stats)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_dashboard_response(response)
    except Exception as e:
        return {'error': str(e), 'summary': '', 'alerts': [], 'recommendations': []}


def build_dashboard_prompt(stats):
    """Build prompt for dashboard summary"""

    prompt = f"""أنت مدير جودة في مصنع أنابيب حديد دكتايل. قم بتلخيص حالة الإنتاج اليومية.

إحصائيات اليوم:
- تحليلات كيميائية اليوم: {stats.get('chem_today', 0)}
- أنابيب اليوم: {stats.get('pipes_today', 0)}
- اختبارات ميكانيكية اليوم: {stats.get('mech_today', 0)}
- عيوب هذا الأسبوع: {stats.get('defects_week', 0)}
- نسبة القبول: {stats.get('acceptance_rate', 0)}%

أجب JSON فقط (بالعربية):
{{"summary":"ملخص قصير للحالة","alerts":["تنبيه 1","تنبيه 2"],"recommendations":["توصية 1","توصية 2"],"status":"good/warning/critical"}}"""

    return prompt


def parse_dashboard_response(response):
    """Parse Gemini response for dashboard summary"""
    try:
        candidates = response.get('candidates', [])
        if not candidates:
            return {'summary': '', 'alerts': [], 'recommendations': [], 'status': 'good'}

        text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')

        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]

        result = json.loads(text.strip())
        return {
            'summary': result.get('summary', ''),
            'alerts': result.get('alerts', []),
            'recommendations': result.get('recommendations', []),
            'status': result.get('status', 'good'),
            'error': None
        }
    except Exception as e:
        return {'summary': '', 'alerts': [], 'recommendations': [], 'status': 'good', 'error': str(e)}


# =============================================================================
# REPORT AI SUMMARY
# =============================================================================

def generate_report_summary(report_type, data):
    """
    Generate AI summary for reports.

    Args:
        report_type: 'chemical', 'production', 'defect'
        data: report data dict

    Returns:
        dict with 'summary', 'insights', 'recommendations'
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        return {'error': str(e), 'summary': '', 'insights': [], 'recommendations': []}

    prompt = build_report_prompt(report_type, data)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_report_response(response)
    except Exception as e:
        return {'error': str(e), 'summary': '', 'insights': [], 'recommendations': []}


def build_report_prompt(report_type, data):
    """Build prompt for report summary"""

    if report_type == 'chemical':
        prompt = f"""أنت خبير جودة. حلل تقرير التحليل الكيميائي.

البيانات:
- الفترة: {data.get('date_from')} إلى {data.get('date_to')}
- إجمالي التحليلات: {data.get('total', 0)}
- المقبولة: {data.get('accepted', 0)}
- المرفوضة: {data.get('rejected', 0)}
- العيوب: {data.get('defects', 0)}
- نسبة القبول: {data.get('rate', 0)}%

أجب JSON فقط (بالعربية):
{{"summary":"ملخص التقرير","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    elif report_type == 'defect':
        defects_info = ", ".join([f"{k}: {v}" for k, v in data.get('defects_by_stage', {}).items()])
        prompt = f"""أنت خبير جودة. حلل تقرير العيوب.

البيانات:
- الفترة: {data.get('date_from')} إلى {data.get('date_to')}
- عيوب التحليل الكيميائي: {data.get('chem_defects_count', 0)}
- عيوب المراحل: {defects_info}

أجب JSON فقط (بالعربية):
{{"summary":"ملخص العيوب","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    else:  # production
        prompt = f"""أنت خبير إنتاج. حلل تقرير الإنتاج اليومي.

البيانات:
- التاريخ: {data.get('date')}
- إجمالي الأنابيب: {data.get('total', 0)}
- حسب القطر: {data.get('by_diameter', {})}

أجب JSON فقط (بالعربية):
{{"summary":"ملخص الإنتاج","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    return prompt


def parse_report_response(response):
    """Parse Gemini response for report summary"""
    try:
        candidates = response.get('candidates', [])
        if not candidates:
            return {'summary': '', 'insights': [], 'recommendations': []}

        text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')

        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]

        result = json.loads(text.strip())
        return {
            'summary': result.get('summary', ''),
            'insights': result.get('insights', []),
            'recommendations': result.get('recommendations', []),
            'error': None
        }
    except Exception as e:
        return {'summary': '', 'insights': [], 'recommendations': [], 'error': str(e)}


def generate_analysis_notes(element_values, auto_decision_result):
    """
    Use Gemini to generate Reason, Has Defect, and Notes based on chemical analysis.

    Args:
        element_values: dict of element names to values
        auto_decision_result: result from calculate_auto_decision()

    Returns:
        dict with 'reason', 'has_defect', 'notes'
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        return {
            'error': str(e),
            'reason': '',
            'has_defect': False,
            'notes': ''
        }

    # Build the prompt
    prompt = build_analysis_prompt(element_values, auto_decision_result)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_gemini_response(response)
    except Exception as e:
        return {
            'error': str(e),
            'reason': '',
            'has_defect': False,
            'notes': ''
        }


def build_analysis_prompt(element_values, auto_decision_result):
    """Build the prompt for Gemini analysis"""

    # Format element values compactly
    elements_text = ", ".join([
        f"{name}={value}"
        for name, value in element_values.items()
        if value is not None and value != ''
    ])

    # Get decision info
    decision = auto_decision_result.get('recommended_decision', 'Unknown')
    worst_elements = auto_decision_result.get('worst_elements', [])

    prompt = f"""أنت خبير جودة في مصنع أنابيب حديد دكتايل.

التحليل الكيميائي: {elements_text}
القرار: {decision}
العناصر المؤثرة: {', '.join(worst_elements) if worst_elements else 'لا يوجد'}

المواصفات: C:3-3.9%, Si:1.86-2.7%, Mn:<0.4%, Mg:0.031-0.07%, S:<0.02%

أجب JSON فقط:
{{"reason":"سبب القرار بالعربية","has_defect":true/false,"notes":"توصيات بالعربية"}}"""

    return prompt


def call_gemini_api(api_key, prompt):
    """Call the Gemini API"""
    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2500
        }
    }

    url = f"{GEMINI_API_URL}?key={api_key}"

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90
    )

    response.raise_for_status()
    return response.json()


def call_gemini_api_stream(api_key, prompt):
    """Call the Gemini API with streaming enabled"""
    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2500
        }
    }

    url = f"{GEMINI_STREAM_URL}?key={api_key}&alt=sse"

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90,
        stream=True
    )

    response.raise_for_status()
    return response


def generate_analysis_stream(element_values, auto_decision_result):
    """
    Generate AI analysis with streaming response.
    Yields chunks of text as they arrive.
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return

    prompt = build_analysis_prompt(element_values, auto_decision_result)

    try:
        response = call_gemini_api_stream(api_key, prompt)

        full_content = ""

        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str == '[DONE]':
                        break
                    try:
                        data = json.loads(data_str)

                        # Extract text from Gemini response
                        candidates = data.get('candidates', [])
                        if candidates:
                            content = candidates[0].get('content', {})
                            parts = content.get('parts', [])
                            if parts:
                                text_chunk = parts[0].get('text', '')
                                if text_chunk:
                                    full_content += text_chunk
                                    yield f"data: {json.dumps({'chunk': text_chunk, 'type': 'content'})}\n\n"

                    except json.JSONDecodeError:
                        pass

        # Parse final result
        result = parse_streamed_content(full_content)
        yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


def parse_streamed_content(content):
    """Parse the streamed content to extract reason, has_defect, notes"""
    import re

    reason = ''
    notes = ''
    has_defect = False

    # Try to parse as JSON first
    try:
        # Clean up markdown
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0]
        elif '```' in content:
            content = content.split('```')[1].split('```')[0]

        data = json.loads(content.strip())
        return {
            'reason': data.get('reason', ''),
            'has_defect': bool(data.get('has_defect', False)),
            'notes': data.get('notes', '')
        }
    except:
        pass

    # Fallback: extract with regex
    reason_match = re.search(r'"reason"\s*:\s*"([^"]+)"', content)
    if reason_match:
        reason = reason_match.group(1)

    notes_match = re.search(r'"notes"\s*:\s*"([^"]+)"', content)
    if notes_match:
        notes = notes_match.group(1)

    defect_match = re.search(r'"has_defect"\s*:\s*(true|false)', content, re.IGNORECASE)
    if defect_match:
        has_defect = defect_match.group(1).lower() == 'true'

    return {
        'reason': reason,
        'has_defect': has_defect,
        'notes': notes
    }


def parse_gemini_response(response):
    """Parse the Gemini API response"""
    try:
        import re

        # Extract the content from the response
        candidates = response.get('candidates', [])
        if not candidates:
            return {
                'reason': '',
                'has_defect': False,
                'notes': '',
                'error': 'No response from Gemini'
            }

        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        if not parts:
            return {
                'reason': '',
                'has_defect': False,
                'notes': '',
                'error': 'No content in response'
            }

        text = parts[0].get('text', '')

        # Try to parse as JSON
        # Handle case where response might have markdown code blocks
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]

        result = json.loads(text.strip())

        return {
            'reason': result.get('reason', ''),
            'has_defect': bool(result.get('has_defect', False)),
            'notes': result.get('notes', ''),
            'error': None
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        # If parsing fails, try to extract useful info from raw text
        import re

        candidates = response.get('candidates', [])
        if candidates:
            content = candidates[0].get('content', {})
            parts = content.get('parts', [])
            text = parts[0].get('text', '') if parts else ''
        else:
            text = ''

        # Try to extract reason from the text
        reason = ''
        notes = ''
        has_defect = False

        # Look for "reason": "..." pattern
        reason_match = re.search(r'"reason"\s*:\s*"([^"]+)"', text)
        if reason_match:
            reason = reason_match.group(1)

        # Look for "notes": "..." pattern
        notes_match = re.search(r'"notes"\s*:\s*"([^"]+)"', text)
        if notes_match:
            notes = notes_match.group(1)

        # Look for "has_defect": true/false
        defect_match = re.search(r'"has_defect"\s*:\s*(true|false)', text, re.IGNORECASE)
        if defect_match:
            has_defect = defect_match.group(1).lower() == 'true'

        return {
            'reason': reason,
            'has_defect': has_defect,
            'notes': notes,
            'error': None if reason else f'Failed to parse response: {str(e)}'
        }
