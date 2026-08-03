"""Human review coordination for VisionPR Phase 5."""

from __future__ import annotations

import importlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable

from src.github_publisher import (
    VisionPRError,
    _github_repository,
    commit_changes,
    compute_patch_fingerprint,
    ensure_only_intended_worktree_changes,
    load_pr_state,
    post_engineer_summary,
    push_branch,
    run_git,
    sanitize_text,
    save_state_atomic,
    stage_intended_files,
    utc_now,
    validate_changed_files,
)


LOGGER = logging.getLogger(__name__)
TERMINAL_STATUSES = {"APPROVED", "MERGED", "CLOSED", "STOPPED", "ERROR"}
VISIONPR_MARKER = "VisionPR Engineer Review Summary"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _author(item: Any) -> tuple[str, str]:
    user = _value(item, "user")
    if isinstance(user, dict):
        return str(user.get("login") or ""), str(user.get("type") or "")
    return str(getattr(user, "login", "") or ""), str(getattr(user, "type", "") or "")


def _is_service_account(login: str, account_type: str = "") -> bool:
    configured = {name.strip().lower() for name in os.getenv("VISIONPR_SERVICE_ACCOUNTS", "visionpr").split(",") if name.strip()}
    lowered = login.lower()
    return account_type.lower() == "bot" or lowered.endswith("[bot]") or lowered in configured


def _is_trusted_author(login: str, association: str) -> bool:
    configured = {name.strip().lower() for name in os.getenv("VISIONPR_AUTHORIZED_REVIEWERS", "").split(",") if name.strip()}
    return association.upper() in TRUSTED_ASSOCIATIONS or login.lower() in configured


def _serialize_review(review: Any) -> dict[str, Any]:
    login, account_type = _author(review)
    return {
        "id": _value(review, "id"),
        "state": str(_value(review, "state", "")).upper(),
        "body": str(_value(review, "body", "") or ""),
        "author": login,
        "author_type": account_type,
        "author_association": str(_value(review, "author_association", "") or ""),
        "commit_id": _value(review, "commit_id"),
        "submitted_at": _iso(_value(review, "submitted_at")),
        "url": _value(review, "html_url") or _value(review, "url"),
    }


def _serialize_issue_comment(comment: Any) -> dict[str, Any]:
    login, account_type = _author(comment)
    return {
        "id": _value(comment, "id"),
        "body": str(_value(comment, "body", "") or ""),
        "author": login,
        "author_type": account_type,
        "author_association": str(_value(comment, "author_association", "") or ""),
        "created_at": _iso(_value(comment, "created_at")),
        "url": _value(comment, "html_url") or _value(comment, "url"),
    }


def _serialize_inline_comment(comment: Any) -> dict[str, Any]:
    result = _serialize_issue_comment(comment)
    result.update(
        {
            "path": _value(comment, "path"),
            "line": _value(comment, "line") or _value(comment, "original_line"),
            "commit_id": _value(comment, "commit_id"),
            "in_reply_to_id": _value(comment, "in_reply_to_id"),
        }
    )
    return result


def _github_call(operation: str, callback, attempts: int = 3):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return callback()
        except VisionPRError:
            raise
        except Exception as exc:
            last_error = exc
            status = getattr(exc, "status", None)
            headers = {str(key).lower(): str(value) for key, value in (getattr(exc, "headers", {}) or {}).items()}
            rate_limited = status == 429 or (status == 403 and headers.get("x-ratelimit-remaining") == "0")
            retriable = rate_limited or status in {500, 502, 503, 504} or status is None
            if not retriable or attempt == attempts - 1:
                code = "GITHUB_RATE_LIMITED" if rate_limited else "GITHUB_API_FAILED"
                raise VisionPRError(
                    code,
                    f"GitHub operation failed: {operation}",
                    operation=operation,
                    retriable=retriable,
                    details={"reason": sanitize_text(exc), "status": status},
                ) from exc
            reset_at = int(headers.get("x-ratelimit-reset", "0") or 0)
            rate_delay = max(1, reset_at - int(time.time())) if rate_limited and reset_at else 0
            time.sleep(min(max(2**attempt, rate_delay), 30))
    assert last_error is not None
    raise last_error


