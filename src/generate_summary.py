"""JSON loading helpers for VisionPR pipeline handoff artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_AGENT_RESULT_KEYS = {
    "status",
    "changed_files",
    "coder_result",
    "reviewer_result",
    "review_attempts",
}

STATUS_LABELS = {
    "APPROVED_FOR_PR": "[ready] Approved for PR",
    "NEEDS_REVISION": "[revision] Needs revision",
    "NEEDS_HUMAN_ATTENTION": "[attention] Needs human attention",
    "BUILD_FAILED": "[build-failed] Build failed",
    "REVIEW_FAILED": "[review-failed] Review failed",
}


def load_agent_result(path: str | Path) -> dict[str, Any]:
    """Load and validate Phase 3's AgentWorkflowResult JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Phase 3 AgentWorkflowResult JSON must be an object.")

    missing_keys = sorted(REQUIRED_AGENT_RESULT_KEYS - payload.keys())
    if missing_keys:
        raise ValueError(
            "Phase 3 AgentWorkflowResult JSON is missing required keys: "
            + ", ".join(missing_keys)
        )
    return payload


def load_transcript(path: str | Path) -> Any:
    """Load a transcript JSON artifact from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _markdown_list(values: list[Any], empty: str = "Not provided") -> str:
    items = [f"- `{value}`" for value in values if value]
    return "\n".join(items) if items else empty


def build_header(agent_result: dict) -> str:
    """Build the top-level summary header."""
    status = str(agent_result.get("status") or "NEEDS_HUMAN_ATTENTION")
    label = STATUS_LABELS.get(status, f"[unknown] {status}")
    run_id = agent_result.get("run_id") or "unknown"
    attempts = agent_result.get("review_attempts", "unknown")
    return f"# VisionPR Executive Summary\n\n- Run ID: `{run_id}`\n- Status: **{label}**\n- Review attempts: `{attempts}`"


def build_transcript_section(transcript: list[dict], max_segments: int = 15) -> str:
    """Render timestamped transcript segments."""
    if not transcript:
        return "## Transcript\n\nNo transcript segments were provided."
    visible_segments = transcript[:max(0, max_segments)]
    lines = []
    for segment in visible_segments:
        timestamp = segment.get("timestamp", "unknown")
        text = segment.get("text", "")
        lines.append(f"**[{timestamp}]** {text}")
    omitted = len(transcript) - len(visible_segments)
    if omitted > 0:
        lines.append(f"\n_{omitted} transcript segment(s) omitted._")
    return "## Transcript\n\n" + "\n".join(lines)


def build_plan_section(agent_result: dict) -> str:
    """Render the Architect Agent plan summary."""
    plan = agent_result.get("architect_plan") or {}
    suspected_cause = plan.get("suspected_cause") or "Not provided"
    target_files = list(plan.get("target_files") or [])
    required_changes = list(plan.get("required_changes") or [])
    return (
        "## Implementation Plan\n\n"
        f"**Suspected cause:** {suspected_cause}\n\n"
        "**Target files:**\n"
        f"{_markdown_list(target_files)}\n\n"
        "**Required changes:**\n"
        f"{_markdown_list(required_changes)}"
    )


def build_changes_section(agent_result: dict) -> str:
    """Render changed files and Coder Agent summary."""
    coder_result = agent_result.get("coder_result") or {}
    changed_files = list(agent_result.get("changed_files") or coder_result.get("modified_files") or [])
    change_summary = coder_result.get("change_summary") or "No change summary was provided."
    return (
        "## Changes\n\n"
        "**Changed files:**\n"
        f"{_markdown_list(changed_files)}\n\n"
        f"**Summary:** {change_summary}"
    )


def build_build_log_section(agent_result: dict) -> str:
    """Render build logs from the Coder Agent result."""
    coder_result = agent_result.get("coder_result") or {}
    build_result = coder_result.get("build_result") or {}
    if not coder_result.get("build_attempted", False):
        reason = build_result.get("reason") or "No build was attempted by the Coder Agent."
        return f"## Build Log\n\nBuild skipped: {reason}"
    return "## Build Log\n\n```json\n" + json.dumps(build_result, indent=2) + "\n```"


def build_review_section(agent_result: dict) -> str:
    """Render the Reviewer Agent verdict and findings."""
    reviewer_result = agent_result.get("reviewer_result") or {}
    verdict = reviewer_result.get("verdict") or "unknown"
    issues_found = list(reviewer_result.get("issues_found") or [])
    return (
        "## Review\n\n"
        f"**Verdict:** {verdict}\n\n"
        "**Issues found:**\n"
        f"{_markdown_list(issues_found, empty='None reported')}"
    )
