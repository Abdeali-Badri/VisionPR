"""Shared agent interfaces for VisionPR Phase 3."""

from __future__ import annotations

from typing import Protocol

from src.schemas import AgenticInput, ArchitectPlan, CoderResult, ReviewerResult, RevisionRequest


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
        """Apply or prepare the requested change and summarize the result."""


class ReviewerAgent(Protocol):
    def review_patch(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        coder_result: CoderResult,
    ) -> ReviewerResult:
        """Approve the patch or request focused revisions."""
