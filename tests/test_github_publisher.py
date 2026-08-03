import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import github_publisher as publisher


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Vision PR tests ")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "engineer repo"
        self.remote = self.root / "remote repo.git"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "visionpr@example.invalid")
        git(self.repo, "config", "user.name", "VisionPR Tests")
        (self.repo / "app.py").write_text("print('before')\n", encoding="utf-8")
        git(self.repo, "add", "--", "app.py")
        git(self.repo, "commit", "-m", "initial")
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")
        self.state_dir = self.root / "state files"
        self.environment = patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "test-token-secret",
                "GITHUB_REPOSITORY": "owner/repository",
                "VISIONPR_STATE_DIR": str(self.state_dir),
                "GITHUB_REMOTE_NAME": "origin",
                "GITHUB_BASE_BRANCH": "main",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()


class GitSafetyTests(RepositoryCase):
    def test_rejects_unsafe_and_blocked_paths(self):
        for value in ("../outside.py", "C:/outside.py", ".env", ".git/config", "node_modules/x.js", "key.pem"):
            with self.subTest(value=value), self.assertRaises(publisher.VisionPRError):
                publisher.validate_changed_files(self.repo, [value])

    def test_only_github_remotes_can_be_matched(self):
        self.assertIsNone(publisher._parse_remote_repository(str(self.remote)))
        self.assertEqual("owner/repository", publisher._parse_remote_repository("git@github.com:owner/repository.git"))

    def test_unrelated_changes_are_preserved_and_rejected(self):
        (self.repo / "app.py").write_text("print('after')\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaisesRegex(publisher.VisionPRError, "unrelated local changes"):
            publisher.ensure_only_intended_worktree_changes(self.repo, ["app.py"])
        self.assertTrue((self.repo / "notes.txt").exists())

    def test_stages_exact_files_and_fingerprint_matches(self):
        (self.repo / "app.py").write_text("print('after')\n", encoding="utf-8")
        before = publisher.compute_patch_fingerprint(self.repo, ["app.py"])
        staged = publisher.stage_intended_files(self.repo, ["app.py"])
        after = publisher.compute_patch_fingerprint(self.repo, staged, staged=True)
        self.assertEqual(["app.py"], staged)
        self.assertEqual(before, after)
        self.assertEqual("app.py", git(self.repo, "diff", "--cached", "--name-only"))

    def test_no_changes_returns_empty_staged_set(self):
        self.assertEqual([], publisher.stage_intended_files(self.repo, ["app.py"]))

    def test_feature_branch_is_deterministic_and_uses_base(self):
        branch = publisher.create_or_checkout_feature_branch(
            self.repo, "run-123456789", "Update Navbar", "main", "origin"
        )
        self.assertEqual("visionpr/update-navbar-run-12345678", branch)
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(branch, publisher.create_or_checkout_feature_branch(self.repo, "run-123456789", "Update Navbar"))

    def test_atomic_state_redacts_token(self):
        path = publisher.save_state_atomic({"run_id": "run-1", "note": "test-token-secret"})
        self.assertEqual("[REDACTED]", json.loads(path.read_text(encoding="utf-8"))["note"])
        self.assertFalse(list(path.parent.glob("*.tmp")))


class GithubPublishingTests(RepositoryCase):
    def pipeline_result(self):
        return {
            "run_id": "run-42",
            "repo_path": str(self.repo),
            "base_branch": "main",
            "requirement_summary": "Update navbar alignment",
            "target_files": ["app.py"],
            "changed_files": ["app.py"],
            "build": {"command": "python -m compileall .", "status": "success", "exit_code": 0},
            "tests": {"status": "NOT_AVAILABLE"},
            "architect_plan": {"summary": "Adjust layout", "steps": ["Edit the component"]},
            "visual_anchors": [{"timestamp": "00:01:14", "local_path": "C:/frame.jpg", "description": "Navbar", "public_url": None}],
        }

    def test_create_or_get_pull_request_reuses_existing(self):
        existing = Mock(number=7, html_url="https://github.test/pr/7")
        repository = Mock()
        repository.get_pulls.return_value = [existing]
        with patch.object(publisher, "_github_repository", return_value=repository):
            result = publisher.create_or_get_pull_request("owner/repository", "main", "visionpr/x", "Title", "Body")
        self.assertFalse(result["created"])
        repository.create_pull.assert_not_called()

    def test_create_pull_sanitizes_outbound_text(self):
        created = Mock(number=8, html_url="https://github.test/pr/8")
        repository = Mock()
        repository.get_pulls.return_value = []
        repository.create_pull.return_value = created
        with patch.object(publisher, "_github_repository", return_value=repository):
            publisher.create_or_get_pull_request("owner/repository", "main", "visionpr/x", "test-token-secret", "token=test-token-secret")
        self.assertNotIn("test-token-secret", repository.create_pull.call_args.kwargs["title"])
        self.assertNotIn("test-token-secret", repository.create_pull.call_args.kwargs["body"])

    def test_publish_creates_branch_commit_pr_and_state(self):
        (self.repo / "app.py").write_text("print('after')\n", encoding="utf-8")
        pr_result = {"number": 12, "url": "https://github.test/pr/12", "created": True, "object": Mock()}
        with (
            patch.object(publisher, "_github_repository", return_value=Mock()),
            patch.object(publisher, "_parse_remote_repository", return_value="owner/repository"),
            patch.object(publisher, "create_or_get_pull_request", return_value=pr_result) as create_pr,
            patch.object(publisher, "post_engineer_summary", return_value={"created": True}),
        ):
            state = publisher.publish_pull_request(self.pipeline_result())
        self.assertEqual("PR_OPENED", state["status"])
        self.assertEqual(12, state["pr_number"])
        self.assertTrue(state["head_branch"].startswith("visionpr/update-navbar-alignment-"))
        self.assertIn("VisionPR-Run: run-42", git(self.repo, "log", "-1", "--format=%B"))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), git(self.repo, "rev-parse", "origin/" + state["head_branch"]))
        body = create_pr.call_args.args[4]
        self.assertNotIn("![Navbar](C:/frame.jpg)", body)
        self.assertIn("will never merge", body)

    def test_publish_reuses_persisted_pr_when_no_diff(self):
        (self.repo / "app.py").write_text("print('after')\n", encoding="utf-8")
        pr_result = {"number": 12, "url": "https://github.test/pr/12", "created": True, "object": Mock()}
        with (
            patch.object(publisher, "_github_repository", return_value=Mock()),
            patch.object(publisher, "_parse_remote_repository", return_value="owner/repository"),
            patch.object(publisher, "create_or_get_pull_request", return_value=pr_result),
            patch.object(publisher, "post_engineer_summary", return_value={"created": True}),
        ):
            first = publisher.publish_pull_request(self.pipeline_result())
            second = publisher.publish_pull_request(self.pipeline_result())
        self.assertEqual(first["pr_number"], second["pr_number"])
        self.assertEqual(first["commit_sha"], second["commit_sha"])

    def test_publish_recovers_existing_run_when_state_file_is_missing(self):
        (self.repo / "app.py").write_text("print('after')\n", encoding="utf-8")
        pr_result = {"number": 12, "url": "https://github.test/pr/12", "created": False, "object": Mock()}
        with (
            patch.object(publisher, "_github_repository", return_value=Mock()),
            patch.object(publisher, "_parse_remote_repository", return_value="owner/repository"),
            patch.object(publisher, "create_or_get_pull_request", return_value=pr_result) as create_pr,
            patch.object(publisher, "post_engineer_summary", return_value={"created": False}),
        ):
            first = publisher.publish_pull_request(self.pipeline_result())
            publisher.state_file_path("run-42").unlink()
            second = publisher.publish_pull_request(self.pipeline_result())
        self.assertEqual(first["commit_sha"], second["commit_sha"])
        self.assertEqual(2, create_pr.call_count)


if __name__ == "__main__":
    unittest.main()
