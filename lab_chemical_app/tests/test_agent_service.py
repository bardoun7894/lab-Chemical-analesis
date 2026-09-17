"""Tests for the agent loop (agent_service.py).

All HTTP calls are mocked — these tests verify the event protocol,
tool-call execution, max_steps enforcement, fallback, and both
OpenRouter and Gemini response shapes.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from app import create_app, db
from app.models.user import User
from app.models.permission import seed_default_permissions
from app.services.agent_tools import UserSnapshot


def _make_user():
    return UserSnapshot(
        id=1, username="admin", full_name="Admin", role="super_admin",
        is_super_admin=True, granted_keys=frozenset(),
    )


class SystemPromptTestCase(unittest.TestCase):
    """build_system_prompt produces a valid prompt string."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        u = User(username="admin", full_name="Admin", role="super_admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_prompt_contains_user_and_domain(self):
        from app.services.agent_service import build_system_prompt
        prompt = build_system_prompt(_make_user())
        self.assertIn("Admin", prompt)
        self.assertIn("super_admin", prompt)
        self.assertIn("Ladle", prompt)
        self.assertIn("Pipe", prompt)
        self.assertIn("Tool policy", prompt)

    def test_prompt_states_read_only_limits(self):
        """The agent must not over-promise: the default says what it cannot do."""
        from app.services.agent_service import build_system_prompt
        prompt = build_system_prompt(_make_user())
        self.assertIn("CANNOT create, edit, delete", prompt)

    def test_admin_override_replaces_static_body_but_keeps_dynamic(self):
        """An admin override (settings ai.prompts.chatbot_system) replaces the
        static body; user/time sections are still appended."""
        from unittest import mock
        from app.services import agent_service

        override = "You are a terse test bot. Answer only in haiku."
        with mock.patch.object(
            agent_service, "get_chatbot_prompt_body", return_value=override
        ):
            prompt = agent_service.build_system_prompt(_make_user())
        self.assertTrue(prompt.startswith(override))
        self.assertNotIn("Tool policy", prompt)
        self.assertIn("## Current user", prompt)
        self.assertIn("## Time", prompt)

    def test_override_read_from_app_settings(self):
        """get_chatbot_prompt_body honours the settings file the admin UI writes."""
        from unittest import mock
        from app.services import ai_service, agent_service

        with mock.patch.object(
            ai_service, "load_app_settings",
            return_value={"ai": {"prompts": {"chatbot_system": "CUSTOM BODY"}}},
        ):
            self.assertEqual(agent_service.get_chatbot_prompt_body(), "CUSTOM BODY")
        with mock.patch.object(
            ai_service, "load_app_settings",
            return_value={"ai": {"prompts": {"chatbot_system": ""}}},
        ):
            self.assertEqual(
                agent_service.get_chatbot_prompt_body(),
                agent_service.DEFAULT_CHATBOT_PROMPT,
            )

    def test_default_prompts_registry_includes_chatbot(self):
        from app.services.ai_service import get_default_prompts
        from app.services.agent_service import DEFAULT_CHATBOT_PROMPT
        self.assertEqual(get_default_prompts()["chatbot_system"], DEFAULT_CHATBOT_PROMPT)


