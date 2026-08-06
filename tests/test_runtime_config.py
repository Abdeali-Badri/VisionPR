import os
import unittest
from unittest.mock import patch

from src.runtime_config import ExecutionMode, RuntimeConfigError, detect_runtime


class RuntimeConfigTests(unittest.TestCase):
    def env(self, **values):
        base = {
            "PYTHON_DOTENV_DISABLED": "1",
            "VISIONPR_MODE": None,
            "VISIONPR_LLM_PROVIDER": None,
            "VISIONPR_LLM_MODEL": None,
            "GEMINI_API_KEY": None,
            "GROQ_API_KEY": None,
        }
        base.update(values)
        clean = {key: value for key, value in base.items() if value is not None}
        return patch.dict(os.environ, clean, clear=True)

    def test_auto_uses_offline_without_keys(self):
        with self.env():
            runtime = detect_runtime()
        self.assertEqual(ExecutionMode.OFFLINE_DEMO, runtime.mode)
        self.assertFalse(runtime.llm_used)
        self.assertTrue(runtime.reason)

    def test_auto_selects_gemini_key(self):
        with self.env(GEMINI_API_KEY="secret"):
            runtime = detect_runtime()
        self.assertEqual(ExecutionMode.CREWAI, runtime.mode)
        self.assertEqual("gemini", runtime.provider)
        self.assertEqual("gemini/gemini-2.5-flash", runtime.model)

    def test_auto_selects_groq_with_model(self):
        with self.env(GROQ_API_KEY="secret", VISIONPR_LLM_MODEL="groq/team-tested-model"):
            runtime = detect_runtime()
        self.assertEqual("groq", runtime.provider)
        self.assertEqual("groq/team-tested-model", runtime.model)

    def test_gemini_precedence_when_both_keys_exist(self):
        with self.env(GEMINI_API_KEY="gemini-secret", GROQ_API_KEY="groq-secret"):
            runtime = detect_runtime()
        self.assertEqual("gemini", runtime.provider)

    def test_offline_always_uses_offline(self):
        with self.env(VISIONPR_MODE="offline", GEMINI_API_KEY="secret"):
            runtime = detect_runtime()
        self.assertEqual(ExecutionMode.OFFLINE_DEMO, runtime.mode)
        self.assertIsNone(runtime.provider)

    def test_forced_crewai_rejects_missing_credentials(self):
        with self.env(VISIONPR_MODE="crewai"):
            with self.assertRaisesRegex(RuntimeConfigError, "No supported LLM API key"):
                detect_runtime()

    def test_unknown_mode_rejected(self):
        with self.env(VISIONPR_MODE="magic"):
            with self.assertRaises(RuntimeConfigError):
                detect_runtime()

    def test_secret_not_serialized(self):
        with self.env(GEMINI_API_KEY="very-secret-value"):
            runtime = detect_runtime()
        self.assertNotIn("very-secret-value", str(runtime.to_dict()))

    def test_custom_model_preserved(self):
        with self.env(GEMINI_API_KEY="secret", VISIONPR_LLM_MODEL="gemini/custom-model"):
            runtime = detect_runtime()
        self.assertEqual("gemini/custom-model", runtime.model)


if __name__ == "__main__":
    unittest.main()
