import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.tools import (
    ALLOWED_BUILD_COMMAND_PREFIXES,
    ToolSafetyError,
    read_file,
    run_build_plan,
    run_build_test,
    validate_build_command,
    write_file,
)


class SafeFileToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="visionpr tools ")
        self.repo = Path(self.temporary.name) / "target"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "profile.py").write_text("NAME = 'Ada'\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def assert_rejected(self, relative_path):
        with self.assertRaises(ToolSafetyError):
            read_file(self.repo, relative_path)

    def test_read_allowed_file(self):
        self.assertEqual("hello\n", read_file(self.repo, "README.md"))

    def test_write_allowed_file(self):
        result = write_file(self.repo, "profile.py", "name = 'Grace'\n")
        self.assertEqual("profile.py", result["path"])
        self.assertEqual("name = 'Grace'\n", (self.repo / "profile.py").read_text(encoding="utf-8"))

    def test_write_nested_allowed_file(self):
        write_file(self.repo, "nested/directory/generated_file.txt", "generated\n")
        self.assertEqual(
            "generated\n",
            (self.repo / "nested" / "directory" / "generated_file.txt").read_text(encoding="utf-8"),
        )

    def test_reject_absolute_path(self):
        with self.assertRaises(ToolSafetyError):
            read_file(self.repo, str((self.repo / "README.md").resolve()))
        with self.assertRaises(ToolSafetyError):
            read_file(self.repo, "/etc/passwd")
        with self.assertRaises(ToolSafetyError):
            read_file(self.repo, r"C:\Users\example\secret.txt")

    def test_reject_parent_traversal(self):
        self.assert_rejected("../outside.txt")

    def test_reject_hidden_traversal(self):
        self.assert_rejected("src/../../outside.txt")

    def test_reject_env_file(self):
        with self.assertRaises(ToolSafetyError):
            write_file(self.repo, ".env", "SECRET=1\n")

    def test_reject_nested_env_file(self):
        with self.assertRaises(ToolSafetyError):
            write_file(self.repo, "config/.env", "SECRET=1\n")

    def test_reject_git_path(self):
        self.assert_rejected(".git/config")

    def test_reject_node_modules_path(self):
        self.assert_rejected("frontend/node_modules/package.json")

    def test_reject_venv_paths(self):
        self.assert_rejected(".venv/lib/file.py")
        self.assert_rejected("venv/Scripts/python.exe")

    def test_reject_reading_directory(self):
        with self.assertRaisesRegex(ToolSafetyError, "directory"):
            read_file(self.repo, "src")

    def test_reject_missing_file_cleanly(self):
        with self.assertRaisesRegex(ToolSafetyError, "does not exist"):
            read_file(self.repo, "missing.py")


class BuildCommandValidationTests(unittest.TestCase):
    def test_accept_supported_command_families(self):
        examples = [
            "pytest",
            "pytest -q",
            "python -m pytest",
            "python -m pytest tests",
            "python -m unittest",
            "python -m unittest discover -s tests -v",
            "npm test",
            "npm run build",
        ]
        for command in examples:
            with self.subTest(command=command):
                validate_build_command(command)
        self.assertTrue(ALLOWED_BUILD_COMMAND_PREFIXES)

    def test_reject_unsupported_command(self):
        with self.assertRaises(ToolSafetyError):
            validate_build_command("npm install")

    def test_reject_chaining(self):
        for command in ("pytest && echo hacked", "pytest ; another-command", "pytest || echo nope"):
            with self.subTest(command=command), self.assertRaises(ToolSafetyError):
                validate_build_command(command)

    def test_reject_pipe_chaining(self):
        with self.assertRaises(ToolSafetyError):
            validate_build_command("python -m unittest | another-command")

    def test_reject_redirection(self):
        for command in ("pytest > out.txt", "pytest < input.txt"):
            with self.subTest(command=command), self.assertRaises(ToolSafetyError):
                validate_build_command(command)

    def test_reject_env_access(self):
        with self.assertRaises(ToolSafetyError):
            validate_build_command("pytest .env")


class BuildExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="visionpr build ")
        self.repo = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @patch("src.tools.subprocess.run")
    def test_successful_build_output(self, run):
        run.return_value = subprocess.CompletedProcess(["python"], 0, "ok\n", "")
        result = run_build_test(self.repo, "python -m unittest discover", timeout_seconds=7)
        self.assertEqual("success", result["status"])
        self.assertEqual(0, result["return_code"])
        self.assertEqual("ok\n", result["stdout"])
        self.assertEqual(self.repo.resolve(), run.call_args.kwargs["cwd"])
        self.assertEqual(7, run.call_args.kwargs["timeout"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("src.tools.subprocess.run")
    def test_failed_build_output(self, run):
        run.return_value = subprocess.CompletedProcess(["python"], 1, "out\n", "err\n")
        result = run_build_test(self.repo, "python -m unittest discover")
        self.assertEqual("failed", result["status"])
        self.assertEqual(1, result["return_code"])
        self.assertEqual("out\n", result["stdout"])
        self.assertEqual("err\n", result["stderr"])

    @patch("src.tools.subprocess.run")
    def test_timed_out_build_output(self, run):
        run.side_effect = subprocess.TimeoutExpired("python", 3, output="partial\n", stderr="late\n")
        result = run_build_test(self.repo, "python -m unittest discover", timeout_seconds=3)
        self.assertEqual("timeout", result["status"])
        self.assertIsNone(result["return_code"])
        self.assertTrue(result["timed_out"])
        self.assertEqual("partial\n", result["stdout"])

    def test_empty_build_command_returns_skipped(self):
        result = run_build_test(self.repo, "")
        self.assertEqual("skipped", result["status"])
        self.assertIsNone(result["return_code"])
        self.assertFalse(result["timed_out"])

    @patch("src.tools.run_build_test")
    def test_build_plan_stops_after_failure(self, run_one):
        run_one.side_effect = [
            {"status": "success", "command": "python -m unittest", "return_code": 0, "stdout": "", "stderr": "", "timed_out": False},
            {"status": "failed", "command": "pytest", "return_code": 1, "stdout": "", "stderr": "bad", "timed_out": False},
        ]
        result = run_build_plan(self.repo, ["python -m unittest", "pytest", "npm test"])
        self.assertEqual("failed", result["status"])
        self.assertEqual(2, run_one.call_count)

    @patch("src.tools.run_build_test")
    def test_build_plan_stops_after_timeout(self, run_one):
        run_one.side_effect = [
            {"status": "timeout", "command": "python -m unittest", "return_code": None, "stdout": "", "stderr": "", "timed_out": True},
        ]
        result = run_build_plan(self.repo, ["python -m unittest", "pytest"])
        self.assertEqual("timeout", result["status"])
        self.assertEqual(1, run_one.call_count)

    def test_empty_build_plan_returns_skipped(self):
        result = run_build_plan(self.repo, [])
        self.assertEqual("skipped", result["status"])
        self.assertEqual("skipped", result["commands"][0]["status"])


if __name__ == "__main__":
    unittest.main()
