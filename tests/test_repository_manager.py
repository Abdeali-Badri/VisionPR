import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.github_publisher import VisionPRError
from src.repository_manager import _prepare_clone, acquire_repository, parse_github_repository


class RepositoryUrlTests(unittest.TestCase):
    def test_parses_supported_github_forms(self):
        for value in (
            "https://github.com/owner/project",
            "https://github.com/owner/project.git",
            "git@github.com:owner/project.git",
            "owner/project",
        ):
            with self.subTest(value=value):
                self.assertEqual("owner/project", parse_github_repository(value).full_name)

    def test_rejects_non_github_and_non_repository_urls(self):
        for value in ("", "https://gitlab.com/owner/project", "https://github.com/owner/project/issues"):
            with self.subTest(value=value), self.assertRaises(VisionPRError):
                parse_github_repository(value)


class RepositoryAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionpr acquisition ")
        self.workspace = Path(self.temp.name)
        self.user = SimpleNamespace(login="contributor", email=None)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def repository(full_name, *, push, fork=False, parent=None):
        owner, name = full_name.split("/", 1)
        return SimpleNamespace(
            full_name=full_name,
            name=name,
            owner=SimpleNamespace(login=owner),
            clone_url=f"https://github.com/{full_name}.git",
            default_branch="main",
            permissions=SimpleNamespace(push=push),
            fork=fork,
            parent=parent,
        )

    @patch.dict("os.environ", {"GITHUB_TOKEN": "secret"})
    @patch("src.repository_manager._prepare_clone")
    @patch("src.repository_manager._run_git", return_value="configured")
    @patch("src.repository_manager._github_client")
    def test_direct_push_repository_is_cloned_without_fork(self, client_factory, run_git, prepare_clone):
        source = self.repository("owner/project", push=True)
        client_factory.return_value.get_user.return_value = self.user
        client_factory.return_value.get_repo.return_value = source

        result = acquire_repository("owner/project", run_id="run-1", workspace_dir=self.workspace)

        self.assertFalse(result.fork_used)
        self.assertEqual("owner/project", result.push_repository)
        prepare_clone.assert_called_once()

    @patch.dict("os.environ", {"GITHUB_TOKEN": "secret"})
    @patch("src.repository_manager._prepare_clone")
    @patch("src.repository_manager._run_git", return_value="configured")
    @patch("src.repository_manager._github_client")
    def test_read_only_repository_uses_existing_user_fork(self, client_factory, run_git, prepare_clone):
        source = self.repository("owner/project", push=False)
        fork = self.repository("contributor/project", push=True, fork=True, parent=source)
        client = client_factory.return_value
        client.get_user.return_value = self.user
        client.get_repo.side_effect = lambda name: source if name == "owner/project" else fork

        result = acquire_repository("https://github.com/owner/project", run_id="run-2", workspace_dir=self.workspace)

        self.assertTrue(result.fork_used)
        self.assertEqual("owner/project", result.source_repository)
        self.assertEqual("contributor/project", result.push_repository)
        self.assertEqual("contributor", result.head_owner)

    @patch.dict("os.environ", {"GITHUB_TOKEN": "secret"})
    @patch("src.repository_manager._github_client")
    def test_reports_missing_fork_permission_separately_from_read_access(self, client_factory):
        source = self.repository("owner/project", push=False)
        forbidden = RuntimeError("forbidden")
        forbidden.status = 403
        source.create_fork = Mock(side_effect=forbidden)
        missing = RuntimeError("missing")
        missing.status = 404
        client = client_factory.return_value
        client.get_user.return_value = self.user
        client.get_repo.side_effect = [source, missing]

        with self.assertRaises(VisionPRError) as caught:
            acquire_repository("owner/project", run_id="run-3", workspace_dir=self.workspace)

        self.assertEqual("GITHUB_FORK_PERMISSION_REQUIRED", caught.exception.code)

    @patch.dict("os.environ", {"PYTHON_DOTENV_DISABLED": "1"}, clear=True)
    @patch("src.repository_manager._prepare_clone")
    @patch("src.repository_manager._run_git", return_value="configured")
    @patch("github.Github")
    def test_public_repository_can_be_cloned_anonymously_for_local_work(
        self, github_factory, run_git, prepare_clone
    ):
        source = self.repository("owner/project", push=False)
        github_factory.return_value.get_repo.return_value = source

        result = acquire_repository(
            "owner/project",
            run_id="local-only",
            workspace_dir=self.workspace,
            prepare_push=False,
        )

        self.assertEqual("owner/project", result.source_repository)
        self.assertFalse(result.fork_used)
        prepare_clone.assert_called_once()

    @patch("src.repository_manager._run_git")
    def test_explicit_retry_resets_only_the_existing_managed_clone(self, run_git):
        target = self.workspace / "managed"
        (target / ".git").mkdir(parents=True)
        repository = self.repository("owner/project", push=True)

        def output(args, **_kwargs):
            if args[:2] == ["status", "--porcelain=v1"]:
                return " M app.py"
            if args == ["remote", "get-url", "origin"]:
                return repository.clone_url
            if args == ["remote"]:
                return "origin"
            return ""

        run_git.side_effect = output

        _prepare_clone(repository, repository, target, "main", reset_existing=True)

        calls = [call.args[0] for call in run_git.call_args_list]
        self.assertIn(["reset", "--hard", "HEAD"], calls)
        self.assertIn(["clean", "-fd"], calls)


if __name__ == "__main__":
    unittest.main()
