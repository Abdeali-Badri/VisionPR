import unittest
from unittest.mock import patch

import main
from src.github_publisher import VisionPRError


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
