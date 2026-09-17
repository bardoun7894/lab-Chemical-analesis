"""Route-level tests for the /chatbot/ agent endpoints."""

import json
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.permission import seed_default_permissions


class ChatbotRouteTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.admin = User(username="admin", full_name="Admin", role="super_admin")
        self.admin.set_password("pass")
        db.session.add(self.admin)

        self.viewer = User(username="viewer", full_name="Viewer", role="viewer")
        self.viewer.set_password("pass")
        db.session.add(self.viewer)

        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get("/chatbot/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.location)

    def test_chatbot_index_loads(self):
        self._login(self.admin.id)
        resp = self.client.get("/chatbot/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Assistant", resp.data)

    def test_stream_returns_event_stream(self):
        self._login(self.admin.id)

        fake_events = [
            {"session_id": 1},
            {"chunk": "Hello!"},
            {"done": True, "steps": 0, "tools_used": []},
        ]

        with patch("app.routes.chatbot.is_ai_enabled", return_value=True):
            with patch("app.services.agent_service.run_agent") as mock_agent:
                mock_agent.return_value = iter(fake_events)
                resp = self.client.post(
                    "/chatbot/stream",
                    json={"message": "Hi"},
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.content_type)

        data = resp.data.decode()
        self.assertIn("session_id", data)
        self.assertIn("Hello!", data)

    def test_stream_saves_tool_calls(self):
        self._login(self.admin.id)

        fake_events = [
            {"session_id": 99},
            {"tool_call": {"id": "tc1", "step": 1, "name": "get_context", "args": {}}},
            {"tool_result": {"id": "tc1", "ok": True, "name": "get_context", "summary": "ok", "ms": 10}},
            {"chunk": "Answer"},
            {"done": True, "steps": 1, "tools_used": ["get_context"]},
        ]

        with patch("app.routes.chatbot.is_ai_enabled", return_value=True):
            with patch("app.services.agent_service.run_agent") as mock_agent:
                mock_agent.return_value = iter(fake_events)
                resp = self.client.post(
                    "/chatbot/stream",
                    json={"message": "Test"},
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, 200)

        # The assistant message should have tool_calls stored
        msg = ChatMessage.query.filter_by(role="assistant").first()
        if msg:
            self.assertIsNotNone(msg.tool_calls)
            self.assertTrue(len(msg.tool_calls) >= 1)

    def test_session_id_null_string_is_not_a_500(self):
        """The page used to emit window.CHAT_SESSION_ID = "null" (a string),
        which the client posted back and Postgres rejected as an integer,
        500-ing the first message of every new conversation."""
        from app.routes.chatbot import _coerce_session_id
        for bad in ("null", "", "undefined", None, "abc", True):
            self.assertIsNone(_coerce_session_id(bad), bad)
        self.assertEqual(_coerce_session_id("7"), 7)
        self.assertEqual(_coerce_session_id(7), 7)

    def test_page_emits_json_null_not_the_string(self):
        self._login(self.admin.id)
        html = self.client.get("/chatbot/").get_data(as_text=True)
        self.assertIn("window.CHAT_SESSION_ID = null", html)
        self.assertNotIn('window.CHAT_SESSION_ID = "null"', html)

    def test_new_session(self):
        self._login(self.admin.id)
        resp = self.client.post("/chatbot/new-session")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("session_id", data)

    def test_delete_session(self):
        self._login(self.admin.id)
        session = ChatSession(user_id=self.admin.id, title="Test")
        db.session.add(session)
        db.session.commit()

        resp = self.client.post(f"/chatbot/delete-session/{session.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

        updated = ChatSession.query.get(session.id)
        self.assertFalse(updated.is_active)

    def test_stored_messages_render_with_tool_calls(self):
        """GET /chatbot/?session_id=X renders stored messages with data-tool-calls."""
        self._login(self.admin.id)
        session = ChatSession(user_id=self.admin.id, title="Test")
        db.session.add(session)
        db.session.flush()

        user_msg = ChatMessage(session_id=session.id, role="user", content="Hello")
        db.session.add(user_msg)

        asst_msg = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="Answer",
            tool_calls=[{"tool_call": {"name": "get_context"}}],
        )
        db.session.add(asst_msg)
        db.session.commit()

        resp = self.client.get(f"/chatbot/?session_id={session.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"data-tool-calls", resp.data)
        self.assertIn(b"Answer", resp.data)


class DetachedInstanceRegressionTestCase(unittest.TestCase):
    """Regression test for the DetachedInstanceError crash.

    The agent runs inside a streaming generator that outlives the request.
    A live User row would be detached, so every attribute access raises
    DetachedInstanceError.  The fix is UserSnapshot — a plain dataclass
    copied out before streaming starts.  This test verifies the full
    path: snapshot_user, tool permission checks, and execute() all work
    on the snapshot without touching the ORM.
    """

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.admin = User(username="admin", full_name="Admin", role="super_admin")
        self.admin.set_password("pass")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_snapshot_does_not_touch_orm(self):
        """snapshot_user creates a detach-proof copy, not a live row."""
        from app.services.agent_tools import snapshot_user, UserSnapshot
        snap = snapshot_user(self.admin)
        self.assertIsInstance(snap, UserSnapshot)
        self.assertEqual(snap.username, "admin")
        self.assertTrue(snap.is_super_admin)

        # Expire/detach the original row to simulate generator context
        db.session.expire(self.admin)
        db.session.expunge(self.admin)

        # Snapshot fields remain accessible — no DetachedInstanceError
        self.assertEqual(snap.username, "admin")
        self.assertEqual(snap.role, "super_admin")
        self.assertTrue(snap.can("anything", "anything"))

    def test_execute_with_snapshot_user(self):
        """execute() works with a UserSnapshot, never touching the ORM user."""
        from app.services.agent_tools import snapshot_user, execute

        snap = snapshot_user(self.admin)
        db.session.expire(self.admin)
        db.session.expunge(self.admin)

        # get_context is permission-free and should work with snapshot
        result = execute("get_context", {}, snap)
        self.assertNotIn("error", result)
        self.assertIn("today", result)
        self.assertIn("db_counts", result)

    def test_tool_permission_with_snapshot(self):
        """Permission checks on a snapshot work without DB access."""
        from app.services.agent_tools import snapshot_user, execute

        # Create a non-admin user
        viewer = User(username="viewer2", full_name="V2", role="viewer")
        viewer.set_password("x")
        db.session.add(viewer)
        db.session.commit()

        snap = snapshot_user(viewer)
        db.session.expire(viewer)
        db.session.expunge(viewer)

        # Viewer without chatbot.sql permission can't run run_sql
        result = execute("run_sql", {"sql": "SELECT 1"}, snap)
        self.assertIn("error", result)
        self.assertIn("Permission denied", result["error"])

    def test_as_snapshot_idempotent(self):
        """as_snapshot returns the same snapshot if passed one."""
        from app.services.agent_tools import snapshot_user, as_snapshot
        snap = snapshot_user(self.admin)
        same = as_snapshot(snap)
        self.assertIs(snap, same)

    def test_run_agent_with_detached_user(self):
        """The agent loop works end-to-end with a snapshot user."""
        from app.services.agent_tools import snapshot_user
        from app.services.agent_service import run_agent

        snap = snapshot_user(self.admin)
        db.session.expire(self.admin)
        db.session.expunge(self.admin)

        # Mock the provider call so we don't hit the network
        with patch("app.services.agent_service.get_ai_provider", return_value="openrouter"), \
             patch("app.services.agent_service.get_api_key", return_value="test"), \
             patch("app.services.agent_service.get_openrouter_model", return_value="test"):

            # Build a mock streaming response. The frames are serialised
            # outside the f-strings: doubling braces only escapes them in an
            # f-string's literal part, so a `{{}}` written inside the
            # expression is a set holding an empty dict, not an empty dict.
            import json as _json
            text_frame = _json.dumps({
                "choices": [
                    {"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}
                ]
            })
            stop_frame = _json.dumps({
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ]
            })
            lines = [
                f"data: {text_frame}",
                f"data: {stop_frame}",
                "data: [DONE]",
            ]
            mock_resp = type("R", (), {
                "status_code": 200,
                "raise_for_status": lambda self: None,
                "iter_lines": lambda self: [l.encode() for l in lines],
            })()

            with patch("app.services.agent_service.requests.post", return_value=mock_resp):
                events = list(run_agent("Hello", [], snap, 1))

        chunks = [e["chunk"] for e in events if "chunk" in e]
        dones = [e for e in events if "done" in e]
        self.assertEqual("".join(chunks), "Hi")
        self.assertTrue(len(dones) >= 1)


if __name__ == "__main__":
    unittest.main()
