"""Local tools used by the VisionPR agent workflow.

These functions are intentionally framework-neutral. CrewAI wrappers can call
them, tests can call them directly, and the safety rules stay in one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


BLOCKED_PATH_PARTS = {
    ".git",
    ".env",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


class ToolSafetyError(ValueError):
    """Raised when an agent requests an unsafe file operation."""


def _repo_root(repo_path: str | Path) -> Path:
    return Path(repo_path).resolve()


def resolve_repo_path(repo_path: str | Path, relative_path: str) -> Path:
    """Resolve a repository-relative path and reject escapes or blocked paths."""
    if not relative_path or Path(relative_path).is_absolute():
        raise ToolSafetyError(f"Path must be repository-relative: {relative_path!r}")

    normalized = Path(relative_path)
    if any(part in BLOCKED_PATH_PARTS for part in normalized.parts):
        raise ToolSafetyError(f"Path is blocked for agent access: {relative_path}")

    root = _repo_root(repo_path)
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ToolSafetyError(f"Path escapes repository root: {relative_path}")
    return target


def read_file(repo_path: str | Path, relative_path: str) -> str:
    """Read a UTF-8 text file from the target repository."""
    target = resolve_repo_path(repo_path, relative_path)
    return target.read_text(encoding="utf-8")


def write_file(repo_path: str | Path, relative_path: str, content: str) -> str:
    """Write a UTF-8 text file inside the target repository."""
    target = resolve_repo_path(repo_path, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def run_build_test(repo_path: str | Path, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Run one build/test command and return structured logs."""
    if not command.strip():
        return {
            "command": command,
            "status": "skipped",
            "exit_code": 0,
            "stdout": "",
            "stderr": "No build command was provided.",
        }

    try:
        completed = subprocess.run(
            command,
            cwd=_repo_root(repo_path),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "failed",
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": f"Command timed out after {timeout_seconds} seconds.\n{exc.stderr or ''}",
        }

    return {
        "command": command,
        "status": "success" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_build_plan(repo_path: str | Path, commands: list[str]) -> dict[str, Any]:
    """Run build/test commands in order and stop on the first failure."""
    results = []
    for command in commands:
        result = run_build_test(repo_path, command)
        results.append(result)
        if result["status"] != "success":
            return {"status": "failed", "commands": results}
    return {"status": "success" if results else "skipped", "commands": results}
