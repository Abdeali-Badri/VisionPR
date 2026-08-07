import os
import unittest
from unittest.mock import patch

from src.llm.config import GEMINI_DEFAULT_MODEL, GROQ_DEFAULT_MODEL, LLMConfig, load_llm_config, normalize_model
from src.llm.providers import LLMProvider, parse_llm_provider
from src.runtime_config import RuntimeConfigError


class LLMConfigTests(unittest.TestCase):
    def env(self, **values):
        base = {
            "VISIONPR_LLM_PROVIDER": None,
            "VISIONPR_LLM_MODEL": None,
            "GEMINI_API_KEY": None,
            "GROQ_API_KEY": None,
        }
        base.update(values)
        clean = {key: value for key, value in base.items() if value is not None}
        return patch.dict(os.environ, clean, clear=True)

    def test_provider_parsing(self):
        self.assertEqual(LLMProvider.GEMINI, parse_llm_provider("gemini"))
        self.assertEqual(LLMProvider.GROQ, parse_llm_provider("groq"))

    def test_unknown_provider_rejected(self):
        with self.assertRaisesRegex(RuntimeConfigError, "Supported providers: gemini, groq"):
            parse_llm_provider("openai")

    def test_explicit_gemini_configuration(self):
        with self.env(VISIONPR_LLM_PROVIDER="gemini", GEMINI_API_KEY="secret"):
            config = load_llm_config()
        self.assertEqual(LLMProvider.GEMINI, config.provider)
        self.assertEqual(GEMINI_DEFAULT_MODEL, config.model)

    def test_explicit_groq_configuration(self):
        with self.env(VISIONPR_LLM_PROVIDER="groq", VISIONPR_LLM_MODEL="llama-team-model", GROQ_API_KEY="secret"):
            config = load_llm_config()
        self.assertEqual(LLMProvider.GROQ, config.provider)
        self.assertEqual("groq/llama-team-model", config.model)

    def test_provider_inference(self):
        with self.env(GEMINI_API_KEY="secret"):
            self.assertEqual(LLMProvider.GEMINI, load_llm_config().provider)
        with self.env(GROQ_API_KEY="secret", VISIONPR_LLM_MODEL="groq/model"):
            self.assertEqual(LLMProvider.GROQ, load_llm_config().provider)

    def test_both_credentials_prefer_gemini(self):
        with self.env(GEMINI_API_KEY="gemini-secret", GROQ_API_KEY="groq-secret"):
            self.assertEqual(LLMProvider.GEMINI, load_llm_config().provider)

    def test_custom_model_preserved_and_normalized(self):
        with self.env(GEMINI_API_KEY="secret", VISIONPR_LLM_MODEL="gemini/custom-model"):
            self.assertEqual("gemini/custom-model", load_llm_config().model)
        self.assertEqual("gemini/custom", normalize_model(LLMProvider.GEMINI, "custom"))

    def test_groq_uses_tested_default_model(self):
        with self.env(GROQ_API_KEY="secret"):
            self.assertEqual(GROQ_DEFAULT_MODEL, load_llm_config().model)

    def test_missing_keys_and_wrong_keys_are_rejected(self):
        with self.env():
            with self.assertRaisesRegex(RuntimeConfigError, "No supported LLM API key"):
                load_llm_config()
        with self.env(VISIONPR_LLM_PROVIDER="gemini"):
            with self.assertRaisesRegex(RuntimeConfigError, "GEMINI_API_KEY"):
                load_llm_config()
        with self.env(VISIONPR_LLM_PROVIDER="groq", VISIONPR_LLM_MODEL="model"):
            with self.assertRaisesRegex(RuntimeConfigError, "GROQ_API_KEY"):
                load_llm_config()

    def test_no_api_key_in_serialized_config(self):
        with self.env(GEMINI_API_KEY="very-secret"):
            config = load_llm_config()
        self.assertNotIn("very-secret", str(config))


if __name__ == "__main__":
    unittest.main()
