"""
AI Service - Multi-provider support
Supports:
- Google Gemini (default)
- OpenRouter (alternative)

Features:
- Chemical Analysis: auto-fill reason, has_defect, notes
- Mechanical Tests: auto-fill decision, reason, comments
- Dashboard: daily AI summary
- Reports: AI-generated summaries
"""

import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

# Path to app settings JSON
APP_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app_settings.json"
)

# ---------------------------------------------------------------------------
# Prompt template system
# ---------------------------------------------------------------------------


def get_prompt_template(key: str, default_template: str) -> str:
    """Return the admin-configured prompt override for *key*, or *default_template*.

    An override is used only when it is non-empty. An empty string (or a
    missing key) means "use the default".
    """
    try:
        settings = load_app_settings()
        override = settings.get("ai", {}).get("prompts", {}).get(key, "")
        if override and override.strip():
            return override.strip()
    except Exception as exc:
        logger.warning("get_prompt_template: could not load settings: %s", exc)
    return default_template


def _safe_format(template: str, fmt_kwargs: dict, default_template: str) -> str:
    """Substitute the known ``{placeholder}`` tokens in *template*.

    Uses plain string replacement — NOT ``str.format`` — on purpose: these
    prompts contain literal JSON braces (e.g. ``{"decision":"ACCEPT"}``) as the
    required output example. ``str.format``/``format_map`` treat those braces as
    fields and raise KeyError, which used to make every JSON-bearing custom
    prompt silently fall back to the default (the professional prompt never
    ran). Replacing only the named keys leaves all other braces untouched.
    """
    def _apply(tmpl):
        out = tmpl
        for key, value in fmt_kwargs.items():
            out = out.replace("{" + key + "}", str(value))
        return out

    try:
        return _apply(template)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "_safe_format: override template failed (%s), falling back to default", exc
        )
        try:
            return _apply(default_template)
        except Exception:
            return default_template


# =============================================================================
# Default prompt templates — single source of truth.
# NOTE: single braces only. _safe_format substitutes {named} placeholders via
# plain string replacement, so JSON braces here are emitted literally. Preserve
# the placeholder names ({test_results} / {elements_text} / {decision} /
# {worst_elements}) and the exact JSON output keys — the parser depends on them.
# ISO 2531 / EN 545 thresholds below are load-bearing; do not change the numbers.
# =============================================================================
PROFESSIONAL_MECHANICAL_PROMPT = (
    "You are a senior metallurgical quality-control engineer at a ductile iron "
    "pressure-pipe foundry operating to ISO 2531 and EN 545. Evaluate the "
    "mechanical and microstructural results below and issue a defensible "
    "ACCEPT or REJECT verdict.\n\n"
    "TEST RESULTS:\n{test_results}\n\n"
    "ACCEPTANCE CRITERIA (ISO 2531 / EN 545):\n"
    "- Tensile strength (Rm): >= 42.50 KgF/mm^2 (>= 420 MPa). "
    "Convert with Rm[MPa] = Rm[KgF/mm^2] x 9.8.\n"
    "- Elongation at fracture (A): >= 10 %\n"
    "- Brinell hardness (HB): 130 - 230 HB\n"
    "- Nodularity: >= 80 % (target >= 85 %)\n"
    "- Ferrite: >= 70 %\n"
    "- Nodule count: >= 400 nodules/mm^2\n"
    "- Carbides: < 1 % (tolerable up to 2 %)\n\n"
    "EVALUATION RULES:\n"
    "1. ACCEPT only when every MEASURED property meets its criterion; "
    "REJECT if any measured property fails.\n"
    "2. Treat missing or 'not measured' values as not evaluated — never reject "
    "on their absence.\n"
    "3. Respect units precisely (e.g. 44.9 KgF/mm^2 = 440 MPa -> PASS).\n"
    "4. Justify the verdict from the actual measured values and the specific "
    "criteria they meet or miss.\n"
    "5. Set has_defect true only for a genuine metallurgical non-conformity.\n\n"
    "OUTPUT — return exactly one JSON object, no markdown, no text outside JSON:\n"
    '{"decision":"ACCEPT","reason":"concise metallurgical justification citing '
    'the actual values and units","comments":"actionable observations or process '
    'recommendations","has_defect":false,"confidence":"high"}\n\n'
    "Reply in the language of the test context — Arabic if the values are "
    "labelled in Arabic, otherwise English."
)

PROFESSIONAL_CHEMICAL_PROMPT = (
    "أنت مهندس جودة أول في مصنع أنابيب ضغط من حديد الدكتايل، وفق مواصفات "
    "ISO 2531 / EN 545. راجِع التحليل الكيميائي التالي وبرّر القرار الصادر "
    "استناداً إلى القيم الفعلية.\n\n"
    "التحليل الكيميائي: {elements_text}\n"
    "القرار الحالي: {decision}\n"
    "العناصر الأكثر تأثيراً: {worst_elements}\n\n"
    "حدود التركيب الكيميائي المعتمدة:\n"
    "- الكربون (C): 3.0 – 3.9 %\n"
    "- السيليكون (Si): 1.86 – 2.7 %\n"
    "- المنجنيز (Mn): أقل من 0.4 %\n"
    "- المغنيسيوم (Mg): 0.031 – 0.07 %\n"
    "- الكبريت (S): أقل من 0.02 %\n\n"
    "قواعد التقييم: اذكر سبباً دقيقاً مبنياً على القيم المقاسة والعناصر الخارجة "
    "عن الحدود؛ لا تبنِ القرار على قيمة غير مقاسة.\n\n"
    "أعِد كائن JSON واحداً فقط دون أي نص خارجه:\n"
    '{"reason":"سبب القرار بالعربية مستنداً إلى القيم الفعلية",'
    '"has_defect":false,"notes":"توصيات فنية عملية بالعربية"}'
)


