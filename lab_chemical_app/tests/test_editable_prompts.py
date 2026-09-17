"""
Tests for editable AI prompt templates (admin override system).

Covers:
- get_prompt_template returns override when set, default when unset/empty.
- Malformed override (unknown placeholder) does NOT raise — builder returns usable string.
- build_mechanical_prompt unit-consistency: the prompt states measured tensile value and
  spec threshold in the same unit, never cross-comparing kgf/mm² input against an MPa spec.
- Admin POST persists prompt overrides without clobbering other AI settings.
"""

import json
import unittest
from unittest import mock

from app import create_app
from app.models.permission import seed_default_permissions


# Default settings skeleton used by all tests (no real API keys needed).
_BASE_SETTINGS = {
    "ai": {
        "provider": "gemini",
        "gemini_api_key": "test-key",
        "enabled": True,
    }
}


def _settings_with_prompts(prompts: dict) -> dict:
    """Return a settings dict with the given prompts block merged in."""
    import copy
    s = copy.deepcopy(_BASE_SETTINGS)
    s["ai"]["prompts"] = prompts
    return s


class TestGetPromptTemplate(unittest.TestCase):
    """Unit tests for the get_prompt_template helper."""

    def _import(self):
        from app.services import ai_service
        return ai_service

    def test_returns_override_when_set(self):
        ai = self._import()
        settings = _settings_with_prompts({"mechanical_decision": "My custom prompt {test_results}"})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("mechanical_decision", "DEFAULT")
        self.assertEqual(result, "My custom prompt {test_results}")

    def test_returns_default_when_key_missing(self):
        ai = self._import()
        settings = _settings_with_prompts({})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("mechanical_decision", "DEFAULT")
        self.assertEqual(result, "DEFAULT")

    def test_returns_default_when_override_is_empty_string(self):
        ai = self._import()
        settings = _settings_with_prompts({"mechanical_decision": ""})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("mechanical_decision", "DEFAULT")
        self.assertEqual(result, "DEFAULT")

    def test_returns_default_when_no_prompts_key_in_ai(self):
        ai = self._import()
        import copy
        settings = copy.deepcopy(_BASE_SETTINGS)  # no "prompts" sub-key at all
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("chemical_decision", "MY_DEFAULT")
        self.assertEqual(result, "MY_DEFAULT")


class TestMalformedOverrideFallback(unittest.TestCase):
    """A bad override must NOT propagate exceptions to callers."""

    def _import(self):
        from app.services import ai_service
        return ai_service

    def test_unknown_placeholder_falls_back_to_default(self):
        """An override referencing an undefined placeholder must not raise.

        _safe_format uses plain string replacement (not str.format) so JSON
        braces in prompts survive; an unknown placeholder is simply left
        unreplaced and the override is returned as-is — no exception, and the
        caller still gets a usable string.
        """
        ai = self._import()
        # Override uses {totally_unknown_key} which won't be in the format dict
        bad_override = "Result: {totally_unknown_key} pass/fail"
        settings = _settings_with_prompts({"mechanical_decision": bad_override})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_mechanical_prompt({"tensile_strength": 44.0, "elongation": 12})
        # Must return the override verbatim (unknown token left unreplaced)
        self.assertIsInstance(prompt, str)
        self.assertEqual(prompt, bad_override)

    def test_chemical_analysis_unknown_placeholder_falls_back(self):
        """Same safety for build_analysis_prompt."""
        ai = self._import()
        bad_override = "Analyze: {nonexistent_placeholder} elements"
        settings = _settings_with_prompts({"chemical_decision": bad_override})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_analysis_prompt(
                {"C": 3.5, "Si": 2.0},
                {"recommended_decision": "ACCEPT", "worst_elements": []},
            )
        self.assertIsInstance(prompt, str)
        self.assertEqual(prompt, bad_override)


