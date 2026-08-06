"""VisionPR agentic workflow engine.

This module owns the Architect -> Coder -> Reviewer loop for code patches. The
default implementation is deterministic and dependency-light so the repository
can run in hackathon checkers even before CrewAI credentials are configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agent_interfaces import ArchitectAgent, CoderAgent, ReviewerAgent
from src.prompts import COMMIT_REMINDER_TEMPLATE
from src.runtime_config import AgentEngine, RuntimeConfig
from src.schemas import (
    AgenticInput,
    AgentWorkflowResult,
    ArchitectPlan,
    CoderResult,
    ReviewerResult,
    RevisionRequest,
)
from src.tools import run_build_plan
from src.offline_agents import (
    HeuristicArchitectAgent,
    OfflineArchitectAgent,
    OfflineCoderAgent,
    OfflineReviewerAgent,
    PlaceholderCoderAgent,
    RuleBasedReviewerAgent,
)


SKIPPED_BUILD_RESULT = {
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


def load_agentic_input(path: str | Path) -> AgenticInput:
    """Load the human-readable agent input JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return AgenticInput.from_dict(payload)


def build_revision_request(
    plan: ArchitectPlan,
    reviewer_result: ReviewerResult,
    coder_result: CoderResult,
    attempt_number: int,
) -> RevisionRequest:
    build_logs = ""
    build_result = coder_result.build_result or {}
    if build_result:
        build_logs = json.dumps(build_result, indent=2)
    return RevisionRequest(
        original_plan=plan,
        reviewer_feedback=reviewer_result.required_revisions or reviewer_result.issues_found,
        failed_build_logs=build_logs,
        previous_modified_files=coder_result.modified_files,
        revision_attempt_number=attempt_number,
    )


def _build_result(coder_result: CoderResult | None) -> dict[str, Any]:
    if coder_result and coder_result.build_result:
        return coder_result.build_result
    return SKIPPED_BUILD_RESULT


def _build_ready(build_result: dict[str, Any]) -> bool:
    return str(build_result.get("status") or "").lower() in {"success", "skipped"}


def _pr_title(agentic_input: AgenticInput) -> str:
    summary = " ".join(agentic_input.issue_summary.split())
    if not summary:
        return "Update code from VisionPR agent workflow"
    return summary[:72].rstrip(" .,")


def _pr_summary(agentic_input: AgenticInput, changed_files: list[str]) -> str:
    files = ", ".join(changed_files) if changed_files else "No files changed"
    return (
        f"VisionPR processed the reported issue: {agentic_input.issue_summary} "
        f"Changed files: {files}."
    )


def _ready_for_pr(
    status: str,
    reviewer_result: ReviewerResult,
    coder_result: CoderResult,
    build_result: dict[str, Any],
) -> bool:
    return (
        status == "APPROVED_FOR_PR"
        and reviewer_result.approved
        and bool(coder_result.modified_files)
        and _build_ready(build_result)
    )


def _runtime_default() -> RuntimeConfig:
    return RuntimeConfig(
        engine=AgentEngine.HEURISTIC,
        llm=None,
        reason="Runtime metadata defaulted for injected test agents.",
        requested_mode="injected",
        crewai_installed=True,
    )


def _enforce_deterministic_safety(
    plan: ArchitectPlan,
    coder_result: CoderResult,
    reviewer_result: ReviewerResult,
) -> ReviewerResult:
    issues = list(reviewer_result.issues_found)
    unrelated = sorted(set(coder_result.modified_files) - set(plan.target_files))
    if not coder_result.modified_files:
        issues.append("Deterministic safety gate rejected an empty patch.")
    if unrelated:
        issues.append("Deterministic safety gate rejected unrelated files: " + ", ".join(unrelated))
    build_status = str((coder_result.build_result or {}).get("status") or "").lower()
    if coder_result.build_attempted and build_status not in {"success", "skipped"}:
        issues.append("Deterministic safety gate rejected failed or timed-out build output.")
    if issues:
        deduped = list(dict.fromkeys(issues))
        return ReviewerResult(
            approved=False,
            verdict="NEEDS_REVISION",
            issues_found=deduped,
            plan_followed=not unrelated and reviewer_result.plan_followed,
            unrelated_changes_detected=bool(unrelated) or reviewer_result.unrelated_changes_detected,
            syntax_or_logic_risks=list(dict.fromkeys(reviewer_result.syntax_or_logic_risks + ["Deterministic safety gate blocked approval."])),
            required_revisions=deduped,
            next_action="revise_patch",
        )
    return reviewer_result


