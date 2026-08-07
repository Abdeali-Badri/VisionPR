import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.pipeline import (
    build_agentic_input_for_repository,
    detect_repository_build_commands,
    prepare_publisher_input,
    run_repository_task,
)
from src.repository_manager import AcquiredRepository
from src.schemas import AgenticInput


def acquired():
    return AcquiredRepository(
        source_repository="owner/project",
        push_repository="contributor/project",
        source_url="https://github.com/owner/project.git",
        local_path="C:/target/project",
        default_branch="main",
        remote_name="origin",
        upstream_remote_name="upstream",
        head_owner="contributor",
        fork_used=True,
    )


class PipelineContractTests(unittest.TestCase):
    def test_detects_framework_build_commands_from_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
            (root / "go.mod").write_text("module example.test/project\n", encoding="utf-8")
            self.assertEqual(["pnpm test", "go test ./..."], detect_repository_build_commands(root))

    def test_python_repository_falls_back_to_compile_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(["python -m compileall ."], detect_repository_build_commands(root))

    @patch("src.pipeline.build_repository_context", return_value={"repo_tree": ".", "relevant_files": []})
    def test_builds_agent_input_from_dynamic_local_clone(self, context):
        result = build_agentic_input_for_repository(
            run_id="run-1",
            issue_summary="Fix save behavior",
            repo_path="C:/target/project",
            build_commands=["python -m unittest"],
        )
        self.assertEqual("Fix save behavior", result.issue_summary)
        context.assert_called_once_with("C:/target/project", "Fix save behavior")

    def test_translates_agent_result_for_fork_pr(self):
        value = AgenticInput("run-1", "Fix save", {"screenshot_context": []}, {}, ["python -m unittest"])
        workflow = {
            "status": "APPROVED_FOR_PR",
            "ready_for_pr": True,
            "changed_files": ["app.py"],
            "build_result": {"status": "success", "commands": [{"return_code": 0}]},
            "architect_plan": {"suspected_cause": "cause", "target_files": ["app.py"], "implementation_steps": ["edit"]},
        }
        result = prepare_publisher_input(workflow, value, acquired())
        self.assertEqual("owner/project", result["source_repository"])
        self.assertEqual("contributor/project", result["push_repository"])
        self.assertEqual(["app.py"], result["target_files"])
        self.assertEqual("success", result["build"]["status"])

    @patch("src.pipeline.publish_pull_request", return_value={"status": "PR_OPENED", "pr_number": 2})
    @patch("src.pipeline.run_agentic_workflow")
    @patch("src.pipeline.build_agentic_input_for_repository")
    @patch("src.pipeline.acquire_repository", return_value=acquired())
    def test_runs_acquisition_agents_and_publisher(self, acquire, build_input, workflow, publish):
        build_input.return_value = AgenticInput("run-1", "Fix save", {}, {}, ["python -m unittest"])
        workflow.return_value = {
            "status": "APPROVED_FOR_PR",
            "ready_for_pr": True,
            "changed_files": ["app.py"],
            "build_result": {"status": "success", "commands": [{"return_code": 0}]},
            "architect_plan": {"target_files": ["app.py"]},
        }
        result = run_repository_task("owner/project", run_id="run-1", issue_summary="Fix save")
        self.assertEqual("PR_OPENED", result["status"])
        workflow.assert_called_once()
        publish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
