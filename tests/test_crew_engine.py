import subprocess
import tempfile
import unittest
from pathlib import Path

from src.crew_engine import run_agentic_workflow
from src.runtime_config import AgentEngine, RuntimeConfig
from src.schemas import ArchitectPlan, CoderResult, ReviewerResult


def sample_agentic_input():
    return {
        "run_id": "test-run-1",
        "issue_summary": "Save button does not persist profile edits.",
        "meeting_issue_context": {
            "transcript_segments": [
                {"timestamp": "00:00:04", "text": "The save button is not working."}
            ],
            "screenshot_context": [],
        },
        "repository_context": {
            "repo_tree": "src/pages/Profile.jsx",
            "relevant_files": [
                {
                    "path": "src/pages/Profile.jsx",
                    "summary": "Profile form and save handler.",
                    "symbols": ["ProfilePage", "handleSave"],
                }
            ],
        },
        "build_commands": [],
        "constraints": ["Do not modify routing files."],
        "max_review_attempts": 2,
    }


def heuristic_runtime():
    return RuntimeConfig(
        engine=AgentEngine.HEURISTIC,
        llm=None,
        reason="Unit test forces deterministic agents.",
        requested_mode="heuristic",
        crewai_installed=True,
    )


class StubArchitect:
    def create_plan(self, agentic_input):
        return ArchitectPlan(
            suspected_cause="Save handler does not call the update API.",
            target_files=["src/pages/Profile.jsx"],
            files_to_avoid=["src/router.jsx"],
            required_changes=["Call updateProfile from handleSave."],
            implementation_steps=["Edit handleSave."],
            test_plan=["npm test"],
        )


class SuccessfulCoder:
    def implement_plan(self, agentic_input, plan, revision_request=None):
        return CoderResult(
            modified_files=["src/pages/Profile.jsx"],
            change_summary="Updated save handler.",
            patch_notes=["Changed only the planned file."],
        )


class EmptyCoder:
    def __init__(self):
        self.calls = 0

    def implement_plan(self, agentic_input, plan, revision_request=None):
        self.calls += 1
        return CoderResult(
            modified_files=[],
            change_summary="No changes made.",
            patch_notes=[f"attempt {self.calls}"],
        )


class ApprovingReviewer:
    def review_patch(self, agentic_input, plan, coder_result):
        return ReviewerResult(
            approved=True,
            verdict="APPROVED",
            issues_found=[],
            next_action="send_to_pr_publisher",
        )


class CrewEngineTests(unittest.TestCase):
    def test_approved_patch_is_ready_for_pr_publishing(self):
        result = run_agentic_workflow(
            sample_agentic_input(),
            architect=StubArchitect(),
            coder=SuccessfulCoder(),
            reviewer=ApprovingReviewer(),
            runtime=heuristic_runtime(),
            run_builds=False,
        )

        self.assertEqual("APPROVED_FOR_PR", result["status"])
        self.assertTrue(result["ready_for_pr"])
        self.assertEqual(["src/pages/Profile.jsx"], result["changed_files"])
        self.assertEqual(1, result["review_attempts"])
        self.assertEqual(str(Path(".").resolve()), result["repo_path"])
        self.assertEqual("skipped", result["build_result"]["status"])
        self.assertIn("Save button", result["pr_title"])
        self.assertIn("src/pages/Profile.jsx", result["pr_summary"])
        self.assertIn("Commit this progress", result["commit_reminder"])

    def test_default_reviewer_rejects_empty_patch_until_retry_limit(self):
        coder = EmptyCoder()
        result = run_agentic_workflow(
            sample_agentic_input(),
            architect=StubArchitect(),
            coder=coder,
            runtime=heuristic_runtime(),
            run_builds=False,
        )

        self.assertEqual("REVIEW_FAILED", result["status"])
        self.assertFalse(result["ready_for_pr"])
        self.assertEqual(2, result["review_attempts"])
        self.assertEqual(2, coder.calls)
        self.assertIn("Coder did not modify any files.", result["reviewer_result"]["issues_found"])

    def test_default_architect_uses_repository_context_file_names(self):
        result = run_agentic_workflow(
            sample_agentic_input(),
            runtime=heuristic_runtime(),
            run_builds=False,
        )

        self.assertEqual(["src/pages/Profile.jsx"], result["architect_plan"]["target_files"])
        self.assertIn("ready_for_pr", result)
        self.assertIn("build_result", result)
        self.assertIn("pr_title", result)
        self.assertIn("pr_summary", result)
        self.assertNotIn("phase1", str(result).lower())
        self.assertNotIn("phase2", str(result).lower())

    def test_verified_workflow_removes_cache_created_by_build(self):
        with tempfile.TemporaryDirectory(prefix="visionpr verified workflow ") as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "VisionPR Tests"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "tests@visionpr.local"], cwd=repo, check=True)
            (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)

            class Architect:
                def create_plan(self, agentic_input):
                    return ArchitectPlan("Update value", ["app.py"], [], ["Set VALUE to 2"], ["Edit app.py"], [])

            class Coder:
                def implement_plan(self, agentic_input, plan, revision_request=None):
                    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
                    return CoderResult(["app.py"], "Updated value")

            payload = sample_agentic_input()
            payload["build_commands"] = ["python -m compileall ."]
            result = run_agentic_workflow(
                payload,
                repo_path=repo,
                architect=Architect(),
                coder=Coder(),
                reviewer=ApprovingReviewer(),
                runtime=heuristic_runtime(),
                verify_worktree=True,
            )

            self.assertEqual("APPROVED_FOR_PR", result["status"])
            self.assertEqual(["app.py"], result["changed_files"])
            self.assertFalse((repo / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
