import unittest
from unittest.mock import patch

from src.agent_factory import create_agent_bundle
from src.llm.config import LLMConfig
from src.llm.providers import LLMProvider
from src.offline_agents import OfflineArchitectAgent, OfflineCoderAgent, OfflineReviewerAgent
from src.runtime_config import AgentEngine, ExecutionMode, RuntimeConfig


def runtime(engine):
    return RuntimeConfig(
        engine=engine,
        llm=LLMConfig(LLMProvider.GEMINI, "gemini/gemini-2.5-flash") if engine == AgentEngine.CREWAI else None,
        reason="test",
        requested_mode="test",
        crewai_installed=True,
    )


class AgentFactoryTests(unittest.TestCase):
    def test_offline_runtime_creates_offline_agents(self):
        bundle = create_agent_bundle(runtime(AgentEngine.HEURISTIC), repo_path=".")
        self.assertIsInstance(bundle.architect, OfflineArchitectAgent)
        self.assertIsInstance(bundle.coder, OfflineCoderAgent)
        self.assertIsInstance(bundle.reviewer, OfflineReviewerAgent)
        self.assertEqual(ExecutionMode.OFFLINE_DEMO, bundle.runtime.mode)

    @patch("src.agent_factory.create_llm")
    @patch("src.crewai_agents.CrewAIReviewerAgent")
    @patch("src.crewai_agents.CrewAICoderAgent")
    @patch("src.crewai_agents.CrewAIArchitectAgent")
    def test_crewai_runtime_creates_crewai_adapters(self, architect, coder, reviewer, create_llm):
        shared_llm = object()
        create_llm.return_value = shared_llm
        runtime_config = runtime(AgentEngine.CREWAI)
        bundle = create_agent_bundle(runtime_config, repo_path=".")
        create_llm.assert_called_once_with(runtime_config.llm)
        architect.assert_called_once_with(shared_llm)
        coder.assert_called_once_with(shared_llm, repo_path=".")
        reviewer.assert_called_once_with(shared_llm, repo_path=".")
        self.assertEqual(architect.return_value, bundle.architect)

    @patch("src.agent_factory.detect_runtime")
    def test_supplied_runtime_avoids_detection(self, detect):
        create_agent_bundle(runtime(AgentEngine.HEURISTIC), repo_path=".")
        detect.assert_not_called()

    @patch("src.agent_factory.create_llm")
    def test_offline_runtime_does_not_construct_llm(self, create_llm):
        create_agent_bundle(runtime(AgentEngine.HEURISTIC), repo_path=".")
        create_llm.assert_not_called()

    def test_crewai_dependency_detection_is_available(self):
        from src.runtime_config import crewai_is_installed

        self.assertIsInstance(crewai_is_installed(), bool)


if __name__ == "__main__":
    unittest.main()
