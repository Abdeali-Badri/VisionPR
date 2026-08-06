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
        llm = Mock()
        loader.return_value = llm
        config = LLMConfig(LLMProvider.GROQ, "groq/team-model")
        create_llm(config)
        llm.assert_called_once_with(model="groq/team-model", temperature=0.1)

    def test_missing_key_errors_do_not_include_secret_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeConfigError, "GEMINI_API_KEY") as caught:
                create_llm(LLMConfig(LLMProvider.GEMINI, "gemini/model"))
        self.assertNotIn("secret", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
