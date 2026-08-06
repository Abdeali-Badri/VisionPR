"""Deterministic offline agents for local Phase 3 demonstration."""

from __future__ import annotations

from pathlib import Path

from src.schemas import AgenticInput, ArchitectPlan, CoderResult, ReviewerResult, RevisionRequest
from src.tools import read_file, resolve_repo_path, run_build_plan, write_file


DEMO_RUN_ID = "mock-agentic-run-001"
DEMO_TARGET_FILE = "profile.py"
BUGGY_SNIPPET = "# DEMO_PATCH_PENDING\n"
PATCHED_SNIPPET = "# DEMO_PATCH_APPLIED\n"


class OfflineArchitectAgent:
    """Deterministic planner that uses only supplied structured context."""

    def create_plan(self, agentic_input: AgenticInput) -> ArchitectPlan:
        repository_files = agentic_input.repository_context.get("relevant_files") or []
        target_files = []
        for item in repository_files:
            path = str(item.get("path") or "")
            if path and "UNKNOWN" not in path:
                target_files.append(path)

        files_to_avoid = [
            constraint
            for constraint in agentic_input.constraints
            if "do not modify" in constraint.lower()
        ]
        return ArchitectPlan(
            suspected_cause=(
                "Offline demo mode selected target files from repository_context only; "
                "no LLM reasoning was used."
            ),
            target_files=target_files,
            files_to_avoid=files_to_avoid,
            required_changes=[
                f"Address the reported issue: {agentic_input.issue_summary}",
                "Keep changes limited to the listed repository_context files.",
            ],
            implementation_steps=[
                "Validate target paths with the safe repository tools.",
                "Apply only the controlled bundled demo patch when the run id matches the demo scenario.",
                "Run the configured allow-listed build commands.",
            ],
            test_plan=agentic_input.build_commands,
            risk_notes=[
                "No LLM reasoning was used in offline demo mode.",
                "Offline mode supports only the bundled deterministic demonstration scenario.",
            ],
        )


class OfflineCoderAgent:
    """Deterministic coder for the bundled mock repository scenario."""

    def __init__(self, repo_path: str | Path = ".") -> None:
        self.repo_path = Path(repo_path)

    def implement_plan(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        revision_request: RevisionRequest | None = None,
    ) -> CoderResult:
        if agentic_input.run_id != DEMO_RUN_ID or DEMO_TARGET_FILE not in plan.target_files:
            return CoderResult(
                modified_files=[],
                change_summary="Offline mode supports only the bundled deterministic demonstration scenario.",
                assumptions=["No arbitrary natural-language code generation was attempted."],
                build_attempted=False,
                build_result={"status": "skipped", "commands": []},
            )

        try:
            resolve_repo_path(self.repo_path, DEMO_TARGET_FILE)
            original = read_file(self.repo_path, DEMO_TARGET_FILE)
        except Exception as exc:
            return CoderResult(
                modified_files=[],
                change_summary="Offline demo target file could not be read safely.",
                assumptions=[str(exc)],
                build_attempted=False,
                build_result={"status": "skipped", "commands": []},
            )

        if BUGGY_SNIPPET in original:
            updated = original.replace(BUGGY_SNIPPET, PATCHED_SNIPPET)
            write_file(self.repo_path, DEMO_TARGET_FILE, updated)
            changed_files = [DEMO_TARGET_FILE]
            summary = "Applied the bundled deterministic demo marker patch."
        elif PATCHED_SNIPPET in original:
            changed_files = [DEMO_TARGET_FILE]
            summary = "Bundled deterministic demo marker patch was already present."
        else:
            return CoderResult(
                modified_files=[],
                change_summary="Offline demo target did not match the expected safe original state.",
                assumptions=["Refused to patch because the file contents differed from the controlled fixture."],
                build_attempted=False,
                build_result={"status": "skipped", "commands": []},
            )

        build_result = run_build_plan(self.repo_path, agentic_input.build_commands)
        return CoderResult(
            modified_files=changed_files,
            change_summary=summary,
            patch_notes=["Offline deterministic demo patch; no LLM was used."],
            assumptions=[],
            build_attempted=True,
            build_result=build_result,
        )


class OfflineReviewerAgent:
    """Rule-based reviewer for deterministic offline and safety checks."""

    def __init__(self, repo_path: str | Path = ".") -> None:
        self.repo_path = Path(repo_path)

    def review_patch(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        coder_result: CoderResult,
    ) -> ReviewerResult:
        issues = []
        if not coder_result.modified_files:
            issues.append("Coder did not modify any files.")

        for path in coder_result.modified_files:
            try:
                resolve_repo_path(self.repo_path, path)
            except Exception as exc:
                issues.append(f"Unsafe changed file path rejected: {exc}")

        unrelated = sorted(set(coder_result.modified_files) - set(plan.target_files))
        if unrelated:
            issues.append("Coder modified files outside the Architect plan: " + ", ".join(unrelated))

        build_status = str((coder_result.build_result or {}).get("status") or "").lower()
        if coder_result.build_attempted and build_status != "success":
            issues.append("Build or tests did not pass.")

        if agentic_input.run_id == DEMO_RUN_ID and DEMO_TARGET_FILE in coder_result.modified_files:
            try:
                if PATCHED_SNIPPET not in read_file(self.repo_path, DEMO_TARGET_FILE):
                    issues.append("Expected bundled demo transformation was not present.")
            except Exception as exc:
                issues.append(f"Could not verify bundled demo transformation: {exc}")

        approved = not issues
        return ReviewerResult(
            approved=approved,
            verdict="APPROVED" if approved else "NEEDS_REVISION",
            issues_found=issues,
            plan_followed=not unrelated,
            unrelated_changes_detected=bool(unrelated),
            syntax_or_logic_risks=[] if approved else ["Deterministic reviewer found blocking issues."],
            required_revisions=issues,
            next_action="send_to_pr_publisher" if approved else "revise_patch",
        )


# Backward-compatible aliases retained from Commit 1/2 public imports.
HeuristicArchitectAgent = OfflineArchitectAgent
PlaceholderCoderAgent = OfflineCoderAgent
RuleBasedReviewerAgent = OfflineReviewerAgent
