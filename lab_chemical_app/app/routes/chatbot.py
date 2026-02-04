"""
Chatbot Routes - AI Assistant for Lab Chemical Analysis
"""
import json
from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
from app import csrf
from app.services.ai_service import generate_chatbot_response, generate_chatbot_stream, is_ai_enabled

chatbot_bp = Blueprint('chatbot', __name__)


@chatbot_bp.route('/')
@login_required
def index():
    """Chatbot page"""
    return render_template('chatbot/index.html')


@chatbot_bp.route('/send', methods=['POST'])
@csrf.exempt
@login_required
def send_message():
    """Send message to chatbot and get response"""
    if not is_ai_enabled():
        return jsonify({'error': 'AI features are disabled', 'response': ''})

    data = request.get_json()
    message = data.get('message', '')
    history = data.get('history', [])

    if not message:
        return jsonify({'error': 'No message provided', 'response': ''})

    try:
        result = generate_chatbot_response(message, history, current_user.username)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'response': ''})


@chatbot_bp.route('/stream', methods=['POST'])
@csrf.exempt
@login_required
def stream_message():
    """Stream chatbot response"""
    if not is_ai_enabled():
        return jsonify({'error': 'AI features are disabled'})

    data = request.get_json()
    message = data.get('message', '')
    history = data.get('history', [])

    if not message:
        return jsonify({'error': 'No message provided'})

    def generate():
        for chunk in generate_chatbot_stream(message, history, current_user.username):
            yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@chatbot_bp.route('/suggestions')
@login_required
def get_suggestions():
    """Get suggested questions"""
    suggestions = [
        {
            'text_ar': 'ما هي معايير قبول التحليل الكيميائي؟',
            'text_en': 'What are the chemical analysis acceptance criteria?'
        },
        {
            'text_ar': 'كيف أضيف تحليل كيميائي جديد؟',
            'text_en': 'How do I add a new chemical analysis?'
        },
        {
            'text_ar': 'ما هي نطاقات العناصر المقبولة؟',
            'text_en': 'What are the acceptable element ranges?'
        },
        {
            'text_ar': 'اشرح لي نظام القرارات التلقائي',
            'text_en': 'Explain the automatic decision system'
        },
        {
            'text_ar': 'كيف أفسر نتائج الاختبار الميكانيكي؟',
            'text_en': 'How do I interpret mechanical test results?'
        }
    ]
    return jsonify({'suggestions': suggestions})
