import shutil
import tempfile
import unittest
from pathlib import Path

from src.crew_engine import run_agentic_workflow
from src.llm.config import LLMConfig
from src.llm.providers import LLMProvider
from src.offline_agents import DEMO_TARGET_FILE
from src.runtime_config import AgentEngine, ExecutionMode, RuntimeConfig
from src.schemas import AgenticInput, ArchitectPlan, CoderResult, ReviewerResult


ROOT = Path(__file__).resolve().parents[1]


def runtime(mode=ExecutionMode.OFFLINE_DEMO):
    engine = AgentEngine.CREWAI if mode == ExecutionMode.CREWAI else AgentEngine.HEURISTIC
    return RuntimeConfig(
        engine=engine,
        llm=LLMConfig(LLMProvider.GEMINI, "gemini/gemini-2.5-flash") if engine == AgentEngine.CREWAI else None,
        reason="test runtime",
        requested_mode="test",
        crewai_installed=True,
    )


def demo_input(repo_commands=None):
    return AgenticInput.from_dict(
        {
            "run_id": "mock-agentic-run-001",
            "issue_summary": "Save profile changes.",
            "meeting_issue_context": {},
            "repository_context": {"relevant_files": [{"path": DEMO_TARGET_FILE}]},
            "build_commands": repo_commands if repo_commands is not None else ["python -m unittest discover"],
            "max_review_attempts": 3,
        }
    )


class ApprovingReviewer:
    def review_patch(self, agentic_input, plan, coder_result):
        return ReviewerResult(True, "APPROVED")


class BadCoder:
    def __init__(self, build_status="success", files=None):
        self.build_status = build_status
        self.files = files if files is not None else [DEMO_TARGET_FILE]

    def implement_plan(self, agentic_input, plan, revision_request=None):
        return CoderResult(
            self.files,
            "claimed",
            build_attempted=True,
            build_result={"status": self.build_status},
        )


class StubArchitect:
    def create_plan(self, agentic_input):
        return ArchitectPlan("cause", [DEMO_TARGET_FILE], [], [], [], [])


class CrewEngineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionpr engine ")
        self.repo = Path(self.temp.name) / "repo"
        shutil.copytree(ROOT / "mock_target_repo", self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_metadata_and_demo_ready(self):
        result = run_agentic_workflow(demo_input(), repo_path=self.repo, runtime=runtime())
        self.assertEqual("offline_demo", result["execution_mode"])
        self.assertFalse(result["llm_used"])
        self.assertTrue(result["crewai_installed"])
        self.assertTrue(result["demo_run"])
        self.assertTrue(result["ready_for_pr"])

    def test_crewai_metadata_with_injected_agents(self):
        result = run_agentic_workflow(
            demo_input(repo_commands=[]),
            repo_path=self.repo,
            architect=StubArchitect(),
            coder=BadCoder(build_status="success"),
            reviewer=ApprovingReviewer(),
            runtime=runtime(ExecutionMode.CREWAI),
            run_builds=False,
        )
        self.assertEqual("crewai", result["execution_mode"])
        self.assertTrue(result["llm_used"])

    def test_unsupported_offline_task_not_ready(self):
        agentic_input = AgenticInput.from_dict(
            {"run_id": "other", "issue_summary": "x", "meeting_issue_context": {}, "repository_context": {"relevant_files": [{"path": DEMO_TARGET_FILE}]}, "max_review_attempts": 2}
        )
        result = run_agentic_workflow(agentic_input, repo_path=self.repo, runtime=runtime())
        self.assertFalse(result["ready_for_pr"])
        self.assertEqual(2, result["review_attempts"])

    def test_safety_overrides_reviewer_approval_for_empty_failed_and_timeout(self):
        for build_status, files in (("success", []), ("failed", [DEMO_TARGET_FILE]), ("timeout", [DEMO_TARGET_FILE])):
            with self.subTest(build_status=build_status, files=files):
                result = run_agentic_workflow(
                    demo_input(repo_commands=[]),
                    repo_path=self.repo,
                    architect=StubArchitect(),
                    coder=BadCoder(build_status=build_status, files=files),
                    reviewer=ApprovingReviewer(),
                    runtime=runtime(ExecutionMode.CREWAI),
                    run_builds=False,
                )
                self.assertFalse(result["ready_for_pr"])


if __name__ == "__main__":
    unittest.main()