def get_default_prompts() -> dict:
    """Return the default (hardcoded) prompt texts for all 4 editable prompts.

    Used by the admin UI to show "reset to default" values.
    """
    from app.services.mechanical_ocr_service import MECHANICAL_EXTRACTION_PROMPT
    from app.services.agent_service import DEFAULT_CHATBOT_PROMPT

    return {
        "chatbot_system": DEFAULT_CHATBOT_PROMPT,
        "mechanical_decision": PROFESSIONAL_MECHANICAL_PROMPT,
        "chemical_decision": PROFESSIONAL_CHEMICAL_PROMPT,
        "auto_decision_summary": PROFESSIONAL_CHEMICAL_PROMPT,
        "microstructure_image": MECHANICAL_EXTRACTION_PROMPT,
    }


def load_app_settings():
    """Load app settings from JSON file"""
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ai": {"provider": "gemini", "enabled": True}}


def get_ai_settings():
    """The ``ai`` section of app settings ({} if missing)."""
    return load_app_settings().get("ai", {})


def get_ai_provider():
    """Get the AI provider from settings"""
    return get_ai_settings().get("provider", "gemini")


def get_gemini_model():
    """Get Gemini model from settings (empty string falls back to default)"""
    return get_ai_settings().get("gemini_model") or "gemini-2.5-flash"


def get_openrouter_model():
    """Get OpenRouter model from settings (empty string falls back to default)"""
    return get_ai_settings().get("openrouter_model") or "qwen/qwen3-next-80b-a3b-instruct:free"


def get_openrouter_fallback_models():
    """Get fallback models for OpenRouter - returns list of model IDs"""
    settings = load_app_settings()
    fallback_str = settings.get("ai", {}).get("openrouter_fallback_models", "")
    if fallback_str:
        models = [m.strip() for m in fallback_str.split(",") if m.strip()]
        return models
    return []


def is_auto_fallback_enabled():
    """Check if auto-fallback is enabled"""
    settings = load_app_settings()
    return settings.get("ai", {}).get("enable_auto_fallback", True)


def get_all_openrouter_models():
    """Get all models (primary + fallbacks) as a list"""
    primary = get_openrouter_model()
    fallbacks = get_openrouter_fallback_models()
    if primary:
        return [primary] + fallbacks
    return fallbacks


