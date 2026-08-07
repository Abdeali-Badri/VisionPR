import os
import unittest
from unittest.mock import Mock, patch

from src.llm.config import LLMConfig
from src.llm.factory import create_llm
from src.llm.providers import LLMProvider
from src.runtime_config import RuntimeConfigError


class LLMFactoryTests(unittest.TestCase):
    @patch.dict(os.environ, {"GEMINI_API_KEY": "secret"}, clear=True)
    @patch("src.llm.factory._load_crewai_llm")
    def test_gemini_llm_construction(self, loader):
        llm = Mock()
        llm.return_value = Mock(name="llm")
        loader.return_value = llm
        config = LLMConfig(LLMProvider.GEMINI, "gemini/gemini-2.5-flash", temperature=0.2)
        result = create_llm(config)
        self.assertEqual(llm.return_value, result)
        llm.assert_called_once_with(model="gemini/gemini-2.5-flash", temperature=0.2)

    @patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=True)
    @patch("src.llm.factory._load_crewai_llm")
    def test_groq_llm_construction(self, loader):
        class FakeLLM:
            def __init__(self, **options):
                self.options = options

            def call(self, messages, *args, **kwargs):
                return messages

        loader.return_value = FakeLLM
        config = LLMConfig(LLMProvider.GROQ, "groq/team-model")
        result = create_llm(config)
        self.assertEqual(
            {
                "model": "groq/team-model",
                "temperature": 0.1,
                "drop_params": True,
                "additional_drop_params": ["messages[*].cache_breakpoint"],
            },
            result.options,
        )
        messages = [{"role": "system", "content": "x", "cache_breakpoint": True}]
        self.assertNotIn("cache_breakpoint", result.call(messages)[0])

    def test_missing_key_errors_do_not_include_secret_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeConfigError, "GEMINI_API_KEY") as caught:
                create_llm(LLMConfig(LLMProvider.GEMINI, "gemini/model"))
        self.assertNotIn("secret", str(caught.exception).lower())

    @patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=True)
    @patch("src.llm.factory.time.sleep")
    @patch("src.llm.factory._load_crewai_llm")
    def test_groq_rate_limit_retries_the_llm_call(self, loader, sleep):
        class RateLimitError(RuntimeError):
            pass

        class FakeLLM:
            def __init__(self, **options):
                self.calls = 0

            def call(self, messages, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RateLimitError("Rate limit reached. Please try again in 1.5s")
                return "ok"

        loader.return_value = FakeLLM
        result = create_llm(LLMConfig(LLMProvider.GROQ, "groq/team-model"))
        self.assertEqual("ok", result.call([{"role": "user", "content": "x"}]))
        sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
