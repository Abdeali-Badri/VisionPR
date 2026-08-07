from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.database import db, utc_now
from backend.security import token_cipher
from backend.services import add_event, review_detail


FAILED_TASK_STATUSES = {"ERROR", "REVIEW_FAILED", "BUILD_FAILED"}


@dataclass(frozen=True)
class ReportOutcome:
    status: str
    failed_task: dict[str, Any] | None
    error_message: str | None


def task_display_status(task: dict[str, Any]) -> str:
    if task.get("pr_url"):
        return "awaiting_review"
    status = str(task.get("status") or "incomplete").lower()
    return status.replace("-", "_")


def report_outcome(report: dict[str, Any]) -> ReportOutcome:
    tasks = list(report.get("tasks") or [])
    first = tasks[0] if tasks else {}
    failed_task = next(
        (item for item in tasks if str(item.get("status") or "").upper() in FAILED_TASK_STATUSES),
        None,
    )
    if failed_task:
        error = dict(failed_task.get("error") or {})
        message = error.get("message") or failed_task.get("change_summary")
        return ReportOutcome(
            status=str(failed_task.get("status") or "ERROR").upper(),
            failed_task=failed_task,
            error_message=str(message)[:2000] if message else "The generated patch did not pass VisionPR review.",
        )
    if first.get("pr_url"):
        return ReportOutcome("AWAITING_HUMAN_REVIEW", None, None)
    return ReportOutcome(str(report.get("status") or "INCOMPLETE").upper(), None, None)


def run_review(review_id: int) -> None:
    review = review_detail(review_id)
    if not review:
        raise ValueError("Review does not exist.")
    user = db.fetch_one("SELECT * FROM users WHERE id=?", (review.get("user_id"),)) if review.get("user_id") else None
    if user and user.get("encrypted_token"):
        os.environ["GITHUB_TOKEN"] = token_cipher.decrypt(user["encrypted_token"])
    db.execute("UPDATE reviews SET status='PROCESSING',current_step=3,updated_at=? WHERE id=?", (utc_now(), review_id))
    add_event(review_id, "processing", "VisionPR is extracting tasks and mapping the repository.")
    options = review.get("options") or {}
    try:
        from src.workflow import run_intelligence_workflow, run_meeting_workflow

        if review["source_type"] == "intelligence":
            payload = json.loads(Path(review["source_value"]).read_text(encoding="utf-8"))
            report = run_intelligence_workflow(
                review["repository_url"], payload, run_id=review["run_id"],
                build_commands=options.get("build_commands"), constraints=options.get("constraints"), publish=True,
            )
        else:
            def record_progress(event_type: str, message: str) -> None:
                add_event(review_id, event_type, message)

            report = run_meeting_workflow(
                review["source_value"], review["repository_url"], run_id=review["run_id"],
                build_commands=options.get("build_commands"), constraints=options.get("constraints"), publish=True,
                progress_callback=record_progress,
            )
        tasks = report.get("tasks") or []
        first = tasks[0] if tasks else {}
        outcome = report_outcome(report)
        db.execute(
            """UPDATE reviews SET status=?,current_step=4,pr_number=?,pr_url=?,changed_files_json=?,report_json=?,error_message=?,updated_at=? WHERE id=?""",
            (outcome.status, first.get("pr_number"), first.get("pr_url"), json.dumps(first.get("changed_files") or []), json.dumps(report), outcome.error_message, utc_now(), review_id),
        )
        for item in tasks:
            db.execute(
                """INSERT INTO review_tasks(review_id,task_number,title,timestamp,transcript,status,changed_files_json)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(review_id,task_number) DO UPDATE SET status=excluded.status,changed_files_json=excluded.changed_files_json""",
                (review_id, item["task_number"], item["issue_summary"], (item.get("meeting_issue_context") or {}).get("timestamp"), (item.get("meeting_issue_context") or {}).get("english_quote"), task_display_status(item), json.dumps(item.get("changed_files") or [])),
            )
        if first.get("pr_url"):
            add_event(review_id, "pr_opened", "The pull request is ready for your review.", {"pr_url": first.get("pr_url")})
        elif outcome.status == "NO_ACTIONABLE_TASKS":
            add_event(review_id, "no_actionable_tasks", "No explicit repository change requests were found in the recording.")
        elif outcome.failed_task:
            add_event(review_id, "review_failed", "The proposed patch did not pass VisionPR review. No pull request was created.", {"detail": outcome.error_message})
    except Exception as exc:
        db.execute("UPDATE reviews SET status='ERROR',error_message=?,updated_at=? WHERE id=?", (str(exc)[:2000], utc_now(), review_id))
        add_event(review_id, "error", "VisionPR could not complete this review.", {"detail": str(exc)[:500]})
        raise


