"""Safe Git and GitHub publishing for VisionPR Phase 4."""

from __future__ import annotations

import hashlib
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional at import time
    load_dotenv = None

try:
    from github import Auth, Github
except ImportError:  # pragma: no cover - reported when GitHub access is requested
    Auth = Github = None


LOGGER = logging.getLogger(__name__)
DEFAULT_GIT_TIMEOUT = 60
BLOCKED_PARTS = {
    ".env",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "input_videos",
    "extracted_frames",
    "__pycache__",
}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}
BLOCKED_NAMES = {
    "id_rsa",
    "id_dsa",
    "credentials.json",
    "service-account.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VisionPRError(Exception):
    """A safe, structured error suitable for pipeline boundaries."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: str,
        retriable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation = operation
        self.retriable = retriable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "message": self.message,
            "operation": self.operation,
            "retriable": self.retriable,
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "args": list(self.args),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
        }


def _load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _secret_values() -> list[str]:
    token = os.getenv("GITHUB_TOKEN")
    values = [token] if token else []
    if token:
        values.append(base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii"))
    return values


def _git_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def sanitize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    for secret in _secret_values():
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization:\s*(?:bearer|token)\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"https://[^/@\s]+@github\.com", "https://[REDACTED]@github.com", text)
    return text


def run_git(
    repo_path: Path,
    *args: str,
    timeout: int = DEFAULT_GIT_TIMEOUT,
    check: bool = True,
) -> GitResult:
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
            env=_git_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise VisionPRError(
            "GIT_TIMEOUT",
            f"Git operation timed out after {timeout} seconds.",
            operation=f"git {args[0] if args else ''}".strip(),
            retriable=True,
        ) from exc
    except OSError as exc:
        raise VisionPRError(
            "GIT_UNAVAILABLE",
            "Git could not be executed.",
            operation="git",
            details={"reason": sanitize_text(exc)},
        ) from exc

    result = GitResult(
        args=tuple(args),
        stdout=sanitize_text(completed.stdout).rstrip("\r\n"),
        stderr=sanitize_text(completed.stderr).rstrip("\r\n"),
        returncode=completed.returncode,
    )
    if check and result.returncode != 0:
        combined = f"{result.stderr}\n{result.stdout}".lower()
        auth_failure = any(term in combined for term in ("authentication failed", "could not read username", "permission denied"))
        transient_failure = any(
            term in combined
            for term in (
                "could not resolve host",
                "failed to connect",
                "connection reset",
                "remote end hung up",
                "http 502",
                "http 503",
            )
        )
        raise VisionPRError(
            "GIT_AUTHENTICATION_FAILED" if auth_failure else "GIT_TRANSIENT_FAILURE" if transient_failure else "GIT_COMMAND_FAILED",
            "Git authentication failed." if auth_failure else f"Git command failed: git {args[0] if args else ''}",
            operation=f"git {args[0] if args else ''}".strip(),
            retriable=transient_failure,
            details={"stderr": result.stderr[-1000:], "returncode": result.returncode},
        )
    return result


def _require_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VisionPRError(
            "INVALID_PIPELINE_RESULT",
            f"Phase 3 result is missing required field '{key}'.",
            operation="validate_pipeline_result",
        )
    return value.strip()


def _normalize_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisionPRError("INVALID_FILE_PATH", "Changed file paths must be non-empty strings.", operation="validate_paths")
    raw = value.strip().replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", raw) or ".." in pure.parts:
        raise VisionPRError("UNSAFE_FILE_PATH", f"Unsafe changed-file path: {sanitize_text(value)}", operation="validate_paths")
    normalized = pure.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = {part.lower() for part in PurePosixPath(normalized).parts}
    name = PurePosixPath(normalized).name.lower()
    suffix = PurePosixPath(normalized).suffix.lower()
    if parts & BLOCKED_PARTS or name in BLOCKED_NAMES or suffix in BLOCKED_SUFFIXES or name.startswith(".env."):
        raise VisionPRError("BLOCKED_FILE", f"VisionPR will not publish blocked file: {normalized}", operation="validate_paths")
    return normalized


def validate_changed_files(repo_path: Path, changed_files: Iterable[str]) -> list[str]:
    resolved_repo = repo_path.resolve()
    normalized: list[str] = []
    for value in changed_files:
        relative = _normalize_relative_path(value)
        candidate = (resolved_repo / Path(relative)).resolve(strict=False)
        try:
            candidate.relative_to(resolved_repo)
        except ValueError as exc:
            raise VisionPRError("OUTSIDE_REPOSITORY", f"File is outside the repository: {relative}", operation="validate_paths") from exc
        if relative not in normalized:
            normalized.append(relative)
    if not normalized:
        raise VisionPRError("NO_CHANGED_FILES", "Phase 3 did not report any changed files.", operation="validate_paths")
    return normalized


def validate_repository(
    repo_path: Path,
    remote_name: str | None = None,
    base_branch: str | None = None,
) -> None:
    if not repo_path.exists() or not repo_path.is_dir():
        raise VisionPRError("INVALID_REPOSITORY_PATH", "Target repository directory does not exist.", operation="validate_repository")
    if run_git(repo_path, "rev-parse", "--is-inside-work-tree", check=False).stdout.lower() != "true":
        raise VisionPRError("NOT_A_GIT_REPOSITORY", "Target directory is not a Git repository.", operation="validate_repository")
    remote = remote_name or os.getenv("GITHUB_REMOTE_NAME", "origin")
    if run_git(repo_path, "remote", "get-url", remote, check=False).returncode != 0:
        raise VisionPRError("MISSING_REMOTE", f"Git remote '{remote}' does not exist.", operation="validate_repository")
    base = base_branch or os.getenv("GITHUB_BASE_BRANCH", "main")
    local = run_git(repo_path, "show-ref", "--verify", f"refs/heads/{base}", check=False).returncode == 0
    remote_ref = run_git(repo_path, "show-ref", "--verify", f"refs/remotes/{remote}/{base}", check=False).returncode == 0
    advertised_result = run_git(repo_path, "ls-remote", "--heads", remote, base, check=False)
    advertised = advertised_result.returncode == 0 and bool(advertised_result.stdout)
    if not (local or remote_ref or advertised):
        raise VisionPRError("MISSING_BASE_BRANCH", f"Base branch '{base}' does not exist locally or on '{remote}'.", operation="validate_repository")


def _parse_remote_repository(remote_url: str) -> str | None:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


def _status_paths(repo_path: Path) -> set[str]:
    output = run_git(repo_path, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    paths: set[str] = set()
    entries = output.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:].replace("\\", "/")
        paths.add(path)
        if "R" in status or "C" in status:
            index += 1
            if index < len(entries) and entries[index]:
                paths.add(entries[index].replace("\\", "/"))
        index += 1
    return paths


def ensure_only_intended_worktree_changes(repo_path: Path, intended_files: Iterable[str]) -> None:
    intended = set(intended_files)
    unexpected = sorted(_status_paths(repo_path) - intended)
    if unexpected:
        raise VisionPRError(
            "UNRELATED_LOCAL_CHANGES",
            "The repository contains unrelated local changes; they were preserved and nothing was staged.",
            operation="prepare_repository",
            details={"files": unexpected},
        )


def _slug(value: str, fallback: str = "change") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (result or fallback)[:48].rstrip("-")


def create_or_checkout_feature_branch(
    repo_path: Path,
    run_id: str,
    change_name: str,
    base_branch: str | None = None,
    remote_name: str | None = None,
) -> str:
    safe_run = _slug(run_id, "run")[:12]
    branch = f"visionpr/{_slug(change_name)}-{safe_run}"
    ownership_key = f"branch.{branch}.visionpr-run"

    def verify_local_ownership() -> None:
        owner = run_git(repo_path, "config", "--get", ownership_key, check=False).stdout
        message = run_git(repo_path, "log", "-1", "--format=%B", branch).stdout
        if owner != run_id and f"VisionPR-Run: {run_id}" not in message:
            raise VisionPRError("BRANCH_COLLISION", f"Existing branch '{branch}' is not owned by this VisionPR run.", operation="create_branch")

    current = run_git(repo_path, "branch", "--show-current").stdout
    if current == branch:
        verify_local_ownership()
        return branch
    remote = remote_name or os.getenv("GITHUB_REMOTE_NAME", "origin")
    base = base_branch or os.getenv("GITHUB_BASE_BRANCH", "main")
    exists = run_git(repo_path, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
    if exists:
        verify_local_ownership()
        run_git(repo_path, "switch", branch)
    else:
        remote_branch = run_git(repo_path, "ls-remote", "--heads", remote, branch, check=False).stdout
        if remote_branch:
            run_git(repo_path, "fetch", remote, f"refs/heads/{branch}:refs/remotes/{remote}/{branch}")
            message = run_git(repo_path, "log", "-1", "--format=%B", f"{remote}/{branch}").stdout
            if f"VisionPR-Run: {run_id}" not in message:
                raise VisionPRError("BRANCH_COLLISION", f"Remote branch '{branch}' is not owned by this VisionPR run.", operation="create_branch")
            run_git(repo_path, "switch", "--track", "-c", branch, f"{remote}/{branch}")
        else:
            start_point = f"{remote}/{base}" if run_git(repo_path, "show-ref", "--verify", f"refs/remotes/{remote}/{base}", check=False).returncode == 0 else base
            run_git(repo_path, "switch", "-c", branch, start_point)
        run_git(repo_path, "config", ownership_key, run_id)
    return branch


def stage_intended_files(repo_path: Path, changed_files: list[str]) -> list[str]:
    files = validate_changed_files(repo_path, changed_files)
    for relative in files:
        path = repo_path / Path(relative)
        tracked = run_git(repo_path, "ls-files", "--error-unmatch", "--", relative, check=False).returncode == 0
        if not path.exists() and not tracked:
            raise VisionPRError("MISSING_CHANGED_FILE", f"Changed file does not exist and is not a tracked deletion: {relative}", operation="stage_files")
    run_git(repo_path, "add", "--", *files)
    raw = run_git(repo_path, "diff", "--cached", "--name-only", "-z").stdout
    staged = sorted(item.replace("\\", "/") for item in raw.split("\0") if item)
    if not staged:
        return []
    if set(staged) != set(files):
        raise VisionPRError(
            "UNEXPECTED_STAGED_FILES",
            "The staged file set does not exactly match the intended files.",
            operation="stage_files",
            details={"intended": sorted(files), "staged": staged},
        )
    return staged


def compute_patch_fingerprint(repo_path: Path, changed_files: Iterable[str], *, staged: bool = False) -> str:
    """Hash path, mode, and filtered Git blob so worktree and index views agree."""
    files = validate_changed_files(repo_path, changed_files)
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8") + b"\0")
        if staged:
            entry = run_git(repo_path, "ls-files", "--stage", "--", relative).stdout
            if not entry:
                digest.update(b"DELETED\0")
                continue
            mode, blob_id = entry.split(None, 2)[:2]
            digest.update(mode.encode("ascii") + b"\0" + blob_id.encode("ascii") + b"\0")
        else:
            path = repo_path / Path(relative)
            if not path.exists():
                digest.update(b"DELETED\0")
            else:
                tracked_entry = run_git(repo_path, "ls-files", "--stage", "--", relative).stdout
                mode = tracked_entry.split(None, 1)[0] if tracked_entry else "120000" if path.is_symlink() else "100644"
                blob_id = run_git(repo_path, "hash-object", f"--path={relative}", "--", relative).stdout
                if not blob_id:
                    raise VisionPRError("PATCH_FINGERPRINT_FAILED", "Could not hash changed file content.", operation="fingerprint")
                digest.update(mode.encode("ascii") + b"\0" + blob_id.encode("ascii") + b"\0")
    return digest.hexdigest()


def commit_changes(repo_path: Path, commit_message: str, commit_body: str) -> str:
    if run_git(repo_path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return run_git(repo_path, "rev-parse", "HEAD").stdout
    run_git(repo_path, "commit", "-m", commit_message, "-m", commit_body)
    return run_git(repo_path, "rev-parse", "HEAD").stdout


def push_branch(repo_path: Path, remote_name: str, branch_name: str) -> None:
    remote_ref = run_git(repo_path, "ls-remote", "--heads", remote_name, branch_name, check=False)
    args = ("push", "--set-upstream", remote_name, branch_name) if not remote_ref.stdout else ("push", remote_name, branch_name)
    last_error: VisionPRError | None = None
    for attempt in range(3):
        try:
            run_git(repo_path, *args)
            return
        except VisionPRError as exc:
            last_error = exc
            if not exc.retriable:
                raise
            if attempt < 2:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _github_client(token: str):
    if Github is None:
        raise VisionPRError("PYGITHUB_UNAVAILABLE", "PyGithub is required for GitHub operations.", operation="github_authentication")
    try:
        return Github(auth=Auth.Token(token)) if Auth is not None else Github(token)
    except TypeError:  # PyGithub versions before Auth.Token support
        return Github(token)


def _github_repository(repository_name: str):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise VisionPRError("MISSING_GITHUB_TOKEN", "GITHUB_TOKEN is not configured.", operation="github_authentication")
    try:
        client = _github_client(token)
        client.get_user().login
        return client.get_repo(repository_name)
    except VisionPRError:
        raise
    except Exception as exc:
        raise VisionPRError(
            "GITHUB_AUTHENTICATION_FAILED",
            "GitHub authentication or repository access failed.",
            operation="github_authentication",
            details={"reason": sanitize_text(exc)},
        ) from exc


def create_or_get_pull_request(
    repository_name: str,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    *,
    head_owner: str | None = None,
) -> dict[str, Any]:
    repository = _github_repository(repository_name)
    owner = head_owner or repository_name.split("/", 1)[0]
    head_ref = f"{owner}:{head_branch}"
    try:
        pulls = repository.get_pulls(state="open", head=head_ref, base=base_branch)
        existing = next(iter(pulls), None)
        pr = existing or repository.create_pull(title=sanitize_text(title), body=sanitize_text(body), base=base_branch, head=head_ref)
        return {"number": pr.number, "url": pr.html_url, "created": existing is None, "object": pr}
    except Exception as exc:
        raise VisionPRError("PR_CREATION_FAILED", "Pull Request creation failed.", operation="create_pull_request", details={"reason": sanitize_text(exc)}) from exc


def post_engineer_summary(
    pr_number: int,
    summary_body: str,
    repository_name: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    repository_name = repository_name or os.getenv("GITHUB_REPOSITORY", "")
    repository = _github_repository(repository_name)
    pr = repository.get_pull(pr_number)
    marker = marker or summary_body.splitlines()[0]
    for comment in pr.get_issue_comments():
        if marker and marker in (comment.body or ""):
            return {"id": comment.id, "url": comment.html_url, "created": False}
    comment = pr.create_issue_comment(sanitize_text(summary_body))
    return {"id": comment.id, "url": comment.html_url, "created": True}


def _state_directory() -> Path:
    configured = os.getenv("VISIONPR_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".visionpr" / "state"


def state_file_path(run_id: str) -> Path:
    return _state_directory() / f"{_slug(run_id, 'run')}.json"


def save_state_atomic(state: dict[str, Any], path: Path | None = None) -> Path:
    target = path or state_file_path(str(state.get("run_id", "run")))
    temporary_name: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_state = json.loads(sanitize_text(json.dumps(state, default=str)))
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(safe_state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except OSError as exc:
        raise VisionPRError("STATE_WRITE_FAILED", "VisionPR state could not be saved.", operation="save_state") from exc
    finally:
        if temporary_name and os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return target


def load_pr_state(run_id: str) -> dict[str, Any] | None:
    path = state_file_path(run_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisionPRError("INVALID_STATE", "VisionPR state is unreadable or corrupted.", operation="load_state") from exc
    if not isinstance(data, dict) or data.get("run_id") != run_id:
        raise VisionPRError("INVALID_STATE", "VisionPR state does not match the requested run.", operation="load_state")
    return data


def _markdown_list(values: Iterable[str], empty: str = "Not provided") -> str:
    items = [f"- `{value}`" for value in values]
    return "\n".join(items) if items else empty


def _build_pr_body(data: dict[str, Any], files: list[str], commit_sha: str, iteration: int) -> str:
    build = data.get("build") or {}
    plan = data.get("architect_plan") or {}
    plan_text = plan.get("summary") or "No architect summary was provided."
    steps = plan.get("steps") or []
    if steps:
        plan_text += "\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
    anchors = []
    for anchor in data.get("visual_anchors") or []:
        timestamp = anchor.get("timestamp", "unknown timestamp")
        description = anchor.get("description", "Visual reference")
        url = anchor.get("public_url")
        anchors.append(f"- {timestamp}: {description}" + (f"\n  ![{description}]({url})" if isinstance(url, str) and url.startswith(("https://", "http://")) else ""))
    tests = data.get("tests") or {"status": "NOT_AVAILABLE"}
    return f"""## VisionPR Change Summary

{data.get('requirement_summary', 'No summary provided.')}

## Original Meeting Requirement

{data.get('requirement_summary', 'No summary provided.')}

## Implementation Plan

{plan_text}

## Files Changed

{_markdown_list(files)}

## Build and Test Verification

- Build command: `{build.get('command', 'not provided')}`
- Build status: **{build.get('status', 'unknown')}**
- Test status: **{tests.get('status', 'NOT_AVAILABLE')}**

## Visual Evidence

{chr(10).join(anchors) if anchors else 'No visual evidence was provided.'}

## Human Review Required

Please approve the PR or request changes through a formal review. VisionPR will never merge this PR automatically.

## Pipeline Metadata

- Run ID: `{data.get('run_id')}`
- Review iteration: `{iteration}`
- Commit: `{commit_sha}`
"""


def _engineer_summary(data: dict[str, Any], files: list[str], commit_sha: str, iteration: int) -> str:
    build = data.get("build") or {}
    anchors = [str(item.get("timestamp")) for item in data.get("visual_anchors") or [] if item.get("timestamp")]
    return f"""# VisionPR Engineer Review Summary - Iteration {iteration}

## Meeting Request
{data.get('requirement_summary', 'No summary provided.')}

## Changes and Files
{_markdown_list(files)}

## Verification
- Build: **{build.get('status', 'unknown')}**
- Tests: **{(data.get('tests') or {}).get('status', 'NOT_AVAILABLE')}**
- Commit: `{commit_sha}`

## Assumptions and Visual Anchors
{', '.join(anchors) if anchors else 'No visual-anchor timestamps were provided.'}

## Review Actions
Approve the PR or submit a changes-requested review. The pipeline is waiting for human review and will not merge automatically.
"""


def publish_pull_request(pipeline_result: dict, repo_path: str | None = None) -> dict[str, Any]:
    _load_environment()
    if not isinstance(pipeline_result, dict):
        raise VisionPRError("INVALID_PIPELINE_RESULT", "Phase 3 result must be a dictionary.", operation="validate_pipeline_result")
    run_id = _require_text(pipeline_result, "run_id")
    requirement = _require_text(pipeline_result, "requirement_summary")
    safe_requirement = sanitize_text(requirement)
    build = pipeline_result.get("build")
    if not isinstance(build, dict) or str(build.get("status", "")).lower() != "success":
        raise VisionPRError("BUILD_FAILED", "Phase 3 build did not succeed; the PR was not published.", operation="validate_pipeline_result")
    selected_path = repo_path or pipeline_result.get("repo_path") or os.getenv("TARGET_REPO_PATH")
    if not selected_path:
        raise VisionPRError("INVALID_REPOSITORY_PATH", "No target repository path was provided.", operation="validate_repository")
    repository_path = Path(str(selected_path)).expanduser().resolve()
    remote = os.getenv("GITHUB_REMOTE_NAME", "origin")
    base = str(pipeline_result.get("base_branch") or os.getenv("GITHUB_BASE_BRANCH", "main"))
    repository_name = str(pipeline_result.get("source_repository") or os.getenv("GITHUB_REPOSITORY", "")).strip()
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository_name):
        raise VisionPRError("INVALID_GITHUB_REPOSITORY", "Source repository must use owner/repository format.", operation="validate_configuration")
    push_repository = str(pipeline_result.get("push_repository") or repository_name).strip()
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", push_repository):
        raise VisionPRError("INVALID_GITHUB_REPOSITORY", "Push repository must use owner/repository format.", operation="validate_configuration")
    head_owner = str(pipeline_result.get("head_owner") or push_repository.split("/", 1)[0]).strip()

    validate_repository(repository_path, remote, base)
    files = validate_changed_files(repository_path, pipeline_result.get("changed_files") or [])
    approved = set(validate_changed_files(repository_path, pipeline_result.get("target_files") or files))
    if not set(files) <= approved:
        raise VisionPRError("UNAPPROVED_CHANGED_FILES", "Changed files are not included in the approved target-file set.", operation="validate_paths")
    ensure_only_intended_worktree_changes(repository_path, files)
    remote_url = run_git(repository_path, "remote", "get-url", remote).stdout
    parsed_repository = _parse_remote_repository(remote_url)
    if not parsed_repository:
        raise VisionPRError("UNSUPPORTED_REMOTE", "The configured remote is not a recognizable GitHub repository.", operation="validate_repository")
    if parsed_repository.lower() != push_repository.lower():
        raise VisionPRError("REPOSITORY_MISMATCH", "Writable GitHub repository does not match the local Git remote.", operation="validate_repository")

    _github_repository(repository_name)  # Fail before changing branches when auth is invalid.
    if push_repository.lower() != repository_name.lower():
        _github_repository(push_repository)
    run_git(repository_path, "fetch", "--prune", remote)
    branch = create_or_checkout_feature_branch(repository_path, run_id, safe_requirement, base, remote)
    staged = stage_intended_files(repository_path, files)
    if not staged:
        existing = load_pr_state(run_id)
        if existing and existing.get("pr_number"):
            return existing
        commit_message = run_git(repository_path, "log", "-1", "--format=%B").stdout
        if f"VisionPR-Run: {run_id}" not in commit_message:
            raise VisionPRError("NO_CHANGES", "No meaningful Git diff exists for the intended files.", operation="stage_files")
    iteration = 1
    published_files = staged or files
    if staged:
        fingerprint = compute_patch_fingerprint(repository_path, staged, staged=True)
        subject = f"fix(visionpr): {_slug(safe_requirement)[:50].replace('-', ' ')}"
        body = "\n".join(
            (
                f"VisionPR-Run: {run_id}",
                f"Requirement: {safe_requirement[:500]}",
                "Build: success",
                f"VisionPR-Iteration: {iteration}",
            )
        )
        commit_sha = commit_changes(repository_path, subject, body)
    else:
        commit_sha = run_git(repository_path, "rev-parse", "HEAD").stdout
        fingerprint = compute_patch_fingerprint(repository_path, files)
    remote_branch = run_git(repository_path, "ls-remote", "--heads", remote, branch, check=False).stdout
    remote_sha = remote_branch.split(None, 1)[0] if remote_branch else None
    if remote_sha != commit_sha:
        push_branch(repository_path, remote, branch)
    pr_body = _build_pr_body(pipeline_result, published_files, commit_sha, iteration)
    pr_info = create_or_get_pull_request(
        repository_name,
        base,
        branch,
        f"VisionPR: {safe_requirement[:80]}",
        pr_body,
        head_owner=head_owner,
    )
    marker = f"VisionPR Engineer Review Summary - Iteration {iteration}"
    post_engineer_summary(pr_info["number"], _engineer_summary(pipeline_result, published_files, commit_sha, iteration), repository_name, marker)
    now = utc_now()
    state = {
        "status": "PR_OPENED",
        "run_id": run_id,
        "repository": repository_name,
        "push_repository": push_repository,
        "head_owner": head_owner,
        "repo_path": str(repository_path),
        "base_branch": base,
        "head_branch": branch,
        "remote_name": remote,
        "commit_sha": commit_sha,
        "latest_commit_sha": commit_sha,
        "latest_patch_fingerprint": fingerprint,
        "patch_fingerprints": [fingerprint],
        "pr_number": pr_info["number"],
        "pr_url": pr_info["url"],
        "review_iteration": iteration,
        "changed_files": published_files,
        "build_status": "success",
        "processed_review_ids": [],
        "processed_issue_comment_ids": [],
        "processed_inline_comment_ids": [],
        "pending_feedback": [],
        "iteration_history": [],
        "transition_history": [{"from": None, "to": "PR_OPENED", "at": now}],
        "stop_requested": False,
        "created_at": now,
        "updated_at": now,
    }
    save_state_atomic(state)
    return state