def fetch_pull_request_state(pr_number: int, repository_name: str | None = None) -> dict[str, Any]:
    repository_name = repository_name or os.getenv("GITHUB_REPOSITORY", "")

    def fetch() -> dict[str, Any]:
        repository = _github_repository(repository_name)
        pr = repository.get_pull(pr_number)
        return {
            "number": pr.number,
            "url": pr.html_url,
            "state": str(pr.state).lower(),
            "merged": bool(pr.merged),
            "merge_commit_sha": getattr(pr, "merge_commit_sha", None),
            "head_sha": pr.head.sha,
            "head_branch": pr.head.ref,
            "base_branch": pr.base.ref,
            "reviews": [_serialize_review(item) for item in pr.get_reviews()],
            "issue_comments": [_serialize_issue_comment(item) for item in pr.get_issue_comments()],
            "inline_comments": [_serialize_inline_comment(item) for item in pr.get_review_comments()],
        }

    return _github_call("fetch_pull_request_state", fetch)


def determine_human_review_status(
    reviews: list[Any],
    current_commit_sha: str,
    *,
    minimum_approvals: int = 1,
) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for raw in reviews:
        review = raw if isinstance(raw, dict) else _serialize_review(raw)
        login = str(review.get("author") or "")
        state = str(review.get("state") or "").upper()
        if not login or not _is_trusted_author(login, str(review.get("author_association") or "")) or _is_service_account(login, str(review.get("author_type") or "")) or state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        previous = latest.get(login.lower())
        ordering = str(review.get("submitted_at") or "")
        previous_ordering = str((previous or {}).get("submitted_at") or "")
        if previous is None or ordering >= previous_ordering:
            latest[login.lower()] = review

    requested_by = sorted(str(item.get("author")) for item in latest.values() if item.get("state") == "CHANGES_REQUESTED")
    if requested_by:
        return {"status": "CHANGES_REQUESTED", "approved_by": [], "changes_requested_by": requested_by}
    approved_by = sorted(
        str(item.get("author"))
        for item in latest.values()
        if item.get("state") == "APPROVED" and str(item.get("commit_id") or "") == current_commit_sha
    )
    return {
        "status": "APPROVED" if len(approved_by) >= max(1, minimum_approvals) else "WAITING_FOR_REVIEW",
        "approved_by": approved_by,
        "changes_requested_by": [],
    }


def _required_approval_count(repository_name: str, base_branch: str) -> int:
    configured = max(1, int(os.getenv("VISIONPR_MIN_APPROVALS", "1")))
    try:
        repository = _github_repository(repository_name)
        protection = repository.get_branch(base_branch).get_required_pull_request_reviews()
        value = getattr(protection, "required_approving_review_count", None)
        return max(configured, int(value)) if value is not None else configured
    except Exception:
        LOGGER.info("Branch protection approval count unavailable; using configured minimum.")
        return configured


def _feedback_key(item: dict[str, Any]) -> tuple[str, int]:
    return str(item["source_type"]), int(item["github_id"])


def _is_actionable_body(body: str) -> bool:
    harmless = {"lgtm", "looks good", "thanks", "thank you", "approved", "+1"}
    return body.strip().lower().rstrip(".! ") not in harmless


