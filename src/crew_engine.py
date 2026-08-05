"""VisionPR agentic workflow engine.

This module owns the Architect -> Coder -> Reviewer loop for code patches. The
default implementation is deterministic and dependency-light so the repository
can run in hackathon checkers even before CrewAI credentials are configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from src.prompts import COMMIT_REMINDER_TEMPLATE
from src.schemas import (
    AgenticInput,
    AgentWorkflowResult,
    ArchitectPlan,
    CoderResult,
    ReviewerResult,
    RevisionRequest,
)
from src.tools import run_build_plan


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


class ArchitectAgent(Protocol):
    def create_plan(self, agentic_input: AgenticInput) -> ArchitectPlan:
        """Create an implementation plan without editing files."""


class CoderAgent(Protocol):
    def implement_plan(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        revision_request: RevisionRequest | None = None,
    ) -> CoderResult:
        """Apply the requested code change and summarize the patch."""


class ReviewerAgent(Protocol):
    def review_patch(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        coder_result: CoderResult,
    ) -> ReviewerResult:
        """Approve the patch or request focused revisions."""


class HeuristicArchitectAgent:
    """Small local Architect Agent used when CrewAI is not configured."""

    def create_plan(self, agentic_input: AgenticInput) -> ArchitectPlan:
        repository_files = agentic_input.repository_context.get("relevant_files") or []
        target_files = [str(item.get("path")) for item in repository_files if item.get("path")]
        if not target_files:
            target_files = ["UNKNOWN_TARGET_FILE"]

        files_to_avoid = []
        for constraint in agentic_input.constraints:
            lowered = constraint.lower()
            if "do not modify" in lowered:
                files_to_avoid.append(constraint)

        return ArchitectPlan(
            suspected_cause=(
                "The reported behavior likely lives in the selected repository files "
                "that handle the visible UI flow and related data update path."
            ),
            target_files=target_files,
            files_to_avoid=files_to_avoid,
            required_changes=[
                f"Trace the reported issue: {agentic_input.issue_summary}",
                "Update only the narrow code path responsible for the failing behavior.",
                "Preserve existing naming, formatting, and component/API boundaries.",
            ],
            implementation_steps=[
                "Read the selected target files.",
                "Locate the handler or function connected to the reported UI behavior.",
                "Make the smallest code change that satisfies the issue context.",
                "Run the requested build/test commands.",
            ],
            test_plan=agentic_input.build_commands or ["Run the project's most relevant local test/build command."],
            risk_notes=[
                "Do not touch unrelated files for cleanup or style-only changes.",
                "Escalate to human attention if the selected repository context is insufficient.",
            ],
        )


class PlaceholderCoderAgent:
    """Coder placeholder until the real CrewAI file-editing agent is connected."""

    def implement_plan(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        revision_request: RevisionRequest | None = None,
    ) -> CoderResult:
        attempt_note = (
            f"Revision attempt {revision_request.revision_attempt_number} prepared."
            if revision_request
            else "Initial implementation task prepared."
        )
        return CoderResult(
            modified_files=[],
            change_summary=(
                "No source files were modified by the placeholder coder. "
                "Connect CrewAI and Member 3's read/write tools to enable patch generation."
            ),
            patch_notes=[attempt_note, "Architect target files: " + ", ".join(plan.target_files)],
            assumptions=[
                "This local fallback avoids pretending to patch files without an LLM-backed coder.",
                "The workflow contract and review loop can still be tested end to end.",
            ],
            build_attempted=False,
            build_result=SKIPPED_BUILD_RESULT,
        )


class RuleBasedReviewerAgent:
    """Reviewer Agent that enforces plan boundaries and build status."""

    def review_patch(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        coder_result: CoderResult,
    ) -> ReviewerResult:
        issues = []
        unrelated_files = sorted(set(coder_result.modified_files) - set(plan.target_files))
        missing_expected_change = not coder_result.modified_files
        build_status = str(coder_result.build_result.get("status") or "").lower()

        if missing_expected_change:
            issues.append("Coder did not modify any files.")
        if unrelated_files:
            issues.append("Coder modified files outside the Architect plan: " + ", ".join(unrelated_files))
        if coder_result.build_attempted and build_status not in {"success", "skipped"}:
            issues.append("Build or tests failed.")

        approved = not issues
        return ReviewerResult(
            approved=approved,
            verdict="APPROVED" if approved else "NEEDS_REVISION",
            issues_found=issues,
            plan_followed=not unrelated_files,
            unrelated_changes_detected=bool(unrelated_files),
            syntax_or_logic_risks=[] if approved else ["Patch needs another coder pass before PR publishing."],
            required_revisions=issues,
            next_action="send_to_pr_publisher" if approved else "revise_patch",
        )


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
    run_builds: bool = True,
) -> dict[str, Any]:
    """Run the three-agent workflow and return a Phase 4-ready result shape."""
    if isinstance(agentic_input, dict):
        agentic_input = AgenticInput.from_dict(agentic_input)

    architect_agent = architect or HeuristicArchitectAgent()
    coder_agent = coder or PlaceholderCoderAgent()
    reviewer_agent = reviewer or RuleBasedReviewerAgent()

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

        reviewer_result = reviewer_agent.review_patch(agentic_input, plan, coder_result)
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
    )


def run_agentic_workflow_from_file(path: str | Path, *, repo_path: str | Path = ".") -> dict[str, Any]:
    """Convenience entry point for local demos and pipeline orchestration."""
    return run_agentic_workflow(load_agentic_input(path), repo_path=repo_path)
