import unittest

from backend.worker import report_outcome, task_display_status


class WorkerReportOutcomeTests(unittest.TestCase):
    def test_review_failed_task_is_not_presented_as_awaiting_review(self):
        task = {
            "status": "REVIEW_FAILED",
            "change_summary": "The generated patch deleted most of the application source.",
            "pr_url": None,
        }

        outcome = report_outcome({"status": "INCOMPLETE", "tasks": [task]})

        self.assertEqual("REVIEW_FAILED", outcome.status)
        self.assertEqual(task, outcome.failed_task)
        self.assertEqual(task["change_summary"], outcome.error_message)
        self.assertEqual("review_failed", task_display_status(task))

    def test_pr_task_is_awaiting_human_review(self):
        task = {"status": "PR_OPENED", "pr_url": "https://github.test/pull/7"}

        outcome = report_outcome({"status": "COMPLETE", "tasks": [task]})

        self.assertEqual("AWAITING_HUMAN_REVIEW", outcome.status)
        self.assertIsNone(outcome.failed_task)
        self.assertIsNone(outcome.error_message)
        self.assertEqual("awaiting_review", task_display_status(task))


if __name__ == "__main__":
    unittest.main()
