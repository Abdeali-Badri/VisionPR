import os
import unittest

from src.runtime_config import ExecutionMode, detect_runtime


@unittest.skipUnless(os.getenv("RUN_LLM_TESTS") == "1", "Real LLM tests are disabled.")
class OptionalLLMIntegrationTests(unittest.TestCase):
    def test_runtime_selects_crewai_for_real_llm_test(self):
        runtime = detect_runtime()
        self.assertEqual(ExecutionMode.CREWAI, runtime.mode)
        self.assertTrue(runtime.llm_used)


if __name__ == "__main__":
    unittest.main()