def _collect_feedback_from_snapshot(snapshot: dict[str, Any], processed_ids: dict[str, Iterable[int]]) -> list[dict[str, Any]]:
    processed = {
        "review": {int(value) for value in processed_ids.get("review", [])},
        "issue_comment": {int(value) for value in processed_ids.get("issue_comment", [])},
        "inline_comment": {int(value) for value in processed_ids.get("inline_comment", [])},
    }
    feedback: list[dict[str, Any]] = []
    for review in snapshot.get("reviews", []):
        github_id = review.get("id")
        body = str(review.get("body") or "").strip()
        if review.get("state") != "CHANGES_REQUESTED" or not github_id or not body or int(github_id) in processed["review"]:
            continue
        if not _is_trusted_author(str(review.get("author") or ""), str(review.get("author_association") or "")) or _is_service_account(str(review.get("author") or ""), str(review.get("author_type") or "")):
            continue
        feedback.append({"source_type": "review", "github_id": int(github_id), "author": review.get("author"), "body": body, "path": None, "line": None, "created_at": review.get("submitted_at"), "url": review.get("url")})
    for source_key, source_type in (("inline_comments", "inline_comment"), ("issue_comments", "issue_comment")):
        for comment in snapshot.get(source_key, []):
            github_id = comment.get("id")
            body = str(comment.get("body") or "").strip()
            if not github_id or not body or int(github_id) in processed[source_type]:
                continue
            if source_type == "issue_comment" and not _is_actionable_body(body):
                continue
            if VISIONPR_MARKER in body or not _is_trusted_author(str(comment.get("author") or ""), str(comment.get("author_association") or "")) or _is_service_account(str(comment.get("author") or ""), str(comment.get("author_type") or "")):
                continue
            feedback.append(
                {
                    "source_type": source_type,
                    "github_id": int(github_id),
                    "author": comment.get("author"),
                    "body": body,
                    "path": comment.get("path") if source_type == "inline_comment" else None,
                    "line": comment.get("line") if source_type == "inline_comment" else None,
                    "created_at": comment.get("created_at"),
                    "url": comment.get("url"),
                }
            )
    return feedback


def collect_new_engineer_feedback(
    pr_number: int,
    processed_ids: dict[str, Iterable[int]],
    repository_name: str | None = None,
) -> list[dict[str, Any]]:
    snapshot = fetch_pull_request_state(pr_number, repository_name)
    return _collect_feedback_from_snapshot(snapshot, processed_ids)


def create_redo_request(pipeline_context: dict, feedback: list[dict], iteration: int) -> dict[str, Any]:
    return {
        "run_id": pipeline_context.get("run_id"),
        "pr_number": pipeline_context.get("pr_number"),
        "review_iteration": iteration,
        "original_requirement": pipeline_context.get("requirement_summary", ""),
        "previous_changes": list(pipeline_context.get("changed_files") or []),
        "engineer_feedback": feedback,
        "target_files": list(pipeline_context.get("target_files") or pipeline_context.get("changed_files") or []),
        "repo_path": pipeline_context.get("repo_path"),
        "branch_name": pipeline_context.get("head_branch"),
        "constraints": [
            "Address every actionable engineer comment",
            "Do not modify unrelated files",
            "Preserve existing behavior not mentioned in feedback",
            "Run the project build and tests after editing",
            "Do not push unless validation succeeds",
        ],
    }


def execute_feedback_iteration(redo_request: dict) -> dict[str, Any]:
    try:
        module = importlib.import_module("src.crew_engine")
        adapter = getattr(module, "run_feedback_iteration")
    except (ImportError, AttributeError) as exc:
        raise VisionPRError(
            "CREWAI_INTEGRATION_UNAVAILABLE",
            "Phase 3 correction adapter is unavailable.",
            operation="execute_feedback_iteration",
        ) from exc
    try:
        result = adapter(redo_request)
    except Exception as exc:
        raise VisionPRError(
            "CREWAI_CORRECTION_FAILED",
            "Phase 3 correction workflow failed.",
            operation="execute_feedback_iteration",
            details={"reason": sanitize_text(exc)},
        ) from exc
    if not isinstance(result, dict):
        raise VisionPRError("INVALID_CORRECTION_RESULT", "Phase 3 correction result must be a dictionary.", operation="validate_correction")
    return result


