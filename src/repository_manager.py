"""Acquire user-supplied GitHub repositories for safe local editing."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from src.github_publisher import VisionPRError, _github_client, sanitize_text

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - runtime dependency checks cover this
    load_dotenv = None


@dataclass(frozen=True)
class GitHubRepositoryRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.full_name}.git"


@dataclass(frozen=True)
class AcquiredRepository:
    source_repository: str
    push_repository: str
    source_url: str
    local_path: str
    default_branch: str
    remote_name: str
    upstream_remote_name: str | None
    head_owner: str
    fork_used: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_github_repository(value: str) -> GitHubRepositoryRef:
    """Parse a public GitHub URL or owner/repository identifier."""
    raw = str(value or "").strip()
    if not raw:
        raise VisionPRError("INVALID_REPOSITORY_URL", "A GitHub repository URL is required.", operation="parse_repository_url")

    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
    elif re.fullmatch(r"[^/\s]+/[^/\s]+(?:\.git)?", raw):
        path = raw
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname not in {"github.com", "www.github.com"}:
            raise VisionPRError(
                "UNSUPPORTED_REPOSITORY_URL",
                "Only GitHub repository URLs are supported by the current publisher.",
                operation="parse_repository_url",
            )
        path = parsed.path.lstrip("/")

    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise VisionPRError("INVALID_REPOSITORY_URL", "GitHub repository must use owner/repository format.", operation="parse_repository_url")
    return GitHubRepositoryRef(parts[0], parts[1])


def _git_environment() -> tuple[dict[str, str], list[str]]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    secrets: list[str] = []
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
        secrets.extend((token, basic))
    return env, secrets


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 120, check: bool = True) -> str:
    env, secrets = _git_environment()
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VisionPRError("GIT_ACQUISITION_FAILED", "Git could not acquire the target repository.", operation="acquire_repository") from exc
    if check and completed.returncode != 0:
        detail = sanitize_text(completed.stderr or completed.stdout)
        for secret in secrets:
            detail = detail.replace(secret, "[REDACTED]")
        raise VisionPRError(
            "GIT_ACQUISITION_FAILED",
            "Git could not acquire the target repository.",
            operation="acquire_repository",
            details={"stderr": detail[-1000:], "returncode": completed.returncode},
        )
    return completed.stdout.strip()


def _safe_folder(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._") or "repository"


def _fork_for_user(client: Any, source: Any, login: str, attempts: int = 12) -> Any:
    fork_name = f"{login}/{source.name}"
    try:
        existing = client.get_repo(fork_name)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise VisionPRError("GITHUB_FORK_LOOKUP_FAILED", "Could not check for an existing repository fork.", operation="fork_repository") from exc
        existing = None

    if existing is not None:
        parent = getattr(getattr(existing, "parent", None), "full_name", None)
        if not getattr(existing, "fork", False) or str(parent or "").lower() != source.full_name.lower():
            raise VisionPRError("FORK_NAME_COLLISION", f"{fork_name} exists but is not a fork of {source.full_name}.", operation="fork_repository")
        return existing

    try:
        source.create_fork()
    except Exception as exc:
        if getattr(exc, "status", None) == 403:
            raise VisionPRError(
                "GITHUB_FORK_PERMISSION_REQUIRED",
                "The token can read this public repository but cannot create the fork required for a pull request.",
                operation="fork_repository",
                details={
                    "required_action": "Use a GitHub credential that can create repositories/forks, or grant direct push access to the source repository."
                },
            ) from exc
        raise VisionPRError("GITHUB_FORK_FAILED", "GitHub could not create a fork for the target repository.", operation="fork_repository") from exc

    for attempt in range(attempts):
        try:
            return client.get_repo(fork_name)
        except Exception as exc:
            if getattr(exc, "status", None) != 404 or attempt == attempts - 1:
                raise VisionPRError("GITHUB_FORK_NOT_READY", "The GitHub fork did not become ready in time.", operation="fork_repository", retriable=True) from exc
            time.sleep(min(1 + attempt, 5))
    raise AssertionError("fork polling exhausted")


def _prepare_clone(
    push_repo: Any,
    source_repo: Any,
    target: Path,
    default_branch: str,
    *,
    reset_existing: bool = False,
) -> None:
    if target.exists():
        if not (target / ".git").exists():
            raise VisionPRError("CLONE_PATH_COLLISION", "Managed clone path exists but is not a Git repository.", operation="clone_repository")
        if _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=target):
            if not reset_existing:
                raise VisionPRError("DIRTY_MANAGED_CLONE", "Managed target clone contains uncommitted changes.", operation="clone_repository")
            _run_git(["reset", "--hard", "HEAD"], cwd=target)
            _run_git(["clean", "-fd"], cwd=target)
        _run_git(["fetch", "--prune", "origin"], cwd=target)
        _run_git(["switch", default_branch], cwd=target)
        _run_git(["merge", "--ff-only", f"origin/{default_branch}"], cwd=target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--origin", "origin", "--branch", default_branch, "--single-branch", push_repo.clone_url, str(target)])

    origin = _run_git(["remote", "get-url", "origin"], cwd=target)
    if parse_github_repository(origin).full_name.lower() != push_repo.full_name.lower():
        raise VisionPRError("CLONE_REMOTE_MISMATCH", "Managed clone origin does not match the writable repository.", operation="clone_repository")

    if push_repo.full_name.lower() != source_repo.full_name.lower():
        upstream = _run_git(["remote", "get-url", "upstream"], cwd=target) if "upstream" in _run_git(["remote"], cwd=target).splitlines() else ""
        if upstream:
            if parse_github_repository(upstream).full_name.lower() != source_repo.full_name.lower():
                raise VisionPRError("CLONE_REMOTE_MISMATCH", "Managed clone upstream does not match the source repository.", operation="clone_repository")
        else:
            _run_git(["remote", "add", "upstream", source_repo.clone_url], cwd=target)


def acquire_repository(
    repository_url: str,
    *,
    run_id: str,
    workspace_dir: str | Path | None = None,
    prepare_push: bool = True,
    reset_existing: bool = False,
) -> AcquiredRepository:
    """Fork when necessary and clone a user-supplied repository locally."""
    reference = parse_github_repository(repository_url)
    if load_dotenv is not None and not os.getenv("PYTHON_DOTENV_DISABLED"):
        load_dotenv()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token and prepare_push:
        raise VisionPRError("MISSING_GITHUB_TOKEN", "GITHUB_TOKEN is required to prepare a pull request.", operation="acquire_repository")
    if token:
        client = _github_client(token)
    else:
        from github import Github

        client = Github()
    try:
        source = client.get_repo(reference.full_name)
        user = client.get_user() if token else SimpleNamespace(login="VisionPR", email=None)
    except Exception as exc:
        raise VisionPRError("GITHUB_REPOSITORY_UNAVAILABLE", "The requested GitHub repository is unavailable to the configured account.", operation="acquire_repository") from exc

    permissions = getattr(source, "permissions", None)
    can_push = bool(getattr(permissions, "push", False))
    push_repo = source if can_push or not prepare_push else _fork_for_user(client, source, user.login)
    base = Path(workspace_dir) if workspace_dir else Path.cwd() / "data" / "target_repositories"
    target = (base / _safe_folder(run_id) / f"{_safe_folder(reference.owner)}--{_safe_folder(reference.name)}").resolve()
    _prepare_clone(
        push_repo,
        source,
        target,
        source.default_branch,
        reset_existing=reset_existing,
    )

    email = getattr(user, "email", None) or f"{user.login}@users.noreply.github.com"
    if not _run_git(["config", "--get", "user.name"], cwd=target, check=False):
        _run_git(["config", "user.name", user.login], cwd=target)
    if not _run_git(["config", "--get", "user.email"], cwd=target, check=False):
        _run_git(["config", "user.email", email], cwd=target)

    fork_used = push_repo.full_name.lower() != source.full_name.lower()
    return AcquiredRepository(
        source_repository=source.full_name,
        push_repository=push_repo.full_name,
        source_url=reference.clone_url,
        local_path=str(target),
        default_branch=source.default_branch,
        remote_name="origin",
        upstream_remote_name="upstream" if fork_used else None,
        head_owner=push_repo.owner.login,
        fork_used=fork_used,
    )
