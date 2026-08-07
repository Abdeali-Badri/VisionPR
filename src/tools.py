"""Local tools used by the VisionPR agent workflow.

These functions are intentionally framework-neutral. CrewAI wrappers can call
them, tests can call them directly, and the safety rules stay in one place.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


BLOCKED_PATH_PARTS = {
    ".git",
    ".env",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}
GENERATED_BUILD_PATH_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
ALLOWED_BUILD_COMMAND_PREFIXES = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("python", "-m", "compileall"),
    ("python", "-m", "py_compile"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("npm", "run", "build"),
    ("pnpm", "test"),
    ("pnpm", "run", "test"),
    ("pnpm", "run", "build"),
    ("yarn", "test"),
    ("yarn", "build"),
    ("go", "test"),
    ("cargo", "test"),
    ("dotnet", "test"),
    ("mvn", "test"),
    ("gradle", "test"),
    ("gradlew.bat", "test"),
    ("./gradlew", "test"),
)
SHELL_METACHARACTERS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\n", "\r")
DESTRUCTIVE_REWRITE_MIN_LINES = 40
DESTRUCTIVE_REWRITE_MIN_REMOVED_LINES = 30
DESTRUCTIVE_REWRITE_MAX_REMAINING_RATIO = 0.25


class ToolSafetyError(ValueError):
    """Raised when an agent requests an unsafe file operation."""


def _repo_root(repo_path: str | Path) -> Path:
    return Path(repo_path).resolve()


def _path_parts(relative_path: str) -> tuple[str, ...]:
    normalized = relative_path.replace("\\", "/")
    return PurePosixPath(normalized).parts


def resolve_repo_path(repo_path: str | Path, relative_path: str) -> Path:
    """Resolve a repository-relative path and reject escapes or blocked paths."""
    if not relative_path:
        raise ToolSafetyError("Path must be a non-empty repository-relative path.")
    if (
        Path(relative_path).is_absolute()
        or PureWindowsPath(relative_path).is_absolute()
        or PurePosixPath(relative_path).is_absolute()
    ):
        raise ToolSafetyError(f"Path must be repository-relative: {relative_path!r}")

    parts = _path_parts(relative_path)
    lowered_parts = {part.lower() for part in parts}
    if ".." in parts:
        raise ToolSafetyError(f"Path traversal is not allowed: {relative_path}")
    if any(part in BLOCKED_PATH_PARTS for part in lowered_parts):
        raise ToolSafetyError(f"Path is blocked for agent access: {relative_path}")

    root = _repo_root(repo_path)
    target = (root / Path(*parts)).resolve()
    if target != root and root not in target.parents:
        raise ToolSafetyError(f"Path escapes repository root: {relative_path}")
    return target


def read_file(repo_path: str | Path, relative_path: str) -> str:
    """Read a UTF-8 text file from the target repository."""
    target = resolve_repo_path(repo_path, relative_path)
    if not target.exists():
        raise ToolSafetyError(f"File does not exist: {relative_path}")
    if target.is_dir():
        raise ToolSafetyError(f"Path is a directory, not a file: {relative_path}")
    return target.read_text(encoding="utf-8")


def write_file(repo_path: str | Path, relative_path: str, content: str) -> dict[str, Any]:
    """Write a UTF-8 text file inside the target repository."""
    target = resolve_repo_path(repo_path, relative_path)
    root = _repo_root(repo_path)
    parent = target.parent.resolve()
    if parent != root and root not in parent.parents:
        raise ToolSafetyError(f"Parent path escapes repository root: {relative_path}")
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        existing_lines = existing.splitlines()
        replacement_lines = content.splitlines()
        removed_lines = len(existing_lines) - len(replacement_lines)
        remaining_ratio = len(replacement_lines) / max(len(existing_lines), 1)
        if (
            len(existing_lines) >= DESTRUCTIVE_REWRITE_MIN_LINES
            and removed_lines >= DESTRUCTIVE_REWRITE_MIN_REMOVED_LINES
            and remaining_ratio <= DESTRUCTIVE_REWRITE_MAX_REMAINING_RATIO
        ):
            raise ToolSafetyError(
                "VisionPR blocked a destructive partial overwrite of "
                f"{relative_path}: the replacement keeps only {len(replacement_lines)} of "
                f"{len(existing_lines)} lines. Preserve the complete file and apply a focused edit."
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": str(PurePosixPath(*_path_parts(relative_path))),
        "absolute_path": str(target),
        "bytes_written": len(content.encode("utf-8")),
    }


def _git_output(repo_path: str | Path, args: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_repo_root(repo_path),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ToolSafetyError("Git is required to verify repository changes.") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ToolSafetyError(f"Git could not inspect the target repository: {message}")
    return completed.stdout


def list_worktree_changes(repo_path: str | Path) -> list[str]:
    """Return tracked and untracked repository-relative paths changed from HEAD."""
    tracked = _git_output(repo_path, ["diff", "--name-only", "--relative", "-z", "HEAD"])
    untracked = _git_output(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    paths = {
        str(PurePosixPath(path))
        for path in (tracked + untracked).decode("utf-8", errors="replace").split("\0")
        if path
    }
    return sorted(paths)


def cleanup_generated_build_artifacts(
    repo_path: str | Path,
    changes_before_build: list[str],
) -> list[str]:
    """Remove only new, untracked cache files created by a validation command."""
    root = _repo_root(repo_path)
    before = set(changes_before_build)
    untracked = _git_output(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    removed: list[str] = []
    for relative_path in untracked.decode("utf-8", errors="replace").split("\0"):
        if not relative_path or relative_path in before:
            continue
        parts = {part.lower() for part in _path_parts(relative_path)}
        if not parts.intersection(GENERATED_BUILD_PATH_PARTS):
            continue
        target = (root / Path(*_path_parts(relative_path))).resolve()
        if target == root or root not in target.parents:
            raise ToolSafetyError(f"Generated artifact escapes repository root: {relative_path}")
        if target.is_file():
            target.unlink()
            removed.append(str(PurePosixPath(relative_path)))

        parent = target.parent
        while parent != root and root in parent.parents:
            if parent.name.lower() not in GENERATED_BUILD_PATH_PARTS or any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
    return sorted(removed)


def read_git_diff(repo_path: str | Path, max_chars: int = 30_000) -> str:
    """Return a bounded, read-only patch for agent and reviewer grounding."""
    patch = _git_output(repo_path, ["diff", "--no-ext-diff", "--relative", "HEAD", "--"])
    text = patch.decode("utf-8", errors="replace")
    untracked = list_worktree_changes(repo_path)
    if untracked and not text:
        text = "Untracked changed files: " + ", ".join(untracked)
    if len(text) > max_chars:
        return text[:max_chars] + "\n... diff truncated by VisionPR ...\n"
    return text


def validate_build_command(command: str) -> None:
    """Reject arbitrary shell commands before build/test execution."""
    stripped = command.strip()
    if not stripped:
        return
    if ".env" in stripped.lower():
        raise ToolSafetyError("Build commands must not access .env files.")
    if any(marker in stripped for marker in SHELL_METACHARACTERS):
        raise ToolSafetyError(f"Shell chaining and redirection are not allowed: {command}")

    try:
        args = shlex.split(stripped, posix=os.name != "nt")
    except ValueError as exc:
        raise ToolSafetyError(f"Build command could not be parsed safely: {command}") from exc
    if not args:
        return

    for allowed_prefix in ALLOWED_BUILD_COMMAND_PREFIXES:
        if tuple(args[: len(allowed_prefix)]) == allowed_prefix:
            return
    allowed = ", ".join(" ".join(prefix) for prefix in ALLOWED_BUILD_COMMAND_PREFIXES)
    raise ToolSafetyError(f"Unsupported build command: {command}. Allowed prefixes: {allowed}")


def _build_args(command: str) -> list[str]:
    validate_build_command(command)
    args = shlex.split(command.strip(), posix=os.name != "nt")
    if args and args[0] == "python":
        args[0] = sys.executable
    return args


def run_build_test(repo_path: str | Path, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Run one build/test command and return structured logs."""
    stripped = command.strip()
    if not stripped:
        return {
            "command": command,
            "status": "skipped",
            "return_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
        }

    args = _build_args(stripped)
    try:
        completed = subprocess.run(
            args,
            cwd=_repo_root(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "timeout",
            "return_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "command": command,
            "status": "failed",
            "return_code": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
        }

    return {
        "command": command,
        "status": "success" if completed.returncode == 0 else "failed",
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": False,
    }


def run_build_plan(repo_path: str | Path, commands: list[str]) -> dict[str, Any]:
    """Run build/test commands in order and stop on the first failure."""
    if not commands:
        return {
            "status": "skipped",
            "commands": [
                {
                    "status": "skipped",
                    "command": "",
                    "return_code": None,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                }
            ],
        }

    results = []
    for command in commands:
        result = run_build_test(repo_path, command)
        results.append(result)
        if result["status"] != "success":
            return {"status": result["status"], "commands": results}
    return {"status": "success", "commands": results}