def extract_openrouter_content(message):
    """Normalize an OpenRouter message's content to a plain string.

    content may be None (reasoning models, refusals) or a list of parts
    on some providers — both would crash naive string handling.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            (p.get("text", "") if isinstance(p, dict) else str(p)) for p in content
        )
    return content or ""


def extract_first_choice_text(result, provider):
    """Pull the text out of a parsed API response dict for either provider.

    Returns "" on any shape mismatch — never raises.
    """
    if not isinstance(result, dict):
        return ""
    try:
        if provider == "openrouter":
            choices = result.get("choices") or []
            if choices and choices[0]:
                return extract_openrouter_content(choices[0].get("message"))
        else:
            candidates = result.get("candidates") or []
            if candidates and candidates[0]:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                if parts and parts[0]:
                    return parts[0].get("text") or ""
    except (AttributeError, TypeError, IndexError):
        pass
    return ""


# Get current provider and configure URLs
PROVIDER = get_ai_provider()
if PROVIDER == "openrouter":
    OPENROUTER_MODEL = get_openrouter_model()
    OPENROUTER_FALLBACK_MODELS = get_openrouter_fallback_models()
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    GEMINI_MODEL = get_gemini_model()
    GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
else:
    GEMINI_MODEL = get_gemini_model()
    GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def get_api_key():
    """Get API key based on provider - prioritize environment variable over settings file"""
    settings = load_app_settings()
    ai_settings = settings.get("ai", {})
    provider = ai_settings.get("provider", "gemini")

    # First try environment variables (more secure)
    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            api_key = ai_settings.get("openrouter_api_key", "")
        if not api_key:
            raise ValueError(
                "OpenRouter API key not configured. Please set OPENROUTER_API_KEY environment variable or configure in Admin Settings."
            )
    else:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            api_key = ai_settings.get("gemini_api_key", "")
        if not api_key:
            raise ValueError(
                "Gemini API key not configured. Please set GEMINI_API_KEY environment variable or configure in Admin Settings."
            )

    return api_key


def is_ai_enabled():
    """Check if AI features are enabled"""
    settings = load_app_settings()
    return settings.get("ai", {}).get("enabled", True)


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
        return {"error": str(e), "decision": "", "reason": "", "comments": ""}

    prompt = build_mechanical_prompt(test_values)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_mechanical_response(response)
    except Exception as e:
        return {"error": str(e), "decision": "", "reason": "", "comments": ""}


def build_mechanical_prompt(test_values):
    """Build prompt for mechanical test analysis — unit-aware with dual values.

    The form stores `tensile_strength` in KgF/mm² (raw measurement).
    MPa is derived: tensile_mpa = tensile_strength × 9.8.
    We pass BOTH to the AI so it can reason in either unit.

    Supports admin-configurable template override via the "mechanical_decision"
    prompt key. The template may use these named placeholders:
        {test_results}   — formatted bullet list of test values
    If the override is malformed the default template is used.
    """

    # Normalize values with explicit units
    tensile_kgf = test_values.get('tensile_strength')
    tensile_mpa = test_values.get('tensile_mpa')
    if tensile_kgf is not None and tensile_mpa is None:
        try:
            tensile_mpa = round(float(tensile_kgf) * 9.8, 2)
        except (TypeError, ValueError):
            tensile_mpa = None

    elongation = test_values.get('elongation')
    hardness = test_values.get('hardness')
    nodularity = test_values.get('nodularity_percent') or test_values.get('nodularity')
    ferrite = test_values.get('percent_70') or test_values.get('ferrite')
    nodule_count = test_values.get('nodule_count')
    carbides = test_values.get('carbides')

    def _fmt(v, suffix=''):
        if v is None or v == '':
            return 'not measured'
        return f'{v}{suffix}'

    lines = [
        f'- Tensile Strength: {_fmt(tensile_kgf, " KgF/mm²")} ({_fmt(tensile_mpa, " MPa")})',
        f'- Elongation: {_fmt(elongation, "%")}',
        f'- Hardness: {_fmt(hardness, " HB")}',
        f'- Nodularity: {_fmt(nodularity, "%")}',
        f'- Ferrite: {_fmt(ferrite, "%")}',
        f'- Nodule Count: {_fmt(nodule_count)}',
        f'- Carbides: {_fmt(carbides, "%")}',
    ]
    test_results = '\n'.join(lines)

    _DEFAULT_MECHANICAL_TEMPLATE = PROFESSIONAL_MECHANICAL_PROMPT

    template = get_prompt_template("mechanical_decision", _DEFAULT_MECHANICAL_TEMPLATE)
    fmt_kwargs = {"test_results": test_results}
    return _safe_format(template, fmt_kwargs, _DEFAULT_MECHANICAL_TEMPLATE)


def parse_mechanical_response(response):
    """Parse AI response for mechanical test - handles both Gemini and OpenRouter"""
    provider = get_ai_provider()
    text = extract_first_choice_text(response, provider)

    if not text:
        return {"decision": "", "reason": "", "comments": "", "has_defect": False}

    # Clean markdown
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        result = json.loads(text.strip())
        return {
            "decision": result.get("decision", ""),
            "reason": result.get("reason", ""),
            "comments": result.get("comments", ""),
            "has_defect": bool(result.get("has_defect", False)),
            "confidence": result.get("confidence", "medium"),
            "error": None,
        }
    except Exception as e:
        return {
            "decision": "",
            "reason": "",
            "comments": "",
            "has_defect": False,
            "confidence": "",
            "error": str(e),
        }


def generate_mechanical_stream(test_values):
    """Generate mechanical test analysis with streaming"""
    try:
        api_key = get_api_key()
    except ValueError as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return

    prompt = build_mechanical_prompt(test_values)
    provider = get_ai_provider()

    try:
        response = call_ai_api(api_key, prompt, max_retries=2, stream=True)
        full_content = ""

        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)

                        if provider == "openrouter":
                            choices = data.get("choices") or []
                            if choices and choices[0]:
                                delta = choices[0].get("delta") or {}
                                text_chunk = delta.get("content", "")
                                if text_chunk:
                                    full_content += text_chunk
                                    yield f"data: {json.dumps({'chunk': text_chunk, 'type': 'content'})}\n\n"
                        else:
                            candidates = data.get("candidates", [])
                            if candidates:
                                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                if text:
                                    full_content += text
                                    yield f"data: {json.dumps({'chunk': text, 'type': 'content'})}\n\n"
                    except json.JSONDecodeError:
                        pass

        if provider == "openrouter":
            result = parse_mechanical_response({"choices": [{"message": {"content": full_content}}]})
        else:
            result = parse_mechanical_response({"candidates": [{"content": {"parts": [{"text": full_content}]}}]})
        yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


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
        return {"error": str(e), "summary": "", "alerts": [], "recommendations": []}

    prompt = build_dashboard_prompt(stats)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_dashboard_response(response)
    except Exception as e:
        return {"error": str(e), "summary": "", "alerts": [], "recommendations": []}


def build_dashboard_prompt(stats):
    """Build prompt for dashboard summary"""

    prompt = f"""أنت مدير جودة في مصنع أنابيب حديد دكتايل. قم بتلخيص حالة الإنتاج اليومية.

