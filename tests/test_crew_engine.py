import unittest

from src.crew_engine import run_agentic_workflow
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
            run_builds=False,
        )

        self.assertEqual("APPROVED_FOR_PR", result["status"])
        self.assertEqual(["src/pages/Profile.jsx"], result["changed_files"])
        self.assertEqual(1, result["review_attempts"])
        self.assertIn("Commit this progress", result["commit_reminder"])

    def test_default_reviewer_rejects_empty_patch_until_retry_limit(self):
        coder = EmptyCoder()
        result = run_agentic_workflow(
            sample_agentic_input(),
            architect=StubArchitect(),
            coder=coder,
            run_builds=False,
        )

        self.assertEqual("REVIEW_FAILED", result["status"])
        self.assertEqual(2, result["review_attempts"])
        self.assertEqual(2, coder.calls)
        self.assertIn("Coder did not modify any files.", result["reviewer_result"]["issues_found"])

    def test_default_architect_uses_repository_context_file_names(self):
        result = run_agentic_workflow(sample_agentic_input(), run_builds=False)

        self.assertEqual(["src/pages/Profile.jsx"], result["architect_plan"]["target_files"])
        self.assertNotIn("phase1", str(result).lower())
        self.assertNotIn("phase2", str(result).lower())


if __name__ == "__main__":
    unittest.main()