class OpenRouterAgentTestCase(unittest.TestCase):
    """Test the OpenRouter agent loop with mocked HTTP."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        u = User(username="admin", full_name="Admin", role="super_admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user = _make_user()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mock_openrouter_response(self, content="Hello!", tool_calls=None):
        """Build a mock streaming response for OpenRouter."""
        lines = []
        delta = {"content": content}
        if tool_calls:
            delta["tool_calls"] = tool_calls
            delta.pop("content", None)

        choice = {"index": 0, "delta": delta, "finish_reason": None}
        lines.append(f"data: {json.dumps({'choices': [choice]})}")

        finish = {"index": 0, "delta": {}, "finish_reason": "stop" if not tool_calls else "tool_calls"}
        lines.append(f"data: {json.dumps({'choices': [finish]})}")
        lines.append("data: [DONE]")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.iter_lines = MagicMock(return_value=[l.encode() for l in lines])
        return resp

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_simple_text_response(self, mock_post, *_):
        from app.services.agent_service import run_agent

        mock_post.return_value = self._mock_openrouter_response("Hello there!")

        events = list(run_agent("Hi", [], self.user, 1))

        types = {k for e in events for k in e.keys()}
        self.assertIn("session_id", types)
        self.assertIn("chunk", types)
        self.assertIn("done", types)

        chunks = [e["chunk"] for e in events if "chunk" in e]
        self.assertEqual(''.join(chunks), "Hello there!")

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_tool_call_then_answer(self, mock_post, *_):
        from app.services.agent_service import run_agent

        # First call: model wants to call get_context
        tool_call_resp = self._mock_openrouter_response(
            tool_calls=[{
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_context", "arguments": "{}"},
            }]
        )

        # Second call: model responds with text
        text_resp = self._mock_openrouter_response("Today is 2026-08-30.")

        mock_post.side_effect = [tool_call_resp, text_resp]

        events = list(run_agent("What day is it?", [], self.user, 1))

        tool_calls = [e for e in events if "tool_call" in e]
        tool_results = [e for e in events if "tool_result" in e]
        chunks = [e for e in events if "chunk" in e]
        dones = [e for e in events if "done" in e]

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["tool_call"]["name"], "get_context")
        self.assertEqual(len(tool_results), 1)
        self.assertTrue(tool_results[0]["tool_result"]["ok"])
        self.assertTrue(len(chunks) > 0)
        self.assertEqual(len(dones), 1)

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_unknown_tool_yields_error_result(self, mock_post, *_):
        from app.services.agent_service import run_agent

        tool_call_resp = self._mock_openrouter_response(
            tool_calls=[{
                "index": 0,
                "id": "call_bad",
                "type": "function",
                "function": {"name": "nonexistent_tool", "arguments": "{}"},
            }]
        )
        text_resp = self._mock_openrouter_response("Sorry, that tool doesn't exist.")
        mock_post.side_effect = [tool_call_resp, text_resp]

        events = list(run_agent("test", [], self.user, 1))

        results = [e for e in events if "tool_result" in e]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["tool_result"]["ok"])
        self.assertIn("Unknown tool", results[0]["tool_result"]["summary"])

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_malformed_args_yields_error(self, mock_post, *_):
        from app.services.agent_service import run_agent

        tool_call_resp = self._mock_openrouter_response(
            tool_calls=[{
                "index": 0,
                "id": "call_bad",
                "type": "function",
                "function": {"name": "get_context", "arguments": "not json{{{"},
            }]
        )
        text_resp = self._mock_openrouter_response("Let me try again.")
        mock_post.side_effect = [tool_call_resp, text_resp]

        events = list(run_agent("test", [], self.user, 1))

        results = [e for e in events if "tool_result" in e]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["tool_result"]["ok"])

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_max_steps_enforced(self, mock_post, *_):
        from app.services.agent_service import run_agent

        # Every round returns a tool call — should stop at max_steps
        def make_tc_resp():
            return self._mock_openrouter_response(
                tool_calls=[{
                    "index": 0,
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "get_context", "arguments": "{}"},
                }]
            )

        final = self._mock_openrouter_response("Done.")
        mock_post.side_effect = [make_tc_resp() for _ in range(3)] + [final]

        events = list(run_agent("test", [], self.user, 1, max_steps=3))
        dones = [e for e in events if "done" in e]
        self.assertTrue(len(dones) >= 1)

    @patch('app.services.agent_service.get_ai_provider', return_value='openrouter')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_openrouter_model', return_value='test-model')
    @patch('app.services.agent_service.requests.post')
    def test_provider_400_falls_back(self, mock_post, *_):
        """A 400 with 'tool' in the message triggers fallback to legacy path."""
        from app.services.agent_service import run_agent
        import requests as req

        error_resp = MagicMock()
        error_resp.status_code = 400
        error_resp.text = "tool_use not supported"
        error_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
            "400 Client Error: tool_use not supported",
            response=error_resp,
        )
        mock_post.return_value = error_resp

        with patch('app.services.agent_service._fallback_stream') as mock_fb:
            mock_fb.return_value = iter([{"chunk": "fallback answer"}, {"done": True}])
            events = list(run_agent("test", [], self.user, 1))

        notices = [e for e in events if "notice" in e]
        self.assertTrue(len(notices) >= 1)
        self.assertIn("basic mode", notices[0]["notice"])


class GeminiAgentTestCase(unittest.TestCase):
    """Test the Gemini agent loop with mocked HTTP."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        u = User(username="admin", full_name="Admin", role="super_admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user = _make_user()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @patch('app.services.agent_service.get_ai_provider', return_value='gemini')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_gemini_model', return_value='gemini-2.5-flash-lite')
    @patch('app.services.agent_service.requests.post')
    def test_gemini_text_response(self, mock_post, *_):
        from app.services.agent_service import run_agent

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Today is Saturday."}],
                    "role": "model",
                },
            }],
        }
        mock_post.return_value = resp

        events = list(run_agent("What day is it?", [], self.user, 1))

        chunks = [e["chunk"] for e in events if "chunk" in e]
        self.assertEqual(''.join(chunks), "Today is Saturday.")

    @patch('app.services.agent_service.get_ai_provider', return_value='gemini')
    @patch('app.services.agent_service.get_api_key', return_value='test-key')
    @patch('app.services.agent_service.get_gemini_model', return_value='gemini-2.5-flash-lite')
    @patch('app.services.agent_service.requests.post')
    def test_gemini_function_call(self, mock_post, *_):
        from app.services.agent_service import run_agent

        # First: function call
        fc_resp = MagicMock()
        fc_resp.status_code = 200
        fc_resp.raise_for_status = MagicMock()
        fc_resp.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"functionCall": {"name": "get_context", "args": {}}}],
                    "role": "model",
                },
            }],
        }

        # Second: text answer
        text_resp = MagicMock()
        text_resp.status_code = 200
        text_resp.raise_for_status = MagicMock()
        text_resp.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "There are 187 pipes."}],
                    "role": "model",
                },
            }],
        }

        mock_post.side_effect = [fc_resp, text_resp]

        events = list(run_agent("How many pipes?", [], self.user, 1))
        tool_calls = [e for e in events if "tool_call" in e]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["tool_call"]["name"], "get_context")


if __name__ == "__main__":
    unittest.main()
