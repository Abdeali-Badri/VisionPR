import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
from src.generate_summary import load_agent_result, load_transcript
from src.github_publisher import VisionPRError


class LoaderTests(unittest.TestCase):
    def test_load_agent_result_returns_valid_phase3_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent_result.json"
            payload = {
                "status": "APPROVED_FOR_PR",
                "changed_files": ["app.py"],
                "coder_result": {"change_summary": "Updated app."},
                "reviewer_result": {"approved": True},
                "review_attempts": 1,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(payload, load_agent_result(path))

    def test_load_agent_result_lists_missing_required_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent_result.json"
            path.write_text(json.dumps({"status": "APPROVED_FOR_PR"}), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "changed_files, coder_result, review_attempts, reviewer_result",
            ):
                load_agent_result(path)

    def test_load_transcript_returns_json_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transcript.json"
            payload = {"transcript": {"english": "Save does not work.", "segments": []}}
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(payload, load_transcript(path))


class OrchestrationTests(unittest.TestCase):
    def test_phase4_error_is_returned_without_entering_review_gate(self):
        error = VisionPRError("BUILD_FAILED", "Build failed.", operation="publish")
        with (
            patch.object(main, "publish_pull_request", side_effect=error),
            patch.object(main, "run_human_review_gate") as review,
        ):
            result = main.run_phase4_and_phase5({"run_id": "run-1"})
        self.assertEqual("ERROR", result["status"])
        self.assertEqual(4, result["phase"])
        review.assert_not_called()

    def test_phase5_receives_blocking_mode(self):
        published = {"status": "PR_OPENED", "run_id": "run-1"}
        with (
            patch.object(main, "publish_pull_request", return_value=published),
            patch.object(main, "run_human_review_gate", return_value={"status": "WAITING_FOR_REVIEW"}) as review,
        ):
            result = main.run_phase4_and_phase5({"run_id": "run-1"}, blocking=True)
        self.assertEqual(5, result["phase"])
        self.assertTrue(review.call_args.kwargs["blocking"])


if __name__ == "__main__":
    unittest.main()