إحصائيات اليوم:
- تحليلات كيميائية اليوم: {stats.get("chem_today", 0)}
- أنابيب اليوم: {stats.get("pipes_today", 0)}
- اختبارات ميكانيكية اليوم: {stats.get("mech_today", 0)}
- عيوب هذا الأسبوع: {stats.get("defects_week", 0)}
- نسبة القبول: {stats.get("acceptance_rate", 0)}%

أجب JSON فقط (بالعربية):
{{"summary":"ملخص قصير للحالة","alerts":["تنبيه 1","تنبيه 2"],"recommendations":["توصية 1","توصية 2"],"status":"good/warning/critical"}}"""

    return prompt


def parse_dashboard_response(response):
    """Parse AI response for dashboard summary - handles both Gemini and OpenRouter"""
    provider = get_ai_provider()
    text = extract_first_choice_text(response, provider)

    if not text:
        return {"summary": "", "alerts": [], "recommendations": [], "status": "good"}

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        result = json.loads(text.strip())
        return {
            "summary": result.get("summary", ""),
            "alerts": result.get("alerts", []),
            "recommendations": result.get("recommendations", []),
            "status": result.get("status", "good"),
        }
    except Exception:
        return {"summary": "", "alerts": [], "recommendations": [], "status": "good"}


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
        return {"error": str(e), "summary": "", "insights": [], "recommendations": []}

    prompt = build_report_prompt(report_type, data)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_report_response(response)
    except Exception as e:
        return {"error": str(e), "summary": "", "insights": [], "recommendations": []}


def build_report_prompt(report_type, data):
    """Build prompt for report summary"""

    if report_type == "chemical":
        prompt = f"""أنت خبير جودة. حلل تقرير التحليل الكيميائي.

البيانات:
- الفترة: {data.get("date_from")} إلى {data.get("date_to")}
- إجمالي التحليلات: {data.get("total", 0)}
- المقبولة: {data.get("accepted", 0)}
- المرفوضة: {data.get("rejected", 0)}
- العيوب: {data.get("defects", 0)}
- نسبة القبول: {data.get("rate", 0)}%

