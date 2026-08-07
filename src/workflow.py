"""End-to-end orchestration from media intelligence to per-task PRs and reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.github_publisher import VisionPRError, sanitize_text
from src.media_input import resolve_media_input
from src.pipeline import run_repository_task


TaskRunner = Callable[..., dict[str, Any]]
ProgressCallback = Callable[[str, str], None]


def tasks_from_video_intelligence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert timestamped key points and frames into repository task inputs."""
    key_points = list(payload.get("key_points") or [])
    segments = list((payload.get("transcript") or {}).get("segments") or [])
    visual_by_index = {
        int(item.get("key_point_index")): item
        for item in payload.get("visual_context") or []
        if isinstance(item, dict) and item.get("key_point_index") is not None
    }
    tasks: list[dict[str, Any]] = []
    for index, point in enumerate(key_points, start=1):
        if not isinstance(point, dict):
            continue
        timestamp = float(point.get("timestamp") or 0)
        nearby = [
            segment
            for segment in segments
            if isinstance(segment, dict)
            and float(segment.get("end") or segment.get("start") or 0) >= timestamp - 5
            and float(segment.get("start") or 0) <= timestamp + 5
        ]
        visual = visual_by_index.get(index, {})
        analysis = dict(visual.get("analysis") or {})
        screenshots = [
            {
                "timestamp": timestamp,
                "path": str(path),
                "description": analysis.get("summary") or str(point.get("point") or "Recording evidence"),
            }
            for path in visual.get("frames") or []
        ]
        summary = str(point.get("point") or point.get("english_quote") or point.get("original_quote") or "").strip()
        if not summary:
            continue
        tasks.append(
            {
                "task_number": index,
                "issue_summary": summary,
                "meeting_issue_context": {
                    "timestamp": timestamp,
                    "original_quote": point.get("original_quote"),
                    "english_quote": point.get("english_quote"),
                    "transcript_segments": nearby,
                    "screenshot_context": screenshots,
                    "visual_analysis": analysis,
                },
            }
        )
    return tasks


def _safe_run_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._") or "visionpr-run"


def write_workflow_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    destination = Path(output_dir).resolve() / _safe_run_id(str(report["run_id"]))
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "report.json"
    markdown_path = destination / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# VisionPR Report: {report['run_id']}",
        "",
        f"- Repository: `{report['repository_url']}`",
        f"- Status: **{report['status']}**",
        f"- Tasks discovered: {report['task_count']}",
        f"- Tasks processed: {report['processed_count']}",
        "",
        "## Changes",
    ]
    if not report["tasks"]:
        lines.extend(["", "No explicit repository change requests were found in the recording."])
    for item in report["tasks"]:
        lines.extend(
            [
                "",
                f"### Task {item['task_number']}: {item['issue_summary']}",
                f"- Status: {item['status']}",
                f"- Changed files: {', '.join(item.get('changed_files') or []) or 'None'}",
                f"- Pull request: {item.get('pr_url') or 'Not created'}",
                f"- Summary: {item.get('change_summary') or 'No change summary available.'}",
            ]
        )
        if item.get("error"):
            lines.append(f"- Error: {item['error'].get('message') or item['error']}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def run_intelligence_workflow(
    repository_url: str,
    intelligence: dict[str, Any],
    *,
    run_id: str,
    build_commands: list[str] | None = None,
    constraints: list[str] | None = None,
    workspace_dir: str | Path | None = None,
    report_dir: str | Path = "data/reports",
    publish: bool = True,
    blocking_review: bool = False,
    task_runner: TaskRunner = run_repository_task,
) -> dict[str, Any]:
    """Run each extracted change as an isolated repository and PR task."""
    tasks = tasks_from_video_intelligence(intelligence)
    entries: list[dict[str, Any]] = []
    for task in tasks:
        task_run_id = f"{_safe_run_id(run_id)}-task-{task['task_number']:02d}"
        try:
            result = task_runner(
                repository_url,
                run_id=task_run_id,
                issue_summary=task["issue_summary"],
                meeting_issue_context=task["meeting_issue_context"],
                build_commands=list(build_commands or []),
                constraints=list(constraints or []),
                workspace_dir=workspace_dir,
                publish=publish,
                enter_review_gate=publish,
                blocking_review=blocking_review,
            )
            workflow = dict(result.get("agent_workflow_result") or {})
            pr_state = dict(result.get("pr_state") or {})
            entry = {
                **task,
                "run_id": task_run_id,
                "status": result.get("status"),
                "changed_files": list(workflow.get("changed_files") or []),
                "change_summary": (workflow.get("coder_result") or {}).get("change_summary"),
                "pr_number": pr_state.get("pr_number"),
                "pr_url": pr_state.get("pr_url"),
            }
        except VisionPRError as exc:
            entry = {**task, "run_id": task_run_id, "status": "ERROR", "changed_files": [], "error": exc.to_dict()}
        except Exception as exc:
            entry = {
                **task,
                "run_id": task_run_id,
                "status": "ERROR",
                "changed_files": [],
                "error": {
                    "code": "TASK_EXECUTION_FAILED",
                    "message": sanitize_text(str(exc))[:2_000],
                    "operation": "run_repository_task",
                },
            }
        entries.append(entry)
        if entry["status"] in {"ERROR", "REVIEW_FAILED", "BUILD_FAILED"}:
            break

    processed = len(entries)
    successful_statuses = {"PR_OPENED", "WAITING_FOR_REVIEW", "APPROVED_FOR_PR", "UPDATE_PUSHED"}
    if not tasks:
        report_status = "NO_ACTIONABLE_TASKS"
    elif processed == len(tasks) and all(item["status"] in successful_statuses for item in entries):
        report_status = "COMPLETED"
    else:
        report_status = "INCOMPLETE"
    report = {
        "run_id": run_id,
        "repository_url": repository_url,
        "status": report_status,
        "task_count": len(tasks),
        "processed_count": processed,
        "tasks": entries,
    }
    report["report_paths"] = write_workflow_report(report, report_dir)
    return report


def run_meeting_workflow(
    media_source: str | Path,
    repository_url: str,
    *,
    run_id: str,
    extractor: Callable[[Path], str | Path | dict[str, Any]] | None = None,
    media_dir: str | Path = "data/input_videos",
    progress_callback: ProgressCallback | None = None,
    **workflow_options: Any,
) -> dict[str, Any]:
    """Resolve media, extract intelligence through an adapter, then run all tasks."""
    if progress_callback:
        progress_callback("media_started", "Preparing the recording or YouTube source.")
    media_path = resolve_media_input(media_source, media_dir)
    if extractor is None:
        from src.extract_video import process_video

        extractor = process_video
    if progress_callback:
        progress_callback("intelligence_started", "Transcribing audio and analyzing timestamped visual evidence.")
    output = extractor(media_path)
    intelligence = output if isinstance(output, dict) else json.loads(Path(output).read_text(encoding="utf-8"))
    if progress_callback:
        progress_callback("repository_started", "Meeting evidence is ready. Mapping the repository and preparing changes.")
    return run_intelligence_workflow(repository_url, intelligence, run_id=run_id, **workflow_options)