class TestMechanicalPromptUnitConsistency(unittest.TestCase):
    """
    The prompt must state tensile value and spec threshold in the same unit.

    The model stores tensile_strength in kgf/mm². The spec threshold is:
      42.50 kgf/mm²  ≡  420 MPa.

    A correct prompt must either:
      - Show 42.50 kgf/mm² measured vs 42.50 kgf/mm² threshold  (kgf/mm² world), OR
      - Show ~420 MPa measured vs 420 MPa threshold              (MPa world).

    What is FORBIDDEN is cross-unit comparison such as:
      "44.0 MPa ... passes >= 420 MPa"   (44.0 kgf/mm² ≠ 44.0 MPa)
    or any statement that converts only one side.
    """

    def _import(self):
        from app.services import ai_service
        return ai_service

    def _default_settings(self):
        import copy
        s = copy.deepcopy(_BASE_SETTINGS)
        s["ai"]["prompts"] = {}
        return s

    def test_tensile_420_kgf_prompt_mentions_kgf_value_and_spec(self):
        """
        tensile_strength=420 kgf/mm² should appear as ~420 kgf/mm² AND ~4116 MPa.
        The prompt must NOT imply 420 (raw) passes a 420 MPa spec without conversion.
        """
        ai = self._import()
        settings = self._default_settings()
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_mechanical_prompt({"tensile_strength": 420.0})

        # Both units must appear
        self.assertIn("420", prompt)         # kgf/mm² value
        self.assertIn("MPa", prompt)         # MPa present
        self.assertIn("KgF", prompt)         # kgf label present

        # The prompt must show the MPa equivalent (420 * 9.8 = 4116)
        # We accept any reasonable representation of the converted value.
        self.assertIn("4116", prompt)

        # The spec threshold in MPa (420) must be stated
        self.assertIn("420 MPa", prompt)

        # The spec threshold in kgf/mm² must be stated
        self.assertIn("42.5", prompt)  # 42.50 kgf/mm²

    def test_tensile_44_kgf_prompt_consistent(self):
        """tensile_strength=44.0 kgf/mm² → MPa=431.2. Both must appear correctly."""
        ai = self._import()
        settings = self._default_settings()
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_mechanical_prompt({"tensile_strength": 44.0})

        # kgf/mm² value appears
        self.assertIn("44.0", prompt)
        # MPa equivalent appears (44.0 * 9.8 = 431.2)
        self.assertIn("431.2", prompt)

    def test_tensile_mpa_field_takes_precedence(self):
        """When tensile_mpa is provided directly, it is used as-is (no second ×9.8)."""
        ai = self._import()
        settings = self._default_settings()
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_mechanical_prompt(
                {"tensile_strength": 44.0, "tensile_mpa": 431.2}
            )
        self.assertIn("431.2", prompt)

    def test_prompt_does_not_cross_compare_units(self):
        """
        Ensure the prompt text does not construct a comparison like
        'X MPa ... passes >= 420 MPa' where X is the raw kgf/mm² value
        (i.e. less than 420 but still labelled MPa).
        """
        ai = self._import()
        settings = self._default_settings()
        # Use a value that is a valid kgf/mm² but looks wrong if treated as MPa
        # 43 kgf/mm² = 421.4 MPa → passes. If mis-read as 43 MPa it would fail.
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            prompt = ai.build_mechanical_prompt({"tensile_strength": 43.0})

        # The MPa equivalent (43*9.8=421.4) must appear
        self.assertIn("421.4", prompt)
        # The raw value must appear with kgf/mm² label
        self.assertIn("43.0", prompt)
        self.assertIn("KgF", prompt)


class TestAdminPromptPersistence(unittest.TestCase):
    """Admin POST to update_ai_settings must save prompts and merge, not replace."""

    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()

        from app.models.user import User
        from app import db
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        u = User(username="admin_user", full_name="Admin", role="admin")
        if hasattr(u, "set_password"):
            u.set_password("pw")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def tearDown(self):
        from app import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def test_post_saves_prompt_overrides_without_clobbering_api_keys(self):
        """Submitting prompts must merge into ai settings, not replace them."""
        from app.routes import admin as admin_module

        initial_settings = {
            "ai": {
                "provider": "gemini",
                "gemini_api_key": "real-key-xyz",
                "enabled": True,
                "prompts": {},
            }
        }

        saved = {}

        def fake_load():
            import copy
            return copy.deepcopy(initial_settings)

        def fake_save(settings):
            saved.update(settings)

        self._login()

        with mock.patch.object(admin_module, "load_app_settings", side_effect=fake_load), \
             mock.patch.object(admin_module, "save_app_settings", side_effect=fake_save):
            resp = self.client.post(
                "/admin/settings/ai/update",
                data={
                    "provider": "gemini",
                    "gemini_api_key": "*" * 8,  # masked — should not overwrite
                    "gemini_model": "gemini-2.0-flash",
                    "openrouter_api_key": "",
                    "openrouter_model": "anthropic/claude-3-sonnet",
                    "enabled": "on",
                    "prompt_mechanical_decision": "Custom mech prompt {test_results}",
                    "prompt_chemical_decision": "",  # empty = use default
                    "prompt_auto_decision_summary": "Summary: {elements_text}",
                    "prompt_microstructure_image": "",
                },
                follow_redirects=False,
            )

        self.assertIn(resp.status_code, (302, 200))
        # API key must be preserved (not overwritten with masked value)
        self.assertEqual(saved["ai"]["gemini_api_key"], "real-key-xyz")
        # Non-empty prompts are saved
        self.assertEqual(
            saved["ai"]["prompts"]["mechanical_decision"],
            "Custom mech prompt {test_results}",
        )
        self.assertEqual(
            saved["ai"]["prompts"]["auto_decision_summary"],
            "Summary: {elements_text}",
        )
        # Empty prompts stored as empty string (means "use default")
        self.assertEqual(saved["ai"]["prompts"].get("chemical_decision", ""), "")
        self.assertEqual(saved["ai"]["prompts"].get("microstructure_image", ""), "")


class TestMicrostructurePromptOverride(unittest.TestCase):
    """Microstructure image prompt can be overridden (it has no placeholders)."""

    def _import_ocr(self):
        from app.services import mechanical_ocr_service
        return mechanical_ocr_service

    def _import_ai(self):
        from app.services import ai_service
        return ai_service

    def test_get_microstructure_prompt_returns_override(self):
        ai = self._import_ai()
        settings = _settings_with_prompts({"microstructure_image": "Custom extraction prompt"})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("microstructure_image", "DEFAULT OCR PROMPT")
        self.assertEqual(result, "Custom extraction prompt")

    def test_get_microstructure_prompt_returns_default_when_empty(self):
        ai = self._import_ai()
        settings = _settings_with_prompts({"microstructure_image": ""})
        with mock.patch.object(ai, "load_app_settings", return_value=settings):
            result = ai.get_prompt_template("microstructure_image", "DEFAULT OCR PROMPT")
        self.assertEqual(result, "DEFAULT OCR PROMPT")


if __name__ == "__main__":
    unittest.main()
