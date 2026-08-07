"""Repository-agnostic orchestration for one VisionPR change task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.codebase_mapper import build_repository_context
from src.crew_engine import run_agentic_workflow
from src.github_publisher import VisionPRError, publish_pull_request
from src.hitl_review_gate import run_human_review_gate
from src.repository_manager import AcquiredRepository, acquire_repository
from src.schemas import AgenticInput


def detect_repository_build_commands(repo_path: str | Path) -> list[str]:
    """Infer conservative validation commands from common project manifests."""
    root = Path(repo_path)
    commands: list[str] = []
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            scripts = dict(json.loads(package_json.read_text(encoding="utf-8")).get("scripts") or {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        test_script = str(scripts.get("test") or "")
        if test_script and "no test specified" not in test_script.lower():
            commands.append(f"{runner} test")
        elif scripts.get("build"):
            commands.append(f"{runner} build" if runner == "yarn" else f"{runner} run build")
    if (root / "go.mod").is_file():
        commands.append("go test ./...")
    if (root / "Cargo.toml").is_file():
        commands.append("cargo test")
    if list(root.glob("*.sln")) or list(root.glob("*.csproj")):
        commands.append("dotnet test")
    if (root / "pom.xml").is_file():
        commands.append("mvn test")
    if (root / "gradlew.bat").is_file():
        commands.append("gradlew.bat test")
    elif (root / "gradlew").is_file():
        commands.append("./gradlew test")
    elif (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        commands.append("gradle test")
    has_python = bool(list(root.glob("*.py"))) or (root / "pyproject.toml").is_file() or (root / "setup.py").is_file()
    if has_python:
        has_tests = (root / "tests").is_dir() or any(root.glob("test_*.py"))
        commands.append("python -m pytest" if has_tests else "python -m compileall .")
    return list(dict.fromkeys(commands))


def build_agentic_input_for_repository(
    *,
    run_id: str,
    issue_summary: str,
    repo_path: str | Path,
    meeting_issue_context: dict[str, Any] | None = None,
    build_commands: list[str] | None = None,
    constraints: list[str] | None = None,
    max_review_attempts: int = 3,
) -> AgenticInput:
    context = build_repository_context(repo_path, issue_summary)
    return AgenticInput(
        run_id=run_id,
        issue_summary=issue_summary,
        meeting_issue_context=dict(meeting_issue_context or {}),
        repository_context=context,
        build_commands=list(build_commands or []),
        constraints=list(constraints or []),
        max_review_attempts=max_review_attempts,
    )


def _exit_code(build_result: dict[str, Any]) -> int | None:
    commands = build_result.get("commands") or []
    return commands[-1].get("return_code") if commands else None


def prepare_publisher_input(
    workflow_result: dict[str, Any],
    agentic_input: AgenticInput,
    repository: AcquiredRepository,
) -> dict[str, Any]:
    """Translate the Phase 3 contract into the GitHub publisher contract."""
    build_result = dict(workflow_result.get("build_result") or {})
    plan = dict(workflow_result.get("architect_plan") or {})
    screenshot_context = agentic_input.meeting_issue_context.get("screenshot_context") or []
    visual_anchors = [
        {
            "timestamp": item.get("timestamp"),
            "description": item.get("description") or "Recording evidence",
            "local_path": item.get("path"),
            "public_url": item.get("public_url"),
        }
        for item in screenshot_context
        if isinstance(item, dict)
    ]
    status = str(build_result.get("status") or "").lower()
    return {
        **workflow_result,
        "repo_path": repository.local_path,
        "base_branch": repository.default_branch,
        "source_repository": repository.source_repository,
        "push_repository": repository.push_repository,
        "head_owner": repository.head_owner,
        "requirement_summary": agentic_input.issue_summary,
        "meeting_issue_context": agentic_input.meeting_issue_context,
        "build_commands": list(agentic_input.build_commands),
        "constraints": list(agentic_input.constraints),
        "target_files": list(plan.get("target_files") or []),
        "build": {
            "command": " && ".join(agentic_input.build_commands),
            "status": status,
            "exit_code": _exit_code(build_result),
        },
        "tests": {"status": "SUCCESS" if status == "success" else "NOT_AVAILABLE"},
        "architect_plan": {
            **plan,
            "summary": plan.get("suspected_cause") or "VisionPR implementation plan",
            "steps": list(plan.get("implementation_steps") or []),
        },
        "visual_anchors": visual_anchors,
    }


def run_repository_task(
    repository_url: str,
    *,
    run_id: str,
    issue_summary: str,
    meeting_issue_context: dict[str, Any] | None = None,
    build_commands: list[str] | None = None,
    constraints: list[str] | None = None,
    workspace_dir: str | Path | None = None,
    publish: bool = True,
    enter_review_gate: bool = False,
    blocking_review: bool = False,
) -> dict[str, Any]:
    """Clone/fork, understand, edit, validate, and optionally publish one task."""
    repository = acquire_repository(
        repository_url,
        run_id=run_id,
        workspace_dir=workspace_dir,
        prepare_push=publish,
        reset_existing=True,
    )
    selected_build_commands = list(build_commands or detect_repository_build_commands(repository.local_path))
    agentic_input = build_agentic_input_for_repository(
        run_id=run_id,
        issue_summary=issue_summary,
        repo_path=repository.local_path,
        meeting_issue_context=meeting_issue_context,
        build_commands=selected_build_commands,
        constraints=constraints,
    )
    workflow = run_agentic_workflow(
        agentic_input,
        repo_path=repository.local_path,
        verify_worktree=True,
    )
    result: dict[str, Any] = {
        "status": workflow["status"],
        "repository": repository.to_dict(),
        "agent_workflow_result": workflow,
        "pr_state": None,
    }
    if not publish or not workflow.get("ready_for_pr"):
        return result

    publisher_input = prepare_publisher_input(workflow, agentic_input, repository)
    if str((publisher_input.get("build") or {}).get("status")) != "success":
        raise VisionPRError(
            "BUILD_REQUIRED",
            "A successful build or test command is required before publishing.",
            operation="publish_repository_task",
        )
    pr_state = publish_pull_request(publisher_input, repo_path=repository.local_path)
    result.update({"status": pr_state["status"], "publisher_input": publisher_input, "pr_state": pr_state})
    if enter_review_gate:
        result["review_state"] = run_human_review_gate(pr_state, publisher_input, blocking=blocking_review)
        result["status"] = result["review_state"]["status"]
    return result
