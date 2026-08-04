# VisionPR Agent Rules and Skills

This document describes the custom agent system used by VisionPR to convert
meeting issue context and repository context into a reviewed code patch.

## Agent System Goal

The agent workflow must behave like a disciplined engineering review loop:

1. The Architect Agent plans the change.
2. The Coder Agent implements the smallest safe patch.
3. The Reviewer Agent checks the patch before PR publishing.
4. Failed reviews return to the Coder Agent for a bounded number of attempts.

The workflow avoids internal phase-number handoff names in its code-facing data
contracts. Clear names such as `agentic_input`, `meeting_issue_context`,
`repository_context`, `agent_workflow_result`, and `revision_request` are used so
external reviewers can understand the system without knowing team assignments.

## Architect Agent

The Architect Agent receives the issue context, screenshot descriptions,
repository tree, relevant file summaries, constraints, and build commands.

Responsibilities:

- Identify the likely cause of the issue.
- Select the smallest reasonable set of target files.
- Name files or areas that must not be touched.
- Produce implementation steps for the Coder Agent.
- Recommend tests and build commands.
- Avoid editing code.

Success criteria:

- The plan is specific enough for another agent to execute.
- The plan preserves existing codebase boundaries.
- The plan avoids unrelated refactoring.

## Coder Agent

The Coder Agent receives the Architect Agent's plan and repository tools from
the local build/file-system layer.

Responsibilities:

- Read relevant files.
- Modify only planned target files unless the plan is impossible.
- Preserve existing coding style.
- Make the minimum required change.
- Return modified files, patch notes, assumptions, and build status.

Rules:

- Do not touch files listed in `files_to_avoid`.
- Do not rename clear handoff files back to ambiguous phase-number names.
- Do not make formatting-only or cleanup changes unrelated to the issue.
- If a deviation is necessary, document it in the result.

## Reviewer Agent

The Reviewer Agent checks the Coder Agent's patch and build result.

Responsibilities:

- Verify the patch matches the Architect Agent's plan.
- Check whether the reported issue is actually addressed.
- Detect unrelated file changes.
- Surface syntax, logic, and test risks.
- Approve the patch or request specific revisions.

Approval criteria:

- The patch changes only intended files.
- The implementation follows the plan.
- The build/test result is successful or explicitly justified.
- The change is small enough for human PR review.

## Retry Policy

The default workflow allows up to three review attempts.

If the Reviewer Agent returns `NEEDS_REVISION`, the engine creates a
`revision_request` containing:

- The original Architect Agent plan.
- Reviewer feedback.
- Build logs if available.
- Previously modified files.
- The revision attempt number.

The Architect Agent does not rerun during normal retries. The original plan
stays the source of truth unless human engineers later provide new review
feedback through the HITL gate.

## Custom Skills

VisionPR Phase 3 depends on these local skills:

- Repository file reading with safe path validation.
- Repository file writing with safe path validation.
- Build/test command execution with structured logs.
- Agent review loop orchestration.
- Revision request generation for failed review attempts.

These skills are implemented in `src/tools.py` and coordinated by
`src/crew_engine.py`.

## Commit Discipline

Development should be committed after each major milestone:

- Agent schemas and data contracts.
- Prompt contracts.
- Agent workflow engine.
- Mock `agentic_input` example.
- Review retry behavior.
- Documentation updates.

This keeps progress reviewable and avoids a single large final commit.