أجب JSON فقط (بالعربية):
{{"summary":"ملخص التقرير","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    elif report_type == "defect":
        defects_info = ", ".join(
            [f"{k}: {v}" for k, v in data.get("defects_by_stage", {}).items()]
        )
        prompt = f"""أنت خبير جودة. حلل تقرير العيوب.

البيانات:
- الفترة: {data.get("date_from")} إلى {data.get("date_to")}
- عيوب التحليل الكيميائي: {data.get("chem_defects_count", 0)}
- عيوب المراحل: {defects_info}

أجب JSON فقط (بالعربية):
{{"summary":"ملخص العيوب","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    elif report_type == "crosstab":
        # The rendered selection only — the point of analysing a pivot is that
        # the user has already narrowed the question.
        header = " | ".join(data.get("columns", [])) or "الإجمالي"
        table_lines = "\n".join(
            "  " + " | ".join(str(c) for c in row)
            for row in data.get("rows", [])[:40]
        ) or "  (لا توجد صفوف)"
        coverage = data.get("coverage") or {}
        coverage_line = (
            f"\nتغطية الوزن: {coverage.get('weighed', 0)} من "
            f"{coverage.get('pipes', 0)} ماسورة لها وزن معياري وفعلي."
            if coverage.get("shows_weight") else ""
        )

        prompt = f"""أنت محلل جودة في مصنع مواسير حديد زهر مطيل.
أمامك جدول محوري اختاره المستخدم بنفسه. حلل الجدول المعروض فقط.

الصفوف حسب: {data.get("row_dims")}
الأعمدة حسب: {data.get("pivot_dims") or "بدون تقسيم"}
المقاييس: {data.get("measures")}
عدد الصفوف المعروضة: {data.get("shown_rows", 0)} من {data.get("total_rows", 0)}{coverage_line}

{header}
{table_lines}

حدد الأنماط والقيم الشاذة والعلاقات بين الأبعاد المختارة، ثم توصيات عملية.
لا تفترض بيانات خارج الجدول، ولو كان عدد الصفوف المعروض أقل من الإجمالي اذكر
أن التحليل يخص المعروض فقط.

أجب JSON فقط (بالعربية):
{{"summary":"ملخص","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    elif report_type == "daily":
        # Aggregates only — never raw pipe rows. The model is being asked to
        # read a report that has already been computed, not to do the arithmetic.
        stage_lines = "\n".join(
            f"  - {r['stage']}: إجمالي {r['total']}، مقبول {r['acc']}، "
            f"مرفوض {r['rej']}، نسبة الرفض {r['rej_pct']}%"
            for r in data.get("stage_rows", [])
        ) or "  (لا توجد مراحل مسجلة)"
        reason_lines = "\n".join(
            f"  - {r['reason']}: {r['count']} ({r['pct']}%)"
            for r in data.get("top_reasons", [])
        ) or "  (لا توجد أسباب مسجلة)"
        defect_lines = "\n".join(
            f"  - {r['label']}: {r['count']}"
            for r in data.get("defects", [])
        ) or "  (لا توجد عيوب مسجلة)"
        saving = data.get("saving") or {}
        saving_line = (
            f"{saving.get('pct', 0):.2f}% محسوبة من {saving.get('n', 0)} "
            f"من {saving.get('of', 0)} ماسورة"
            if saving.get("n") else "غير متاحة (لا يوجد وزن معياري وفعلي)"
        )

        prompt = f"""أنت خبير جودة وإنتاج في مصنع مواسير حديد زهر مطيل.
حلل التقرير التالي وحدد الأسباب الجذرية وتوصيات مرتبة بالأولوية.

الفترة: {data.get("period")}
الإنتاج: {data.get("produced", 0)} ماسورة، تم البت في {data.get("decided", 0)}
المرفوض: {data.get("rejects", 0)} (نسبة الرفض {data.get("rej_pct", 0):.2f}%)
التوفير: {saving_line}

الأداء بالمرحلة:
{stage_lines}

أسباب الرفض:
{reason_lines}

العيوب:
{defect_lines}

اعتمد على الأرقام المعطاة فقط ولا تفترض بيانات غير موجودة. لو نسبة تغطية
التوفير منخفضة اذكر ذلك بدل الاعتماد عليها.

أجب JSON فقط (بالعربية):
{{"summary":"ملخص تنفيذي","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    else:  # production
        prompt = f"""أنت خبير إنتاج. حلل تقرير الإنتاج اليومي.

البيانات:
- التاريخ: {data.get("date")}
- إجمالي الأنابيب: {data.get("total", 0)}
- حسب القطر: {data.get("by_diameter", {})}

أجب JSON فقط (بالعربية):
{{"summary":"ملخص الإنتاج","insights":["ملاحظة 1","ملاحظة 2"],"recommendations":["توصية 1","توصية 2"]}}"""

    return prompt


def parse_report_response(response):
    """Parse AI response for report summary - handles both Gemini and OpenRouter"""
    provider = get_ai_provider()
    text = extract_first_choice_text(response, provider)

    if not text:
        return {"summary": "", "insights": [], "recommendations": [], "error": None}

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        result = json.loads(text.strip())
        return {
            "summary": result.get("summary", ""),
            "insights": result.get("insights", []),
            "recommendations": result.get("recommendations", []),
            "error": None,
        }
    except Exception as e:
        return {"summary": "", "insights": [], "recommendations": [], "error": str(e)}


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
        return {"error": str(e), "reason": "", "has_defect": False, "notes": ""}

    # Build the prompt
    prompt = build_analysis_prompt(element_values, auto_decision_result)

    try:
        response = call_gemini_api(api_key, prompt)
        return parse_ai_response(response)
    except Exception as e:
        return {"error": str(e), "reason": "", "has_defect": False, "notes": ""}


def build_analysis_prompt(element_values, auto_decision_result, template_key="chemical_decision"):
    """Build the prompt for Gemini chemical analysis.

    Args:
        element_values: dict of element names to measured values.
        auto_decision_result: result from calculate_auto_decision().
        template_key: which settings key to look up for an admin override.
            Use "chemical_decision" (default) for standard analysis,
            or "auto_decision_summary" when called from an auto-decision context.

    Supports admin-configurable template override. Available placeholders:
        {elements_text}  — "C=3.5, Si=2.0, ..."
        {decision}       — recommended decision ("ACCEPT" / "REJECT" / "Unknown")
        {worst_elements} — comma-separated list of failing elements, or "لا يوجد"
    """

    # Format element values compactly
    elements_text = ", ".join(
        [
            f"{name}={value}"
            for name, value in element_values.items()
            if value is not None and value != ""
        ]
    )

    # Get decision info
    decision = auto_decision_result.get("recommended_decision", "Unknown")
    worst_elements_list = auto_decision_result.get("worst_elements", [])
    worst_elements = ", ".join(worst_elements_list) if worst_elements_list else "لا يوجد"

    _DEFAULT_ANALYSIS_TEMPLATE = PROFESSIONAL_CHEMICAL_PROMPT

    template = get_prompt_template(template_key, _DEFAULT_ANALYSIS_TEMPLATE)
    fmt_kwargs = {
        "elements_text": elements_text,
        "decision": decision,
        "worst_elements": worst_elements,
    }
    return _safe_format(template, fmt_kwargs, _DEFAULT_ANALYSIS_TEMPLATE)


def call_ai_api(api_key, prompt, max_retries=2, stream=False):
    """Call AI API - routes to Gemini or OpenRouter based on settings.

    For non-streaming calls, returns parsed JSON dict (not raw Response).
    For streaming calls, returns the raw Response for iteration.
    """
    provider = get_ai_provider()

    if provider == "openrouter":
        resp = call_openrouter_api_impl(api_key, prompt, max_retries, stream)
    else:
        resp = call_gemini_api_impl(api_key, prompt, max_retries, stream)

    # For non-streaming: parse the Response object to a dict
    if not stream and hasattr(resp, 'json'):
        return resp.json()
    return resp


def call_gemini_api(api_key, prompt, max_retries=2, stream=False):
    """Call the AI API - wrapper for backward compatibility"""
    return call_ai_api(api_key, prompt, max_retries, stream)


def call_gemini_api_impl(api_key, prompt, max_retries=2, stream=False):
    """Call the Gemini API with retry logic - implementation"""
    headers = {"Content-Type": "application/json"}

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2500},
    }

    model = get_gemini_model()
    if stream:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?key={api_key}&alt=sse"
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if stream:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=90, stream=True
                )
            else:
                response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            last_error = "API request timed out"
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt < max_retries:
                import time

                time.sleep(1)

    raise Exception(f"Gemini API failed after {max_retries + 1} attempts: {last_error}")


def call_openrouter_api_impl(api_key, prompt, max_retries=2, stream=False):
    """Call the OpenRouter API with automatic fallback support"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://gcpipes.com",
        "X-Title": "GCP QC Pipes Traceability",
    }

    if is_auto_fallback_enabled():
        all_models = get_all_openrouter_models()
    else:
        all_models = [get_openrouter_model()]

    url = "https://openrouter.ai/api/v1/chat/completions"
    last_error = None

    for model_id in all_models:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2500,
            # No provider "only" restriction — all OpenRouter providers
            # (free and paid) are allowed; fallbacks stay enabled.
            "provider": {"allow_fallbacks": True},
        }
        # OpenRouter streams only when asked in the body (same URL either way);
        # without this the caller's iter_lines() sees a single JSON blob, no
        # "data:" SSE lines, so streamed AI analysis came back empty.
        if stream:
            payload["stream"] = True

        for attempt in range(max_retries + 1):
            try:
                if stream:
                    response = requests.post(
                        url, headers=headers, json=payload, timeout=90, stream=True
                    )
                else:
                    response = requests.post(url, headers=headers, json=payload, timeout=30)
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout:
                last_error = "API request timed out"
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                if attempt < max_retries:
                    import time
                    time.sleep(1)

        import logging
        logging.getLogger(__name__).warning(f"Model {model_id} failed, trying next fallback...")

    raise Exception(
        f"OpenRouter API failed for all models: {last_error}"
    )

def call_openrouter_api(api_key, prompt, max_retries=2, stream=False):
    """Wrapper for backward compatibility"""
    return call_openrouter_api_impl(api_key, prompt, max_retries, stream)


def parse_ai_response(response, provider=None):
    """Parse AI API response based on provider - returns full parsed result"""
    if provider is None:
        provider = get_ai_provider()

    if provider == "openrouter":
        return parse_openrouter_response_new(response)
    else:
        return parse_gemini_response_new(response)


def parse_gemini_response_new(response):
    """Parse the Gemini API response - returns full parsed dict"""
    try:
        # Handle both dict (from call_gemini_api) and response object
        if isinstance(response, dict):
            result = response
        else:
            result = response.json()

        candidates = result.get("candidates") or []
        if not candidates:
            return {
                "reason": "",
                "has_defect": False,
                "notes": "",
                "error": "No response from Gemini",
            }

        text = extract_first_choice_text(result, "gemini")
        if not text:
            return {
                "reason": "",
                "has_defect": False,
                "notes": "",
                "error": "No content in response",
            }

        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        result = json.loads(text.strip())

        return {
            "reason": result.get("reason", ""),
            "has_defect": bool(result.get("has_defect", False)),
            "notes": result.get("notes", ""),
            "error": None,
        }
    except Exception as e:
        return {"reason": "", "has_defect": False, "notes": "", "error": str(e)}


def parse_openrouter_response_new(response):
    """Parse OpenRouter API response - returns full parsed dict"""
    try:
        # Handle both dict (from call_gemini_api) and response object
        if isinstance(response, dict):
            result = response
        else:
            result = response.json()

        text = extract_first_choice_text(result, "openrouter")
        if not text:
            return {
                "reason": "",
                "has_defect": False,
                "notes": "",
                "error": "No response from OpenRouter",
            }

        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        result = json.loads(text.strip())

        return {
            "reason": result.get("reason", ""),
            "has_defect": bool(result.get("has_defect", False)),
            "notes": result.get("notes", ""),
            "error": None,
        }
    except Exception as e:
        return {"reason": "", "has_defect": False, "notes": "", "error": str(e)}


# Legacy wrapper - returns text only
def parse_openrouter_response(response):
    """Parse OpenRouter API response - legacy wrapper"""
    parsed = parse_openrouter_response_new(response)
    return parsed.get("reason", "") or parsed.get("notes", "") or ""


def call_gemini_api_stream(api_key, prompt):
    """Call the Gemini API with streaming enabled - deprecated, use call_ai_api with stream=True"""
    return call_ai_api(api_key, prompt, max_retries=2, stream=True)


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
    provider = get_ai_provider()

    try:
        response = call_ai_api(api_key, prompt, max_retries=2, stream=True)

        full_content = ""

        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)

                        # Parse based on provider
                        if provider == "openrouter":
                            choices = data.get("choices", [])
                            if choices:
                                text_chunk = (
                                    choices[0].get("delta", {}).get("content", "")
                                )
                                if text_chunk:
                                    full_content += text_chunk
                                    yield f"data: {json.dumps({'chunk': text_chunk, 'type': 'content'})}\n\n"
                        else:
                            # Gemini format
                            candidates = data.get("candidates", [])
                            if candidates:
                                content = candidates[0].get("content", {})
                                parts = content.get("parts", [])
                                if parts:
                                    text_chunk = parts[0].get("text", "")
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

    reason = ""
    notes = ""
    has_defect = False

    # Try to parse as JSON first
    try:
        # Clean up markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content.strip())
        return {
            "reason": data.get("reason", ""),
            "has_defect": bool(data.get("has_defect", False)),
            "notes": data.get("notes", ""),
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
        has_defect = defect_match.group(1).lower() == "true"

    return {"reason": reason, "has_defect": has_defect, "notes": notes}


def parse_gemini_response(response):
    """Parse the Gemini API response"""
    try:
        import re

        text = extract_first_choice_text(response, "gemini")
        if not text:
            return {
                "reason": "",
                "has_defect": False,
                "notes": "",
                "error": "No response from Gemini",
            }

        # Try to parse as JSON
        # Handle case where response might have markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        result = json.loads(text.strip())

        return {
            "reason": result.get("reason", ""),
            "has_defect": bool(result.get("has_defect", False)),
            "notes": result.get("notes", ""),
            "error": None,
        }
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        # If parsing fails, try to extract useful info from raw text
        import re

        text = extract_first_choice_text(response, "gemini")

        # Try to extract reason from the text
        reason = ""
        notes = ""
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
        defect_match = re.search(
            r'"has_defect"\s*:\s*(true|false)', text, re.IGNORECASE
        )
        if defect_match:
            has_defect = defect_match.group(1).lower() == "true"

        return {
            "reason": reason,
            "has_defect": has_defect,
            "notes": notes,
            "error": None if reason else f"Failed to parse response: {str(e)}",
        }


# =============================================================================
# CHATBOT AI ASSISTANT
# =============================================================================

CHATBOT_SYSTEM_PROMPT = """أنت مساعد ذكي متخصص في نظام تتبع جودة أنابيب الحديد الدكتايل GCP. لديك وصول مباشر لقاعدة البيانات ويمكنك الإجابة عن بيانات حقيقية.

You are an AI assistant for the GCP Ductile Iron Pipes QC Traceability system. You have direct access to the production database and can answer questions about real data.

معلومات النظام / System Info:
- Chemical analysis tracking: C, Si, Mn, P, S, Mg, Cu, Cr, Pb, Al, CE, MnE, MgE
- Mechanical tests: Tensile (KgF/mm² & MPa), Elongation, Hardness (HB), Nodularity, Ferrite, Nodule Count, Carbides
- Production stages: Melting Ladle → CCM → Annealing → Lab → Zinc → Cutting → Hydrotest → Cement → Coating → Finish → Delivery
- Ladle decision types: فحص أخيرة فقط (LAST_ONLY, best), فحص أولى وأخيرة (FIRST_LAST), فحص الشحنة 100% (FULL_100), تالف (REJECT)
- Pipe states: WAITING → ACCEPT/REJECT/HOLD/BLOCKED
- Mechanical acceptance: Tensile ≥42.5 KgF/mm², Elongation ≥10%, Nodularity ≥85%, Hardness <230 HB, Carbides <1%

القواعد / Rules:
1. Answer in the SAME LANGUAGE as the question (Arabic → Arabic, English → English)
2. When database context is provided below, use the REAL DATA to answer accurately
3. Format numbers consistently: 3 decimal places for percentages
4. Use tables and bullet points for clarity
5. If asked about data you don't have context for, say "I don't have that specific data available right now" instead of guessing
6. You can explain metallurgical concepts, help interpret results, and suggest next steps"""


def generate_chatbot_response(message, history=None, username=None, db_context=None):
    """Generate chatbot response using configured AI provider.

    Args:
        db_context: Real-time database context from chatbot_context_service.
    """
    try:
        api_key = get_api_key()
    except ValueError as e:
        return {"error": str(e), "response": ""}

    provider = get_ai_provider()
    system_context = CHATBOT_SYSTEM_PROMPT
    if db_context:
        system_context += f"\n\n[Database Context — Real-time Data]\n{db_context}\n\nUse this data to answer accurately."
    if username:
        system_context += f"\n\n\u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u062d\u0627\u0644\u064a: {username}"

    if provider == "openrouter":
        messages = [
            {"role": "system", "content": system_context},
            {"role": "assistant", "content": "\u0645\u0631\u062d\u0628\u0627\u064b! \u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f\u0643 \u0627\u0644\u0630\u0643\u064a \u0644\u0646\u0638\u0627\u0645 \u062a\u062d\u0644\u064a\u0644 \u0627\u0644\u0645\u0639\u0645\u0644. \u0643\u064a\u0641 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0633\u0627\u0639\u062f\u062a\u0643 \u0627\u0644\u064a\u0648\u0645\u061f"},
        ]
        if history:
            for msg in history[-10:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": message})

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://gcpipes.com",
                "X-Title": "GCP QC Pipes Traceability",
            }
            all_models = get_all_openrouter_models()
            last_error = "All models rate-limited, try again later"
            for model_id in all_models:
                payload = {
                    "model": model_id,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "provider": {"allow_fallbacks": True},
                }
                try:
                    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
                except requests.exceptions.RequestException as req_err:
                    last_error = str(req_err)
                    continue
                if resp.status_code == 200:
                    result = resp.json() or {}
                    choices = result.get("choices") or []
                    if choices and choices[0]:
                        content = extract_openrouter_content(choices[0].get("message"))
                        if content:
                            return {"response": content, "error": None}
                    last_error = "No response from AI"
                    continue
                # Any non-200 (429 rate limit, 402 credit, 5xx, …) → try next model
                last_error = f"OpenRouter error: {resp.status_code} - {resp.text[:200]}"
            return {"response": "", "error": last_error}
        except Exception as e:
            return {"response": "", "error": str(e)}

    else:
        contents = []
        contents.append({"role": "user", "parts": [{"text": f"[System Context]\n{system_context}\n\n[User Message]\n\u0645\u0631\u062d\u0628\u0627\u064b"}]})
        contents.append({"role": "model", "parts": [{"text": "\u0645\u0631\u062d\u0628\u0627\u064b! \u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f\u0643 \u0627\u0644\u0630\u0643\u064a \u0644\u0646\u0638\u0627\u0645 \u062a\u062d\u0644\u064a\u0644 \u0627\u0644\u0645\u0639\u0645\u0644. \u0643\u064a\u0641 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0633\u0627\u0639\u062f\u062a\u0643 \u0627\u0644\u064a\u0648\u0645\u061f"}]})
        if history:
            for msg in history[-10:]:
                role = "user" if msg.get("role") == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        try:
            model = get_gemini_model()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            response = requests.post(url, headers={"Content-Type": "application/json"}, json={"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}, timeout=60)
            response.raise_for_status()
            result = response.json() or {}
            text = extract_first_choice_text(result, "gemini")
            if text:
                return {"response": text, "error": None}
            return {"response": "", "error": "No response from AI"}
        except Exception as e:
            return {"response": "", "error": str(e)}


def generate_chatbot_stream(message, history=None, username=None, db_context=None):
    """Generate chatbot response with streaming and database context."""
    try:
        api_key = get_api_key()
    except ValueError as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return

    provider = get_ai_provider()
    system_context = CHATBOT_SYSTEM_PROMPT
    if db_context:
        system_context += f"\n\n[Database Context — Real-time Data]\n{db_context}\n\nUse this data to answer accurately."
    if username:
        system_context += f"\n\n\u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u062d\u0627\u0644\u064a: {username}"

    if provider == "openrouter":
        messages = [
            {"role": "system", "content": system_context},
            {"role": "assistant", "content": "\u0645\u0631\u062d\u0628\u0627\u064b! \u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f\u0643 \u0627\u0644\u0630\u0643\u064a \u0644\u0646\u0638\u0627\u0645 \u062a\u062d\u0644\u064a\u0644 \u0627\u0644\u0645\u0639\u0645\u0644. \u0643\u064a\u0641 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0633\u0627\u0639\u062f\u062a\u0643 \u0627\u0644\u064a\u0648\u0645\u061f"},
        ]
        if history:
            for msg in history[-10:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": message})

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://gcpipes.com",
                "X-Title": "GCP QC Pipes Traceability",
            }
            model_id = get_openrouter_model()
            payload = {
                "model": model_id,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True,
                "provider": {"allow_fallbacks": True},
            }
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60, stream=True)
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices") or []
                            if choices and choices[0]:
                                delta = choices[0].get("delta") or {}
                                text_chunk = delta.get("content", "")
                                if text_chunk:
                                    yield f"data: {json.dumps({'chunk': text_chunk})}\n\n"
                        except json.JSONDecodeError:
                            pass
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    else:
        contents = []
        contents.append({"role": "user", "parts": [{"text": f"[System Context]\n{system_context}\n\n[User Message]\n\u0645\u0631\u062d\u0628\u0627\u064b"}]})
        contents.append({"role": "model", "parts": [{"text": "\u0645\u0631\u062d\u0628\u0627\u064b! \u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f\u0643 \u0627\u0644\u0630\u0643\u064a \u0644\u0646\u0638\u0627\u0645 \u062a\u062d\u0644\u064a\u0644 \u0627\u0644\u0645\u0639\u0645\u0644. \u0643\u064a\u0641 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0633\u0627\u0639\u062f\u062a\u0643 \u0627\u0644\u064a\u0648\u0645\u061f"}]})
        if history:
            for msg in history[-10:]:
                role = "user" if msg.get("role") == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        try:
            model = get_gemini_model()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?key={api_key}&alt=sse"
            response = requests.post(url, headers={"Content-Type": "application/json"}, json={"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}, timeout=60, stream=True)
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            candidates = data.get("candidates", [])
                            if candidates:
                                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                if text:
                                    yield f"data: {json.dumps({'chunk': text})}\n\n"
                        except json.JSONDecodeError:
                            pass
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

