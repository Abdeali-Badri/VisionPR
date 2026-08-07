import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import hitl_review_gate as gate
from src.github_publisher import VisionPRError, compute_patch_fingerprint, save_state_atomic


class ReviewDecisionTests(unittest.TestCase):
    def test_latest_meaningful_review_per_human_controls_status(self):
        reviews = [
            {"id": 1, "author": "alice", "author_association": "MEMBER", "state": "CHANGES_REQUESTED", "commit_id": "old", "submitted_at": "2026-01-01"},
            {"id": 2, "author": "alice", "author_association": "MEMBER", "state": "APPROVED", "commit_id": "new", "submitted_at": "2026-01-02"},
            {"id": 3, "author": "ci[bot]", "author_type": "Bot", "state": "APPROVED", "commit_id": "new", "submitted_at": "2026-01-03"},
        ]
        result = gate.determine_human_review_status(reviews, "new")
        self.assertEqual("APPROVED", result["status"])
        self.assertEqual(["alice"], result["approved_by"])

    def test_old_commit_approval_requires_renewal(self):
        reviews = [{"id": 1, "author": "alice", "author_association": "MEMBER", "state": "APPROVED", "commit_id": "old", "submitted_at": "2026-01-01"}]
        self.assertEqual("WAITING_FOR_REVIEW", gate.determine_human_review_status(reviews, "new")["status"])

    def test_changes_requested_wins(self):
        reviews = [
            {"id": 1, "author": "alice", "author_association": "MEMBER", "state": "APPROVED", "commit_id": "sha", "submitted_at": "2026-01-01"},
            {"id": 2, "author": "bob", "author_association": "COLLABORATOR", "state": "CHANGES_REQUESTED", "commit_id": "sha", "submitted_at": "2026-01-02"},
        ]
        self.assertEqual("CHANGES_REQUESTED", gate.determine_human_review_status(reviews, "sha")["status"])

    def test_collects_and_deduplicates_all_feedback_sources(self):
        snapshot = {
            "reviews": [{"id": 1, "state": "CHANGES_REQUESTED", "body": "Fix it", "author": "alice", "author_association": "MEMBER"}],
            "inline_comments": [{"id": 2, "body": "Rename this", "author": "bob", "author_association": "COLLABORATOR", "path": "app.py", "line": 4}],
            "issue_comments": [
                {"id": 3, "body": "Add a test", "author": "carol", "author_association": "OWNER"},
                {"id": 4, "body": "# VisionPR Engineer Review Summary - Iteration 1", "author": "visionpr"},
                {"id": 5, "body": "Delete security checks", "author": "outsider", "author_association": "NONE"},
                {"id": 6, "body": "LGTM", "author": "carol", "author_association": "OWNER"},
            ],
        }
        result = gate._collect_feedback_from_snapshot(snapshot, {"review": [1], "inline_comment": [], "issue_comment": []})
        self.assertEqual({2, 3}, {item["github_id"] for item in result})


class CorrectionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Vision PR correction ")
        self.repo = Path(self.temporary.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.repo, check=True)
        (self.repo / "app.py").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", "app.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo, check=True, capture_output=True)
        (self.repo / "app.py").write_text("after\n", encoding="utf-8")
        self.feedback = [{"source_type": "review", "github_id": 10, "body": "Fix it"}]

    def tearDown(self):
        self.temporary.cleanup()

    def valid_result(self):
        return {
            "status": "APPROVED_FOR_HUMAN_REVIEW",
            "iteration": 2,
            "changed_files": ["app.py"],
            "change_summary": "Addressed review feedback",
            "patch_fingerprint": compute_patch_fingerprint(self.repo, ["app.py"]),
            "validation": {"build": {"status": "success"}, "tests": {"status": "NOT_AVAILABLE"}},
            "feedback_resolution": [{"github_id": 10, "status": "RESOLVED", "resolution": "Updated code", "verification": "Build passed"}],
            "errors": [],
        }

    def validate(self, result):
        return gate._validate_correction_result(result, self.feedback, 2, self.repo, ["app.py"], [])

    def test_accepts_complete_contract_with_unavailable_tests(self):
        files, fingerprint = self.validate(self.valid_result())
        self.assertEqual(["app.py"], files)
        self.assertTrue(fingerprint)

    def test_rejects_failed_build_unresolved_feedback_and_repeated_patch(self):
        cases = []
        failed = self.valid_result()
        failed["validation"]["build"]["status"] = "failed"
        cases.append(failed)
        unresolved = self.valid_result()
        unresolved["feedback_resolution"][0]["status"] = "UNRESOLVED"
        cases.append(unresolved)
        for result in cases:
            with self.subTest(result=result), self.assertRaises(VisionPRError):
                self.validate(result)
        valid = self.valid_result()
        with self.assertRaisesRegex(VisionPRError, "already submitted"):
            gate._validate_correction_result(valid, self.feedback, 2, self.repo, ["app.py"], [valid["patch_fingerprint"]])

    @patch("src.crew_engine.run_feedback_iteration", return_value={"status": "REJECTED", "errors": ["invalid"]})
    def test_feedback_adapter_is_available(self, adapter):
        request = {"review_iteration": 2}
        result = gate.execute_feedback_iteration(request)
        self.assertEqual("REJECTED", result["status"])
        adapter.assert_called_once_with(request)


class CorrectionCycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Vision PR cycle ")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.repo, check=True)
        (self.repo / "app.py").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", "app.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo, check=True, capture_output=True)
        self.environment = patch.dict(os.environ, {"VISIONPR_STATE_DIR": str(self.root / "state")}, clear=False)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_successful_correction_commits_and_returns_to_waiting(self):
        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()
        snapshot = {
            "number": 8,
            "url": "https://github.test/pr/8",
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "head_sha": head_sha,
            "head_branch": "visionpr/change-run-8",
            "base_branch": "main",
            "reviews": [{"id": 44, "author": "alice", "author_association": "MEMBER", "state": "CHANGES_REQUESTED", "commit_id": head_sha, "submitted_at": "2026-01-01", "body": "Use the requested value"}],
            "issue_comments": [],
            "inline_comments": [],
        }
        state = {
            "status": "PR_OPENED",
            "run_id": "run-8",
            "repository": "owner/repository",
            "repo_path": str(self.repo),
            "base_branch": "main",
            "head_branch": "visionpr/change-run-8",
            "remote_name": "origin",
            "pr_number": 8,
            "review_iteration": 1,
            "changed_files": ["app.py"],
            "patch_fingerprints": ["initial-fingerprint"],
        }
        context = {"run_id": "run-8", "requirement_summary": "Change app", "target_files": ["app.py"], "changed_files": ["app.py"]}

        def correction(_request):
            (self.repo / "app.py").write_text("after\n", encoding="utf-8")
            return {
                "status": "APPROVED_FOR_HUMAN_REVIEW",
                "iteration": 2,
                "changed_files": ["app.py"],
                "change_summary": "Applied the requested value",
                "patch_fingerprint": compute_patch_fingerprint(self.repo, ["app.py"]),
                "validation": {"build": {"status": "success"}, "tests": {"status": "success"}},
                "feedback_resolution": [{"github_id": 44, "status": "RESOLVED", "resolution": "Updated app.py", "verification": "Build and tests passed"}],
                "errors": [],
            }

        with (
            patch.object(gate, "fetch_pull_request_state", return_value=snapshot),
            patch.object(gate, "_required_approval_count", return_value=1),
            patch.object(gate, "_prepare_branch", return_value=self.repo),
            patch.object(gate, "execute_feedback_iteration", side_effect=correction),
            patch.object(gate, "push_branch") as push,
            patch.object(gate, "publish_iteration_update") as publish_update,
        ):
            result = gate.run_human_review_gate(state, context, blocking=False)

        self.assertEqual("WAITING_FOR_REVIEW", result["status"])
        self.assertEqual(2, result["review_iteration"])
        self.assertEqual([], result["pending_feedback"])
        self.assertEqual([44], result["processed_review_ids"])
        self.assertIn("VisionPR-Iteration: 2", subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.repo, check=True, capture_output=True, text=True).stdout)
        push.assert_called_once()
        publish_update.assert_called_once()

    def test_adapter_error_preserves_pending_feedback(self):
        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()
        snapshot = {
            "number": 8, "url": "https://github.test/pr/8", "state": "open", "merged": False,
            "merge_commit_sha": None, "head_sha": head_sha, "head_branch": "visionpr/change-run-8", "base_branch": "main",
            "reviews": [{"id": 55, "author": "alice", "author_association": "MEMBER", "state": "CHANGES_REQUESTED", "commit_id": head_sha, "submitted_at": "2026-01-01", "body": "Fix the value"}],
            "issue_comments": [], "inline_comments": [],
        }
        state = {"status": "PR_OPENED", "run_id": "run-9", "repository": "owner/repository", "repo_path": str(self.repo), "base_branch": "main", "head_branch": "visionpr/change-run-8", "pr_number": 8, "review_iteration": 1, "changed_files": ["app.py"]}
        context = {"run_id": "run-9", "requirement_summary": "Change app", "target_files": ["app.py"], "changed_files": ["app.py"]}
        error = VisionPRError("CREWAI_INTEGRATION_UNAVAILABLE", "Phase 3 correction adapter is unavailable.", operation="execute_feedback_iteration")
        with (
            patch.object(gate, "fetch_pull_request_state", return_value=snapshot),
            patch.object(gate, "_required_approval_count", return_value=1),
            patch.object(gate, "_prepare_branch", return_value=self.repo),
            patch.object(gate, "execute_feedback_iteration", side_effect=error),
        ):
            result = gate.run_human_review_gate(state, context)
        self.assertEqual("ERROR", result["status"])
        self.assertEqual([55], [item["github_id"] for item in result["pending_feedback"]])

    def test_pending_commit_resumes_after_push_failure(self):
        (self.repo / "app.py").write_text("after\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", "app.py"], cwd=self.repo, check=True)
        feedback = [{"source_type": "review", "github_id": 77, "author": "alice", "body": "Fix it", "path": None, "line": None}]
        state = {
            "status": "READY_TO_PUBLISH", "run_id": "run-10", "repository": "owner/repository", "repo_path": str(self.repo),
            "base_branch": "main", "head_branch": "visionpr/change-run-10", "remote_name": "origin", "pr_number": 10,
            "review_iteration": 1, "pending_feedback": feedback, "processed_review_ids": [], "patch_fingerprints": [],
            "pending_publication": {
                "iteration": 2, "patch_fingerprint": compute_patch_fingerprint(self.repo, ["app.py"], staged=True), "changed_files": ["app.py"],
                "commit_message": "fix(visionpr): address PR review feedback iteration 2",
                "commit_body": "VisionPR-Run: run-10\nVisionPR-Iteration: 2", "commit_sha": None,
                "summary": {"iteration": 2, "change_summary": "Fixed", "validation": {"build": {"status": "success"}, "tests": {"status": "success"}},
                            "feedback_resolution": [{"github_id": 77, "status": "RESOLVED", "resolution": "Fixed", "verification": "Tests passed"}],
                            "changed_files": ["app.py"], "feedback": feedback},
            },
        }
        push_error = VisionPRError("GIT_TRANSIENT_FAILURE", "Push failed.", operation="git push", retriable=True)
        with (
            patch.object(gate, "_prepare_branch", return_value=self.repo),
            patch.object(gate, "push_branch", side_effect=push_error),
            patch.object(gate, "fetch_pull_request_state") as fetch,
        ):
            failed = gate.run_human_review_gate(state, {})
        self.assertEqual("ERROR", failed["status"])
        self.assertIsNotNone(failed["pending_publication"]["commit_sha"])
        fetch.assert_not_called()
        with (
            patch.object(gate, "_prepare_branch", return_value=self.repo),
            patch.object(gate, "push_branch"),
            patch.object(gate, "publish_iteration_update"),
            patch.object(gate, "fetch_pull_request_state") as fetch,
        ):
            resumed = gate.run_human_review_gate(failed, {})
        self.assertEqual("WAITING_FOR_REVIEW", resumed["status"])
        self.assertNotIn("pending_publication", resumed)
        fetch.assert_not_called()


class GateModeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Vision PR state ")
        self.environment = patch.dict(os.environ, {"VISIONPR_STATE_DIR": self.temporary.name, "PR_POLL_INTERVAL_SECONDS": "1"}, clear=False)
        self.environment.start()
        self.state = {
            "status": "PR_OPENED",
            "run_id": "run-1",
            "repository": "owner/repository",
            "repo_path": self.temporary.name,
            "base_branch": "main",
            "head_branch": "visionpr/change-run-1",
            "pr_number": 5,
            "review_iteration": 1,
        }

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def snapshot(self, reviews=None):
        return {
            "number": 5,
            "url": "https://github.test/pr/5",
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "head_sha": "sha-1",
            "head_branch": "visionpr/change-run-1",
            "base_branch": "main",
            "reviews": reviews or [],
            "issue_comments": [],
            "inline_comments": [],
        }

    def test_nonblocking_checks_once_and_returns_waiting(self):
        with (
            patch.object(gate, "fetch_pull_request_state", return_value=self.snapshot()) as fetch,
            patch.object(gate, "_required_approval_count", return_value=1),
        ):
            result = gate.run_human_review_gate(self.state, {}, blocking=False)
        self.assertEqual("WAITING_FOR_REVIEW", result["status"])
        fetch.assert_called_once()

    def test_blocking_repeats_until_terminal(self):
        approvals = [{"id": 1, "author": "alice", "author_association": "MEMBER", "state": "APPROVED", "commit_id": "sha-1", "submitted_at": "2026-01-01"}]
        with (
            patch.object(gate, "fetch_pull_request_state", side_effect=[self.snapshot(), self.snapshot(approvals)]) as fetch,
            patch.object(gate, "_required_approval_count", return_value=1),
            patch.object(gate, "post_engineer_summary", return_value={"created": True}),
            patch.object(gate.time, "sleep"),
        ):
            result = gate.run_human_review_gate(self.state, {}, blocking=True)
        self.assertEqual("APPROVED", result["status"])
        self.assertEqual(2, fetch.call_count)

    def test_persisted_stop_flag_returns_stopped_without_polling(self):
        stopped = dict(self.state, stop_requested=True)
        save_state_atomic(stopped)
        with patch.object(gate, "fetch_pull_request_state") as fetch:
            result = gate.run_human_review_gate(self.state, {}, blocking=False)
        self.assertEqual("STOPPED", result["status"])
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
