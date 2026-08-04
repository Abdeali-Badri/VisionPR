# VisionPR Architecture

VisionPR converts meeting recordings and screen-recorded bug reports into
tested, reviewable pull requests.

## High-Level Pipeline

```text
Video/audio issue context
        +
Repository context
        ↓
Agentic patch workflow
        ↓
Local build and review checks
        ↓
GitHub pull request
        ↓
Human review gate
        ↓
Revision loop or final approval
```

## Data Model

The agent workflow uses human-readable handoff names:

- `meeting_issue_context`: transcript segments, screenshot descriptions, and UI
  observations from the recording.
- `repository_context`: relevant file paths, file summaries, symbols, and repo
  tree information.
- `agentic_input`: combined issue and repository context used to start the
  agent workflow.
- `implementation_plan`: the Architect Agent's code plan.
- `revision_request`: feedback sent back to the Coder Agent after failed review.
- `agent_workflow_result`: the final status and patch summary passed to PR
  publishing.

## Agentic Patch Workflow

The Phase 3 workflow uses three agents.

The Architect Agent maps the reported issue to likely target files and creates a
small implementation plan.

The Coder Agent follows that plan and edits selected files using safe repository
tools.

The Reviewer Agent checks the patch, build result, and file boundaries. If the
patch is not ready, it returns concrete revision feedback to the Coder Agent.

The retry loop is bounded to prevent indefinite autonomous edits.

## Safety Boundaries

The local tool layer rejects unsafe paths such as `.git`, `.env`, virtual
environment folders, and `node_modules`.

The workflow is designed to preserve unrelated user changes by keeping the
Coder Agent scoped to the Architect Agent's target files. Git staging and PR
publishing are handled later by the repository publishing layer.

## Human-in-the-Loop Gate

After PR publishing, VisionPR pauses for human engineering review. If reviewers
request changes, their feedback becomes a new revision task for the Coder Agent.
The system repeats the patch and review cycle until human approval or merge.