def run_feedback(review_id: int) -> None:
    review = review_detail(review_id)
    if not review or not review.get("pr_number"):
        raise ValueError("Review does not have an open pull request.")
    user = db.fetch_one("SELECT * FROM users WHERE id=?", (review.get("user_id"),)) if review.get("user_id") else None
    if user and user.get("encrypted_token"):
        os.environ["GITHUB_TOKEN"] = token_cipher.decrypt(user["encrypted_token"])

    # Feedback submitted through the authenticated web UI is an explicit human
    # decision, even when the reviewer is not a collaborator on the upstream repo.
    reviewer = str((user or {}).get("login") or "").strip()
    configured = [item.strip() for item in os.getenv("VISIONPR_AUTHORIZED_REVIEWERS", "").split(",") if item.strip()]
    if reviewer and reviewer.lower() not in {item.lower() for item in configured}:
        configured.append(reviewer)
        os.environ["VISIONPR_AUTHORIZED_REVIEWERS"] = ",".join(configured)

    db.execute("UPDATE reviews SET status='APPLYING_FEEDBACK',updated_at=? WHERE id=?", (utc_now(), review_id))
    add_event(review_id, "feedback_started", "VisionPR is applying your feedback to the existing pull request.")
    try:
        from src.github_publisher import load_pr_state
        from src.hitl_review_gate import run_human_review_gate

        state = load_pr_state(str(review["run_id"]))
        if not state:
            raise ValueError("The saved pull request state could not be found for this review.")
        options = review.get("options") or {}
        task = (review.get("tasks") or [{}])[0]
        changed_files = list(review.get("changed_files") or state.get("changed_files") or [])
        pipeline_context = {
            "run_id": review["run_id"],
            "pr_number": review["pr_number"],
            "requirement_summary": review["title"],
            "changed_files": changed_files,
            "target_files": changed_files,
            "repo_path": state["repo_path"],
            "head_branch": state["head_branch"],
            "build_commands": options.get("build_commands") or [],
            "constraints": options.get("constraints") or [],
            "meeting_issue_context": {
                "timestamp": task.get("timestamp"),
                "english_quote": task.get("transcript"),
            },
        }
        result = run_human_review_gate(state, pipeline_context, blocking=False)
        if result.get("status") == "ERROR":
            detail = ((result.get("error") or {}).get("message") or "The correction pipeline failed.")
            raise RuntimeError(detail)

        status = "AWAITING_HUMAN_REVIEW" if result.get("status") in {"UPDATE_PUSHED", "WAITING_FOR_REVIEW"} else str(result.get("status") or "AWAITING_HUMAN_REVIEW")
        db.execute(
            "UPDATE reviews SET status=?,commit_sha=?,changed_files_json=?,updated_at=? WHERE id=?",
            (status, result.get("latest_commit_sha"), json.dumps(result.get("changed_files") or changed_files), utc_now(), review_id),
        )
        db.execute("UPDATE review_tasks SET status='awaiting_review' WHERE review_id=?", (review_id,))
        add_event(
            review_id,
            "feedback_applied",
            "A new commit was pushed to the same pull request. Human review is required again.",
            {"commit_sha": result.get("latest_commit_sha"), "iteration": result.get("review_iteration")},
        )
    except Exception as exc:
        db.execute("UPDATE reviews SET status='ERROR',error_message=?,updated_at=? WHERE id=?", (str(exc)[:2000], utc_now(), review_id))
        add_event(review_id, "error", "VisionPR could not apply the requested changes.", {"detail": str(exc)[:500]})
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-id", required=True, type=int)
    parser.add_argument("--mode", default="run", choices=["run", "feedback"])
    args = parser.parse_args()
    db.initialize()
    if args.mode == "feedback":
        run_feedback(args.review_id)
    else:
        run_review(args.review_id)


if __name__ == "__main__":
    main()
