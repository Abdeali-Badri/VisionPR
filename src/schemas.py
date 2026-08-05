"""Structured contracts for VisionPR's agentic patch workflow.

The names here avoid internal phase-number jargon so a new reviewer can follow
the data flow without knowing the team split.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


WorkflowStatus = Literal[
    "APPROVED_FOR_PR",
    "NEEDS_REVISION",
    "NEEDS_HUMAN_ATTENTION",
    "BUILD_FAILED",
    "REVIEW_FAILED",
]


@dataclass(frozen=True)
class TranscriptSegment:
    """A timestamped piece of meeting or screen-recording narration."""

    timestamp: str
    text: str


@dataclass(frozen=True)
class ScreenshotContext:
    """A UI frame that gives the agents visual grounding."""

    timestamp: str
    path: str
    description: str


@dataclass(frozen=True)
class RepositoryFileContext:
    """Relevant codebase context selected by the repository mapper."""

    path: str
    summary: str
    symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgenticInput:
    """All context needed to begin the Architect -> Coder -> Reviewer loop."""

    run_id: str
    issue_summary: str
    meeting_issue_context: dict[str, Any]
    repository_context: dict[str, Any]
    build_commands: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    max_review_attempts: int = 3

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgenticInput":
        return cls(
            run_id=str(payload.get("run_id") or "visionpr-local-run"),
            issue_summary=str(payload.get("issue_summary") or ""),
            meeting_issue_context=dict(payload.get("meeting_issue_context") or {}),
            repository_context=dict(payload.get("repository_context") or {}),
            build_commands=list(payload.get("build_commands") or []),
            constraints=list(payload.get("constraints") or []),
            max_review_attempts=int(payload.get("max_review_attempts") or 3),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArchitectPlan:
    """Implementation plan produced before any file edits happen."""

    suspected_cause: str
    target_files: list[str]
    files_to_avoid: list[str]
    required_changes: list[str]
    implementation_steps: list[str]
    test_plan: list[str]
    risk_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArchitectPlan":
        return cls(
            suspected_cause=str(payload.get("suspected_cause") or ""),
            target_files=list(payload.get("target_files") or []),
            files_to_avoid=list(payload.get("files_to_avoid") or []),
            required_changes=list(payload.get("required_changes") or []),
            implementation_steps=list(payload.get("implementation_steps") or []),
            test_plan=list(payload.get("test_plan") or []),
            risk_notes=list(payload.get("risk_notes") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoderResult:
    """Summary of code edits made by the Coder Agent."""

    modified_files: list[str]
    change_summary: str
    patch_notes: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    build_attempted: bool = False
    build_result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CoderResult":
        return cls(
            modified_files=list(payload.get("modified_files") or []),
            change_summary=str(payload.get("change_summary") or ""),
            patch_notes=list(payload.get("patch_notes") or []),
            assumptions=list(payload.get("assumptions") or []),
            build_attempted=bool(payload.get("build_attempted", False)),
            build_result=dict(payload.get("build_result") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewerResult:
    """Reviewer Agent decision after checking the patch and build result."""

    approved: bool
    verdict: Literal["APPROVED", "NEEDS_REVISION"]
    issues_found: list[str] = field(default_factory=list)
    plan_followed: bool = True
    unrelated_changes_detected: bool = False
    syntax_or_logic_risks: list[str] = field(default_factory=list)
    required_revisions: list[str] = field(default_factory=list)
    next_action: str = "send_to_pr_publisher"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewerResult":
        approved = bool(payload.get("approved", False))
        return cls(
            approved=approved,
            verdict="APPROVED" if approved else "NEEDS_REVISION",
            issues_found=list(payload.get("issues_found") or []),
            plan_followed=bool(payload.get("plan_followed", True)),
            unrelated_changes_detected=bool(payload.get("unrelated_changes_detected", False)),
            syntax_or_logic_risks=list(payload.get("syntax_or_logic_risks") or []),
            required_revisions=list(payload.get("required_revisions") or []),
            next_action=str(payload.get("next_action") or ("send_to_pr_publisher" if approved else "revise_patch")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RevisionRequest:
    """Feedback bundle sent back to the Coder Agent after review failure."""

    original_plan: ArchitectPlan
    reviewer_feedback: list[str]
    failed_build_logs: str = ""
    previous_modified_files: list[str] = field(default_factory=list)
    revision_attempt_number: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentWorkflowResult:
    """Final handoff object emitted by the agentic workflow."""

    status: WorkflowStatus
    run_id: str
    ready_for_pr: bool
    repo_path: str
    build_result: dict[str, Any]
    pr_title: str
    pr_summary: str
    architect_plan: dict[str, Any]
    coder_result: dict[str, Any]
    reviewer_result: dict[str, Any]
    changed_files: list[str]
    review_attempts: int
    commit_reminder: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