def _validate_correction_result(
    result: dict[str, Any],
    feedback: list[dict[str, Any]],
    iteration: int,
    repo_path: Path,
    approved_files: list[str],
    previous_fingerprints: Iterable[str],
) -> tuple[list[str], str]:
    errors = result.get("errors")
    validation = result.get("validation")
    if result.get("status") != "APPROVED_FOR_HUMAN_REVIEW":
        raise VisionPRError("CORRECTION_NOT_APPROVED", "Phase 3 did not approve the correction for human review.", operation="validate_correction")
    if result.get("iteration") != iteration:
        raise VisionPRError("ITERATION_MISMATCH", "Phase 3 returned an unexpected correction iteration.", operation="validate_correction")
    if not isinstance(result.get("change_summary"), str) or not result["change_summary"].strip():
        raise VisionPRError("INVALID_CORRECTION_RESULT", "Phase 3 correction summary is missing.", operation="validate_correction")
    if not isinstance(errors, list) or errors:
        raise VisionPRError("CORRECTION_ERRORS", "Phase 3 reported correction errors.", operation="validate_correction")
    if not isinstance(validation, dict):
        raise VisionPRError("INVALID_VALIDATION_RESULT", "Phase 3 validation result is missing.", operation="validate_correction")
    build_status = str((validation.get("build") or {}).get("status", "")).lower()
    test_status = str((validation.get("tests") or {}).get("status", "")).upper()
    if build_status != "success":
        raise VisionPRError("BUILD_FAILED", "Corrected code did not pass the build.", operation="validate_correction")
    if test_status not in {"SUCCESS", "NOT_AVAILABLE"}:
        raise VisionPRError("TEST_FAILED", "Corrected code did not pass tests.", operation="validate_correction")
    changed_files = validate_changed_files(repo_path, result.get("changed_files") or [])
    if not set(changed_files) <= set(approved_files):
        raise VisionPRError("UNAPPROVED_CHANGED_FILES", "Phase 3 modified files outside the approved set.", operation="validate_correction")
    fingerprint = str(result.get("patch_fingerprint") or "")
    if not fingerprint:
        raise VisionPRError("MISSING_PATCH_FINGERPRINT", "Phase 3 did not provide a patch fingerprint.", operation="validate_correction")
    if fingerprint in set(previous_fingerprints):
        raise VisionPRError("REPEATED_PATCH", "Phase 3 produced a patch that was already submitted.", operation="validate_correction")
    resolutions = result.get("feedback_resolution")
    if not isinstance(resolutions, list):
        raise VisionPRError("INVALID_FEEDBACK_RESOLUTION", "Phase 3 did not provide feedback resolutions.", operation="validate_correction")
    for feedback_item in feedback:
        github_id = int(feedback_item["github_id"])
        matches = [
            item
            for item in resolutions
            if isinstance(item, dict)
            and item.get("github_id", item.get("feedback_id")) is not None
            and int(item.get("github_id", item.get("feedback_id"))) == github_id
        ]
        if len(matches) != 1:
            raise VisionPRError("UNRESOLVED_FEEDBACK", "Each feedback item requires exactly one resolution.", operation="validate_correction")
        resolution = matches[0]
        required_file = feedback_item.get("path")
        if (
            resolution.get("status") != "RESOLVED"
            or not str(resolution.get("resolution") or "").strip()
            or not str(resolution.get("verification") or "").strip()
            or (required_file and resolution.get("file", resolution.get("path")) != required_file)
        ):
            raise VisionPRError("UNRESOLVED_FEEDBACK", "Every feedback item requires a verified RESOLVED result.", operation="validate_correction")
    if len(resolutions) != len(feedback):
        raise VisionPRError("INVALID_FEEDBACK_RESOLUTION", "Feedback resolutions contain unexpected entries.", operation="validate_correction")
    ensure_only_intended_worktree_changes(repo_path, changed_files)
    worktree_fingerprint = compute_patch_fingerprint(repo_path, changed_files)
    if worktree_fingerprint != fingerprint:
        raise VisionPRError("PATCH_FINGERPRINT_MISMATCH", "Phase 3 patch fingerprint does not match the corrected files.", operation="validate_correction")
    return changed_files, fingerprint


