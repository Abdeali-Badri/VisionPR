import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.crew_engine import run_feedback_iteration


class FeedbackIterationTests(unittest.TestCase):
    def request(self, repo_path):
        return {
            "run_id": "run-1",
            "review_iteration": 2,
            "original_requirement": "Fix save behavior",
            "engineer_feedback": [{"github_id": 12, "body": "Handle empty names", "path": "app.py", "line": 4}],
            "target_files": ["app.py"],
            "repo_path": str(repo_path),
            "build_commands": ["python -m unittest"],
            "constraints": ["Do not modify unrelated files"],
        }

    @patch("src.github_publisher.compute_patch_fingerprint", return_value="fingerprint")
    @patch("src.crew_engine.run_agentic_workflow")
    @patch("src.codebase_mapper.build_repository_context", return_value={"repo_tree": ".", "relevant_files": []})
    def test_successful_feedback_correction_contract(self, context, workflow, fingerprint):
        workflow.return_value = {
            "ready_for_pr": True,
            "changed_files": ["app.py"],
            "build_result": {"status": "success", "commands": []},
            "coder_result": {"change_summary": "Handled empty names."},
        }
        with tempfile.TemporaryDirectory(prefix="visionpr feedback ") as tmp:
            result = run_feedback_iteration(self.request(Path(tmp)))
        self.assertEqual("APPROVED_FOR_HUMAN_REVIEW", result["status"])
        self.assertEqual("RESOLVED", result["feedback_resolution"][0]["status"])
        self.assertEqual("app.py", result["feedback_resolution"][0]["file"])
        self.assertEqual("fingerprint", result["patch_fingerprint"])

    def test_rejects_missing_feedback(self):
        with tempfile.TemporaryDirectory(prefix="visionpr feedback ") as tmp:
            request = self.request(Path(tmp))
            request["engineer_feedback"] = []
            result = run_feedback_iteration(request)
        self.assertEqual("REJECTED", result["status"])
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