def _workflow_result(
    *,
    status: str,
    agentic_input: AgenticInput,
    repo_path: str | Path,
    plan: ArchitectPlan,
    coder_result: CoderResult,
    reviewer_result: ReviewerResult,
    review_attempts: int,
    milestone: str,
    runtime: RuntimeConfig,
) -> dict[str, Any]:
    build_result = _build_result(coder_result)
    ready_for_pr = _ready_for_pr(status, reviewer_result, coder_result, build_result)
    return AgentWorkflowResult(
        status=status,
        run_id=agentic_input.run_id,
        ready_for_pr=ready_for_pr,
        repo_path=str(Path(repo_path).resolve()),
        build_result=build_result,
        pr_title=_pr_title(agentic_input),
        pr_summary=_pr_summary(agentic_input, coder_result.modified_files),
        execution_mode=runtime.mode.value,
        llm_used=runtime.llm_used,
        crewai_installed=runtime.crewai_installed,
        provider=runtime.provider,
        model=runtime.model,
        runtime_reason=runtime.reason,
        demo_run=agentic_input.run_id == "mock-agentic-run-001",
        architect_plan=plan.to_dict(),
        coder_result=coder_result.to_dict(),
        reviewer_result=reviewer_result.to_dict(),
        changed_files=coder_result.modified_files,
        review_attempts=review_attempts,
        commit_reminder=COMMIT_REMINDER_TEMPLATE.format(milestone=milestone),
    ).to_dict()


def run_agentic_workflow(
    agentic_input: AgenticInput | dict[str, Any],
    *,
    repo_path: str | Path = ".",
    architect: ArchitectAgent | None = None,
    coder: CoderAgent | None = None,
    reviewer: ReviewerAgent | None = None,
    runtime: RuntimeConfig | None = None,
    run_builds: bool = True,
) -> dict[str, Any]:
    """Run the three-agent workflow and return a Phase 4-ready result shape."""
    if isinstance(agentic_input, dict):
        agentic_input = AgenticInput.from_dict(agentic_input)

    if architect is None or coder is None or reviewer is None or runtime is None:
        from src.agent_factory import create_agent_bundle

        bundle = create_agent_bundle(runtime=runtime, repo_path=repo_path)
        architect_agent = architect or bundle.architect
        coder_agent = coder or bundle.coder
        reviewer_agent = reviewer or bundle.reviewer
        runtime = bundle.runtime
    else:
        architect_agent = architect
        coder_agent = coder
        reviewer_agent = reviewer
        runtime = runtime or _runtime_default()

    plan = architect_agent.create_plan(agentic_input)
    coder_result: CoderResult | None = None
    reviewer_result: ReviewerResult | None = None
    revision_request: RevisionRequest | None = None

    max_attempts = max(1, agentic_input.max_review_attempts)
    for attempt in range(1, max_attempts + 1):
        coder_result = coder_agent.implement_plan(agentic_input, plan, revision_request)
        if run_builds and coder_result.modified_files and agentic_input.build_commands:
            build_result = run_build_plan(repo_path, agentic_input.build_commands)
            coder_result = CoderResult(
                modified_files=coder_result.modified_files,
                change_summary=coder_result.change_summary,
                patch_notes=coder_result.patch_notes,
                assumptions=coder_result.assumptions,
                build_attempted=True,
                build_result=build_result,
            )

        reviewer_result = _enforce_deterministic_safety(
            plan,
            coder_result,
            reviewer_agent.review_patch(agentic_input, plan, coder_result),
        )
        if reviewer_result.approved:
            return _workflow_result(
                status="APPROVED_FOR_PR",
                agentic_input=agentic_input,
                repo_path=repo_path,
                plan=plan,
                coder_result=coder_result,
                reviewer_result=reviewer_result,
                review_attempts=attempt,
                milestone="agentic patch approved for PR publishing",
                runtime=runtime,
            )

        revision_request = build_revision_request(plan, reviewer_result, coder_result, attempt + 1)

    return _workflow_result(
        status="REVIEW_FAILED",
        agentic_input=agentic_input,
        repo_path=repo_path,
        plan=plan,
        coder_result=coder_result or CoderResult([], "Coder did not run.", build_result=SKIPPED_BUILD_RESULT),
        reviewer_result=reviewer_result or ReviewerResult(False, "NEEDS_REVISION"),
        review_attempts=max_attempts,
        milestone="agent workflow contracts and retry behavior",
        runtime=runtime,
    )


def run_agentic_workflow_from_file(path: str | Path, *, repo_path: str | Path = ".") -> dict[str, Any]:
    """Convenience entry point for local demos and pipeline orchestration."""
    return run_agentic_workflow(load_agentic_input(path), repo_path=repo_path)
