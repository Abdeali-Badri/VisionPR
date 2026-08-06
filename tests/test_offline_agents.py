import shutil
import tempfile
import unittest
from pathlib import Path

from src.offline_agents import DEMO_TARGET_FILE, PATCHED_SNIPPET, OfflineArchitectAgent, OfflineCoderAgent, OfflineReviewerAgent
from src.schemas import AgenticInput, ArchitectPlan, CoderResult


ROOT = Path(__file__).resolve().parents[1]


def demo_input() -> AgenticInput:
    return AgenticInput.from_dict(
        {
            "run_id": "mock-agentic-run-001",
            "issue_summary": "Save profile changes.",
            "meeting_issue_context": {},
            "repository_context": {"relevant_files": [{"path": DEMO_TARGET_FILE, "summary": "profile helper"}]},
            "build_commands": ["python -m unittest discover"],
            "constraints": ["Do not modify routing files."],
            "max_review_attempts": 3,
        }
    )


class OfflineAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionpr offline ")
        self.repo = Path(self.temp.name) / "repo"
        shutil.copytree(ROOT / "mock_target_repo", self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_architect_is_deterministic_and_honest(self):
        plan = OfflineArchitectAgent().create_plan(demo_input())
        self.assertEqual([DEMO_TARGET_FILE], plan.target_files)
        self.assertIn("No LLM reasoning", " ".join(plan.risk_notes))

    def test_architect_does_not_invent_missing_target_files(self):
        agentic_input = AgenticInput.from_dict(
            {"run_id": "x", "issue_summary": "x", "meeting_issue_context": {}, "repository_context": {}}
        )
        plan = OfflineArchitectAgent().create_plan(agentic_input)
        self.assertEqual([], plan.target_files)

    def test_coder_rejects_unsupported_scenario(self):
        agentic_input = AgenticInput.from_dict(
            {
                "run_id": "other",
                "issue_summary": "x",
                "meeting_issue_context": {},
                "repository_context": {"relevant_files": [{"path": DEMO_TARGET_FILE}]},
            }
        )
        plan = ArchitectPlan("cause", [DEMO_TARGET_FILE], [], [], [], [])
        result = OfflineCoderAgent(self.repo).implement_plan(agentic_input, plan)
        self.assertEqual([], result.modified_files)

    def test_coder_applies_demo_patch_and_runs_build(self):
        plan = OfflineArchitectAgent().create_plan(demo_input())
        result = OfflineCoderAgent(self.repo).implement_plan(demo_input(), plan)
        self.assertEqual([DEMO_TARGET_FILE], result.modified_files)
        self.assertEqual("success", result.build_result["status"])
        self.assertIn(PATCHED_SNIPPET, (self.repo / DEMO_TARGET_FILE).read_text(encoding="utf-8"))

    def test_reviewer_rejects_empty_patch_failed_build_timeout_and_protected_path(self):
        reviewer = OfflineReviewerAgent(self.repo)
        plan = ArchitectPlan("cause", [DEMO_TARGET_FILE], [], [], [], [])
        empty = CoderResult([], "none")
        self.assertFalse(reviewer.review_patch(demo_input(), plan, empty).approved)
        failed = CoderResult([DEMO_TARGET_FILE], "bad", build_attempted=True, build_result={"status": "failed"})
        self.assertFalse(reviewer.review_patch(demo_input(), plan, failed).approved)
        timeout = CoderResult([DEMO_TARGET_FILE], "bad", build_attempted=True, build_result={"status": "timeout"})
        self.assertFalse(reviewer.review_patch(demo_input(), plan, timeout).approved)
        protected = CoderResult([".env"], "bad", build_attempted=True, build_result={"status": "success"})
        self.assertFalse(reviewer.review_patch(demo_input(), ArchitectPlan("cause", [".env"], [], [], [], []), protected).approved)

    def test_reviewer_approves_valid_demo_patch(self):
        plan = OfflineArchitectAgent().create_plan(demo_input())
        result = OfflineCoderAgent(self.repo).implement_plan(demo_input(), plan)
        review = OfflineReviewerAgent(self.repo).review_patch(demo_input(), plan, result)
        self.assertTrue(review.approved)

    def test_rerunning_demo_is_predictable(self):
        plan = OfflineArchitectAgent().create_plan(demo_input())
        first = OfflineCoderAgent(self.repo).implement_plan(demo_input(), plan)
        second = OfflineCoderAgent(self.repo).implement_plan(demo_input(), plan)
        self.assertEqual([DEMO_TARGET_FILE], first.modified_files)
        self.assertEqual([DEMO_TARGET_FILE], second.modified_files)
        self.assertEqual("success", second.build_result["status"])


if __name__ == "__main__":
    unittest.main()
