"""
Chatbot Routes — AI Assistant with tool-calling agent, session persistence,
and conversation history.
"""
import json
from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
from app import db, csrf
from app.models.chat import ChatSession, ChatMessage
from app.services.ai_service import is_ai_enabled
from app.services.permission_service import requires_permission

chatbot_bp = Blueprint('chatbot', __name__)


def _build_suggestions():
    """Data-aware suggested questions for the empty state."""
    from datetime import date, timedelta
    today = date.today()
    suggestions = [
        {'text_ar': f'كم أنبوب تم إنتاجه اليوم ({today.isoformat()})؟',
         'text_en': f'How many pipes were produced today ({today.isoformat()})?'},
        {'text_ar': 'أظهر الأنابيب المرفوضة هذا الأسبوع',
         'text_en': 'Show rejected pipes this week'},
        {'text_ar': 'ما حالة آخر مغرفة؟',
         'text_en': "What's the status of the latest ladle?"},
        {'text_ar': 'ملخص الإنتاج هذا الشهر',
         'text_en': 'Production summary this month'},
        {'text_ar': 'ما هي أكثر العيوب شيوعاً؟',
         'text_en': 'What are the most common defects?'},
        {'text_ar': 'أين يمكنني رؤية تقارير SPC؟',
         'text_en': 'Where can I find SPC reports?'},
    ]
    return suggestions


def _coerce_session_id(value):
    """Client-supplied session id -> int or None. Never trust the shape:
    'null', '', 'undefined' and non-numeric strings all mean "no session"."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_or_create_session(session_id=None):
    """Get existing session or create a new one for the current user."""
    session_id = _coerce_session_id(session_id)
    if session_id:
        session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first()
        if session:
            return session

    # Create new session
    session = ChatSession(user_id=current_user.id, title='New conversation')
    db.session.add(session)
    db.session.commit()
    return session


def _save_message(session, role, content):
    """Save a message to the session and auto-title if first user message."""
    msg = ChatMessage(session_id=session.id, role=role, content=content)
    db.session.add(msg)

    # Auto-title from first user message
    if role == 'user' and (not session.title or session.title == 'New conversation'):
        session.title = (content[:60] + '...') if len(content) > 60 else content

    db.session.commit()
    return msg


def _get_session_memory(session, limit=20):
    """Get conversation history from the DB session for the AI."""
    return session.get_history_for_ai(limit=limit)


@chatbot_bp.route('/')
@login_required
@requires_permission('chatbot', 'view')
def index():
    """Chatbot page with session history sidebar."""
    sessions = (
        ChatSession.query
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
        .all()
    )

    # Load active session (latest or from query param)
    active_session_id = request.args.get('session_id', type=int)
    active_session = None
    messages = []

    if active_session_id:
        active_session = ChatSession.query.filter_by(
            id=active_session_id, user_id=current_user.id
        ).first()

    if active_session:
        messages = active_session.messages.order_by(ChatMessage.created_at.asc()).all()

    suggestions = _build_suggestions()

    return render_template(
        'chatbot/index.html',
        sessions=sessions,
        active_session=active_session,
        messages=messages,
        suggestions=suggestions,
    )


@chatbot_bp.route('/new-session', methods=['POST'])
@csrf.exempt
@login_required
@requires_permission('chatbot', 'send')
def new_session():
    """Create a new chat session."""
    session = ChatSession(user_id=current_user.id, title='New conversation')
    db.session.add(session)
    db.session.commit()
    return jsonify({'session_id': session.id})


@chatbot_bp.route('/delete-session/<int:session_id>', methods=['POST'])
@csrf.exempt
@login_required
@requires_permission('chatbot', 'send')
def delete_session(session_id):
    """Soft-delete a session."""
    session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first()
    if session:
        session.is_active = False
        db.session.commit()
    return jsonify({'success': True})


@chatbot_bp.route('/send', methods=['POST'])
@csrf.exempt
@login_required
@requires_permission('chatbot', 'send')
def send_message():
    """Send message — saves to DB, returns AI response."""
    if not is_ai_enabled():
        return jsonify({'error': 'AI features are disabled', 'response': ''})

    data = request.get_json()
    message = data.get('message', '').strip()
    session_id = data.get('session_id')

    if not message:
        return jsonify({'error': 'No message provided', 'response': ''})

    try:
        from app.services.chatbot_context_service import gather_context
        from app.services.ai_service import generate_chatbot_response

        session = _get_or_create_session(session_id)
        _save_message(session, 'user', message)

        db_context = gather_context(message)
        history = _get_session_memory(session)

        result = generate_chatbot_response(
            message, history, current_user.username, db_context=db_context
        )

        if result.get('response'):
            _save_message(session, 'assistant', result['response'])

        result['session_id'] = session.id
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'response': ''})


@chatbot_bp.route('/stream', methods=['POST'])
@csrf.exempt
@login_required
@requires_permission('chatbot', 'send')
def stream_message():
    """Stream agent response — saves full response + tool_calls to DB when done."""
    if not is_ai_enabled():
        return jsonify({'error': 'AI features are disabled'})

    data = request.get_json()
    message = data.get('message', '').strip()
    session_id = data.get('session_id')

    if not message:
        return jsonify({'error': 'No message provided'})

    session = _get_or_create_session(session_id)

    # F11: read history BEFORE saving the user message so the model
    # does not see it twice (once from history, once from run_agent's append).
    history = _get_session_memory(session)
    _save_message(session, 'user', message)

    from app.services.agent_tools import snapshot_user
    user = snapshot_user(current_user)
    sid = session.id

    full_response_parts = []
    tool_calls_log = []

    def generate():
        from app.services.agent_service import run_agent

        # F10: save in finally so GeneratorExit (tab close) still persists
        try:
            for event in run_agent(message, history, user, sid):
                yield f"data: {json.dumps(event, default=str)}\n\n"

                if event.get('chunk'):
                    full_response_parts.append(event['chunk'])
                if event.get('tool_call'):
                    tool_calls_log.append(event['tool_call'])
                if event.get('tool_result'):
                    tool_calls_log.append(event['tool_result'])
        except GeneratorExit:
            pass
        finally:
            full_text = ''.join(full_response_parts)
            if full_text:
                try:
                    msg = ChatMessage(
                        session_id=sid,
                        role='assistant',
                        content=full_text,
                    )
                    if tool_calls_log:
                        msg.tool_calls = tool_calls_log
                    db.session.add(msg)
                    db.session.commit()
                except Exception:
                    db.session.rollback()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@chatbot_bp.route('/suggestions')
@login_required
@requires_permission('chatbot', 'view')
def get_suggestions():
    """Data-aware suggested questions."""
    suggestions = [
        {'text_ar': 'كم أنبوب تم إنتاجه اليوم؟', 'text_en': 'How many pipes were produced today?'},
        {'text_ar': 'أظهر الأنابيب المرفوضة هذا الأسبوع', 'text_en': 'Show rejected pipes this week'},
        {'text_ar': 'ما حالة آخر مغرفة؟', 'text_en': "What's the status of the latest ladle?"},
        {'text_ar': 'أي أنابيب تنتظر الاختبار الميكانيكي؟', 'text_en': 'Which pipes are waiting for mechanical test?'},
        {'text_ar': 'ملخص الإنتاج هذا الشهر', 'text_en': 'Production summary this month'},
        {'text_ar': 'ما هي معايير قبول التحليل الكيميائي؟', 'text_en': 'What are the chemical analysis acceptance criteria?'},
    ]
    return jsonify({'suggestions': suggestions})
