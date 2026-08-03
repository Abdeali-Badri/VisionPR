"""VisionPR orchestration entry points."""

from __future__ import annotations

from typing import Any

from src.github_publisher import VisionPRError, publish_pull_request
from src.hitl_review_gate import run_human_review_gate


def run_phase4_and_phase5(
    phase3_result: dict[str, Any],
    *,
    repo_path: str | None = None,
    blocking: bool = False,
) -> dict[str, Any]:
    """Publish a validated Phase 3 result, then enter the human review gate."""
    try:
        phase4_result = publish_pull_request(phase3_result, repo_path=repo_path)
    except VisionPRError as exc:
        return {
            "status": "ERROR",
            "run_id": phase3_result.get("run_id") if isinstance(phase3_result, dict) else None,
            "phase": 4,
            "error": exc.to_dict(),
        }
    phase5_result = run_human_review_gate(
        pr_state=phase4_result,
        pipeline_context=phase3_result,
        blocking=blocking,
    )
    phase5_result.setdefault("phase", 5)
    return phase5_result