def _resolution_table(items: list[dict[str, Any]]) -> str:
    rows = ["| Feedback item | File | Resolution | Verification | Status |", "|---|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {id} | {file} | {resolution} | {verification} | {status} |".format(
                id=item.get("github_id", item.get("feedback_id", "")),
                file=str(item.get("file") or item.get("path") or "-").replace("|", "\\|"),
                resolution=str(item.get("resolution") or "Addressed").replace("|", "\\|"),
                verification=str(item.get("verification") or "Validated by Phase 3").replace("|", "\\|"),
                status=item.get("status", ""),
            )
        )
    return "\n".join(rows)


def publish_iteration_update(pr_number: int, iteration_result: dict, repository_name: str | None = None) -> dict[str, Any]:
    iteration = int(iteration_result["iteration"])
    marker = f"VisionPR Engineer Review Summary - Iteration {iteration}"
    validation = iteration_result.get("validation") or {}
    body = f"""# {marker}

## Feedback Received
{chr(10).join(f"- {item.get('body', '')}" for item in iteration_result.get('feedback', [])) or 'No feedback text available.'}

## Changes Made
{iteration_result.get('change_summary', 'No summary provided.')}

## Feedback Resolution
{_resolution_table(iteration_result.get('feedback_resolution') or [])}

## Files Changed
{chr(10).join(f"- `{item}`" for item in iteration_result.get('changed_files', []))}

## Build and Test Results
- Build: **{(validation.get('build') or {}).get('status', 'unknown')}**
- Tests: **{(validation.get('tests') or {}).get('status', 'unknown')}**

## Commit Information
`{iteration_result.get('commit_sha')}`

## Remaining Questions or Risks
None reported by Phase 3.

## Review Required
A new human review is required. Previous approvals may need to be renewed. VisionPR will not merge automatically.
"""
    return post_engineer_summary(pr_number, sanitize_text(body), repository_name, marker)


def save_pr_state_atomic(state: dict) -> Path:
    return save_state_atomic(state)


def _transition(state: dict[str, Any], status: str) -> None:
    previous = state.get("status")
    if previous != status:
        state.setdefault("transition_history", []).append({"from": previous, "to": status, "at": utc_now()})
        LOGGER.info(
            "VisionPR run=%s pr=%s iteration=%s transition=%s->%s",
            state.get("run_id"),
            state.get("pr_number"),
            state.get("review_iteration"),
            previous,
            status,
        )
    state["status"] = status
    state["updated_at"] = utc_now()


def _error_result(state: dict[str, Any], error: VisionPRError) -> dict[str, Any]:
    _transition(state, "ERROR")
    state["error"] = error.to_dict()
    try:
        save_pr_state_atomic(state)
    except VisionPRError as state_error:
        state["error"] = state_error.to_dict()
    return state


def _refresh_persisted_state(state: dict[str, Any]) -> dict[str, Any]:
    persisted = load_pr_state(str(state.get("run_id")))
    if not persisted:
        return state
    # Persisted control fields win; caller context fills fields absent from disk.
    merged = dict(state)
    merged.update(persisted)
    return merged


def _stop_requested(state: dict[str, Any]) -> bool:
    persisted = load_pr_state(str(state.get("run_id")))
    requested = bool(state.get("stop_requested") or (persisted or {}).get("stop_requested"))
    if requested:
        state["stop_requested"] = True
    return requested


