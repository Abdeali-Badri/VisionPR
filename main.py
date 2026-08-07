"""VisionPR orchestration entry points."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn meeting evidence into reviewed repository changes.")
    parser.add_argument("--repository", required=True, help="Public GitHub repository URL or owner/repository")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--media", help="Local recording path or YouTube URL")
    source.add_argument("--intelligence", help="Existing video_intelligence.json path")
    parser.add_argument("--run-id", default=f"visionpr-{uuid4().hex[:10]}")
    parser.add_argument("--build-command", action="append", default=[])
    parser.add_argument("--constraint", action="append", default=[])
    parser.add_argument("--local-only", action="store_true", help="Edit and validate locally without publishing PRs")
    parser.add_argument("--blocking-review", action="store_true", help="Poll GitHub until each PR is approved or changed")
    args = parser.parse_args()

    from src.workflow import run_intelligence_workflow, run_meeting_workflow

    options = {
        "run_id": args.run_id,
        "build_commands": args.build_command,
        "constraints": args.constraint,
        "publish": not args.local_only,
        "blocking_review": args.blocking_review,
    }
    if args.intelligence:
        payload = json.loads(Path(args.intelligence).read_text(encoding="utf-8"))
        report = run_intelligence_workflow(args.repository, payload, **options)
    else:
        report = run_meeting_workflow(args.media, args.repository, **options)
    print(json.dumps({"status": report["status"], "report_paths": report["report_paths"]}, indent=2))


if __name__ == "__main__":
    main()
