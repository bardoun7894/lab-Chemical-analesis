"""
Chat Session & Message models — persist chatbot conversations in the database
so the AI can recall past interactions and users can review history.
"""
from datetime import datetime
from app import db


class ChatSession(db.Model):
    """A chatbot conversation session."""
    __tablename__ = 'chat_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(200))  # Auto-generated from first message
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    user = db.relationship('User', backref='chat_sessions')
    messages = db.relationship('ChatMessage', backref='session', lazy='dynamic',
                               order_by='ChatMessage.created_at',
                               cascade='all, delete-orphan')

    def message_count(self):
        return self.messages.count()

    def last_message_preview(self):
        last = self.messages.order_by(ChatMessage.created_at.desc()).first()
        if last:
            return (last.content[:80] + '...') if len(last.content) > 80 else last.content
        return ''

    def get_history_for_ai(self, limit=20):
        """Return recent messages formatted for the AI conversation history."""
        msgs = self.messages.order_by(ChatMessage.created_at.desc()).limit(limit).all()
        msgs.reverse()
        return [{'role': m.role, 'content': m.content} for m in msgs]

    def __repr__(self):
        return f'<ChatSession {self.id} user={self.user_id}>'


class ChatMessage(db.Model):
    """A single message in a chat session."""
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('chat_sessions.id'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    tool_calls = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<ChatMessage {self.id} role={self.role}>'