def _validation_summary(validation: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("build", "tests"):
        source = validation.get(key) or {}
        summary[key] = {
            field: source[field]
            for field in ("status", "command", "exit_code")
            if field in source
        }
    return summary


def _processed_map(state: dict[str, Any]) -> dict[str, list[int]]:
    return {
        "review": list(state.get("processed_review_ids") or []),
        "issue_comment": list(state.get("processed_issue_comment_ids") or []),
        "inline_comment": list(state.get("processed_inline_comment_ids") or []),
    }


def _mark_feedback_processed(state: dict[str, Any], feedback: list[dict[str, Any]]) -> None:
    mapping = {
        "review": "processed_review_ids",
        "issue_comment": "processed_issue_comment_ids",
        "inline_comment": "processed_inline_comment_ids",
    }
    for item in feedback:
        key = mapping[item["source_type"]]
        values = state.setdefault(key, [])
        if item["github_id"] not in values:
            values.append(item["github_id"])


def _prepare_branch(state: dict[str, Any], *, allow_local_ahead: bool = False) -> Path:
    repo_path = Path(str(state["repo_path"])).resolve()
    branch = str(state["head_branch"])
    remote = str(state.get("remote_name") or os.getenv("GITHUB_REMOTE_NAME", "origin"))
    run_git(repo_path, "fetch", remote, branch)
    current = run_git(repo_path, "branch", "--show-current").stdout
    if current != branch:
        if _status_is_dirty(repo_path):
            raise VisionPRError("UNRELATED_LOCAL_CHANGES", "Cannot switch to the PR branch while local changes exist.", operation="prepare_feedback_branch")
        run_git(repo_path, "switch", branch)
    local_sha = run_git(repo_path, "rev-parse", branch).stdout
    remote_sha = run_git(repo_path, "rev-parse", f"{remote}/{branch}").stdout
    if local_sha != remote_sha:
        local_behind = run_git(repo_path, "merge-base", "--is-ancestor", local_sha, remote_sha, check=False).returncode == 0
        local_ahead = run_git(repo_path, "merge-base", "--is-ancestor", remote_sha, local_sha, check=False).returncode == 0
        if local_behind:
            run_git(repo_path, "merge", "--ff-only", f"{remote}/{branch}")
        elif not (allow_local_ahead and local_ahead):
            raise VisionPRError("GIT_BRANCH_DIVERGED", "Local and remote PR branches have diverged.", operation="prepare_feedback_branch")
    return repo_path


def _status_is_dirty(repo_path: Path) -> bool:
    return bool(run_git(repo_path, "status", "--porcelain=v1", "--untracked-files=all").stdout)


def _complete_pending_publication(state: dict[str, Any]) -> dict[str, Any]:
    pending = state.get("pending_publication")
    if not isinstance(pending, dict):
        return state
    repo_path = _prepare_branch(state, allow_local_ahead=True)
    commit_sha = pending.get("commit_sha")
    if not commit_sha:
        commit_sha = commit_changes(repo_path, pending["commit_message"], pending["commit_body"])
        commit_body = run_git(repo_path, "log", "-1", "--format=%B", commit_sha).stdout
        if f"VisionPR-Run: {state['run_id']}" not in commit_body or f"VisionPR-Iteration: {pending['iteration']}" not in commit_body:
            raise VisionPRError("COMMIT_OWNERSHIP_MISMATCH", "Pending correction commit is not owned by this VisionPR iteration.", operation="publish_correction")
        pending["commit_sha"] = commit_sha
        state["latest_commit_sha"] = commit_sha
        save_pr_state_atomic(state)
    elif run_git(repo_path, "rev-parse", "HEAD").stdout != commit_sha:
        raise VisionPRError("COMMIT_OWNERSHIP_MISMATCH", "The PR branch moved after the correction commit.", operation="publish_correction")

    remote = str(state.get("remote_name") or "origin")
    branch = str(state["head_branch"])
    remote_branch = run_git(repo_path, "ls-remote", "--heads", remote, branch, check=False).stdout
    remote_sha = remote_branch.split(None, 1)[0] if remote_branch else None
    if remote_sha != commit_sha:
        push_branch(repo_path, remote, branch)
    feedback = pending["summary"]["feedback"]
    _mark_feedback_processed(state, feedback)
    state["pending_feedback"] = []
    state["review_iteration"] = pending["iteration"]
    state["latest_patch_fingerprint"] = pending["patch_fingerprint"]
    if pending["patch_fingerprint"] not in state.setdefault("patch_fingerprints", []):
        state["patch_fingerprints"].append(pending["patch_fingerprint"])
    if not any(item.get("iteration") == pending["iteration"] for item in state.setdefault("iteration_history", [])):
        state["iteration_history"].append(
            {
                "iteration": pending["iteration"],
                "commit_sha": commit_sha,
                "patch_fingerprint": pending["patch_fingerprint"],
                "changed_files": pending["changed_files"],
                "feedback_ids": [item["github_id"] for item in feedback],
                "validation": pending["summary"]["validation"],
                "at": utc_now(),
            }
        )
    _transition(state, "UPDATE_PUSHED")
    save_pr_state_atomic(state)
    summary = dict(pending["summary"], commit_sha=commit_sha)
    publish_iteration_update(int(state["pr_number"]), summary, str(state["repository"]))
    state.pop("pending_publication", None)
    _transition(state, "WAITING_FOR_REVIEW")
    save_pr_state_atomic(state)
    return state


def _process_once(state: dict[str, Any], pipeline_context: dict[str, Any]) -> dict[str, Any]:
    state = _refresh_persisted_state(state)
    if _stop_requested(state):
        _transition(state, "STOPPED")
        save_pr_state_atomic(state)
        return state
    if state.get("pending_publication"):
        return _complete_pending_publication(state)
    snapshot = fetch_pull_request_state(int(state["pr_number"]), str(state["repository"]))
    state["latest_commit_sha"] = snapshot["head_sha"]
    if snapshot["merged"]:
        state["merge_commit_sha"] = snapshot.get("merge_commit_sha")
        _transition(state, "MERGED")
        save_pr_state_atomic(state)
        return state
    if snapshot["state"] == "closed":
        _transition(state, "CLOSED")
        save_pr_state_atomic(state)
        return state

    approval_count = _required_approval_count(str(state["repository"]), str(state["base_branch"]))
    review = determine_human_review_status(snapshot["reviews"], snapshot["head_sha"], minimum_approvals=approval_count)
    new_feedback = _collect_feedback_from_snapshot(snapshot, _processed_map(state))
    pending = list(state.get("pending_feedback") or [])
    known = {_feedback_key(item) for item in pending}
    pending.extend(item for item in new_feedback if _feedback_key(item) not in known)
    state["pending_feedback"] = pending
    if pending or review["status"] == "CHANGES_REQUESTED":
        _transition(state, "CHANGES_REQUESTED")
        save_pr_state_atomic(state)
        if not pending:
            return state
    elif review["status"] == "APPROVED":
        state["approved_by"] = review["approved_by"]
        _transition(state, "APPROVED")
        marker = "VisionPR human review gate passed"
        post_engineer_summary(
            int(state["pr_number"]),
            f"{marker}. Approved by: {', '.join(review['approved_by'])}. VisionPR will not merge automatically.",
            str(state["repository"]),
            marker,
        )
        save_pr_state_atomic(state)
        return state
    else:
        _transition(state, "WAITING_FOR_REVIEW")
        save_pr_state_atomic(state)
        return state
    if _stop_requested(state):
        _transition(state, "STOPPED")
        save_pr_state_atomic(state)
        return state

    repo_path = _prepare_branch(state)
    if _status_is_dirty(repo_path):
        raise VisionPRError("UNRELATED_LOCAL_CHANGES", "The PR branch has local changes before Phase 3 correction starts.", operation="prepare_feedback_branch")
    iteration = int(state.get("review_iteration", 1)) + 1
    context = dict(pipeline_context)
    context.update({"pr_number": state["pr_number"], "repo_path": str(repo_path), "head_branch": state["head_branch"]})
    redo_request = create_redo_request(context, pending, iteration)
    _transition(state, "APPLYING_FEEDBACK")
    save_pr_state_atomic(state)
    correction = execute_feedback_iteration(redo_request)
    if _stop_requested(state):
        _transition(state, "STOPPED")
        save_pr_state_atomic(state)
        return state
    _transition(state, "VALIDATING")
    approved_files = validate_changed_files(repo_path, pipeline_context.get("target_files") or pipeline_context.get("changed_files") or state.get("changed_files") or [])
    changed_files, fingerprint = _validate_correction_result(
        correction,
        pending,
        iteration,
        repo_path,
        approved_files,
        state.get("patch_fingerprints") or [],
    )
    staged = stage_intended_files(repo_path, changed_files)
    if not staged:
        raise VisionPRError("NO_CHANGES", "Phase 3 correction produced no meaningful Git diff.", operation="publish_correction")
    staged_fingerprint = compute_patch_fingerprint(repo_path, staged, staged=True)
    if staged_fingerprint != fingerprint:
        raise VisionPRError("PATCH_FINGERPRINT_MISMATCH", "Staged correction does not match the Phase 3 fingerprint.", operation="publish_correction")
    if _stop_requested(state):
        _transition(state, "STOPPED")
        save_pr_state_atomic(state)
        return state
    validation = correction["validation"]
    commit_body = "\n".join(
        (
            f"VisionPR-Run: {state['run_id']}",
            f"Pull Request: #{state['pr_number']}",
            f"VisionPR-Iteration: {iteration}",
            f"Feedback addressed: {len(pending)} item(s)",
            f"Build: {(validation.get('build') or {}).get('status')}",
            f"Tests: {(validation.get('tests') or {}).get('status')}",
        )
    )
    state["pending_publication"] = {
        "iteration": iteration,
        "patch_fingerprint": fingerprint,
        "changed_files": staged,
        "commit_message": f"fix(visionpr): address PR review feedback iteration {iteration}",
        "commit_body": commit_body,
        "commit_sha": None,
        "summary": {
            "iteration": iteration,
            "change_summary": correction["change_summary"],
            "validation": _validation_summary(validation),
            "feedback_resolution": correction["feedback_resolution"],
            "changed_files": staged,
            "feedback": pending,
        },
    }
    _transition(state, "READY_TO_PUBLISH")
    save_pr_state_atomic(state)
    return _complete_pending_publication(state)


def run_human_review_gate(
    pr_state: dict,
    pipeline_context: dict,
    blocking: bool = False,
) -> dict[str, Any]:
    if not isinstance(pr_state, dict) or not isinstance(pipeline_context, dict):
        error = VisionPRError("INVALID_REVIEW_INPUT", "PR state and pipeline context must be dictionaries.", operation="run_human_review_gate")
        return {"status": "ERROR", "error": error.to_dict()}
    state = dict(pr_state)
    required = ("run_id", "repository", "repo_path", "base_branch", "head_branch", "pr_number")
    missing = [key for key in required if state.get(key) in (None, "")]
    if missing:
        error = VisionPRError("INVALID_REVIEW_INPUT", "PR state is missing required fields.", operation="run_human_review_gate", details={"fields": missing})
        state.update({"status": "ERROR", "error": error.to_dict()})
        return state
    try:
        poll_interval = max(1, int(os.getenv("PR_POLL_INTERVAL_SECONDS", "30")))
    except ValueError:
        poll_interval = 30
    while True:
        try:
            state = _process_once(state, pipeline_context)
        except VisionPRError as exc:
            return _error_result(_refresh_persisted_state(state), exc)
        except Exception as exc:  # Keep pipeline boundaries structured.
            error = VisionPRError(
                "UNEXPECTED_REVIEW_ERROR",
                "An unexpected error occurred in the human review gate.",
                operation="run_human_review_gate",
                details={"reason": sanitize_text(exc)},
            )
            return _error_result(state, error)
        if not blocking or state.get("status") in TERMINAL_STATUSES:
            return state
        time.sleep(poll_interval)
