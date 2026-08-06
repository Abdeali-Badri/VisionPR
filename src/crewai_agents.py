"""CrewAI-backed Phase 3 agents for online execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from src.runtime_config import configure_crewai_storage

configure_crewai_storage()

try:
    from crewai import Agent, Crew, Process, Task  # type: ignore  # noqa: E402
    from crewai.tools import BaseTool  # type: ignore  # noqa: E402
except ImportError:  # pragma: no cover - exercised indirectly when CrewAI is absent
    Agent = None
    Crew = None
    Task = None

    class _Process:
        sequential = "sequential"

    Process = _Process()

    class BaseTool:
        name: str = ""
        description: str = ""

        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

from src.prompts import ARCHITECT_AGENT_PROMPT, CODER_AGENT_PROMPT, REDO_TASK_PROMPT, REVIEWER_AGENT_PROMPT
from src.schemas import AgenticInput, ArchitectPlan, CoderResult, ReviewerResult, RevisionRequest
from src.tools import read_file, run_build_plan, validate_build_command, write_file


class ArchitectPlanModel(BaseModel):
    suspected_cause: str
    target_files: list[str] = Field(default_factory=list)
    files_to_avoid: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    test_plan: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class CoderResultModel(BaseModel):
    modified_files: list[str] = Field(default_factory=list)
    change_summary: str
    patch_notes: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    build_attempted: bool = False
    build_result: dict[str, Any] = Field(default_factory=dict)


class ReviewerResultModel(BaseModel):
    approved: bool
    verdict: str
    issues_found: list[str] = Field(default_factory=list)
    plan_followed: bool = True
    unrelated_changes_detected: bool = False
    syntax_or_logic_risks: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)
    next_action: str = "send_to_pr_publisher"


class SafeReadFileTool(BaseTool):
    name: str = "safe_read_file"
    description: str = "Read a UTF-8 file from the trusted target repository using safe path validation."
    repo_path: str

    def _run(self, relative_path: str) -> str:
        return read_file(self.repo_path, relative_path)


class SafeWriteFileTool(BaseTool):
    name: str = "safe_write_file"
    description: str = "Write UTF-8 text inside the trusted target repository using safe path validation."
    repo_path: str

    def _run(self, relative_path: str, content: str) -> dict[str, Any]:
        return write_file(self.repo_path, relative_path, content)


class ValidatedBuildPlanTool(BaseTool):
    name: str = "run_validated_build_plan"
    description: str = "Run allow-listed build/test commands supplied by the workflow against the trusted target repository."
    repo_path: str

    def _run(self, commands: list[str]) -> dict[str, Any]:
        for command in commands:
            validate_build_command(command)
        return run_build_plan(self.repo_path, commands)


T = TypeVar("T", bound=BaseModel)


class CrewAIAdapterError(RuntimeError):
    """Raised when a CrewAI adapter cannot produce valid structured output."""


class _BaseCrewAIAdapter:
    role = "VisionPR Agent"
    goal = "Operate safely within the VisionPR Phase 3 workflow."
    backstory = "You are a constrained software engineering agent in VisionPR."

    def __init__(self, llm: Any, *, repo_path: str | Path = ".") -> None:
        if llm is None:
            raise CrewAIAdapterError("CrewAI agents require an injected LLM instance.")
        self.llm = llm
        self.repo_path = Path(repo_path)

    def _agent(self, *, tools: list[BaseTool] | None = None) -> Agent:
        if Agent is None:
            raise CrewAIAdapterError("CrewAI is not installed; online CrewAI agents cannot run.")
        return Agent(
            role=self.role,
            goal=self.goal,
            backstory=self.backstory,
            llm=self.llm,
            tools=tools or [],
            allow_delegation=False,
            verbose=False,
            max_iter=3,
            max_retry_limit=1,
            allow_code_execution=False,
        )

    def _run_task(self, *, description: str, expected_output: str, output_model: type[T], tools: list[BaseTool] | None = None) -> T:
        if Crew is None or Task is None:
            raise CrewAIAdapterError("CrewAI is not installed; online CrewAI agents cannot run.")
        agent = self._agent(tools=tools)
        task = Task(
            description=description,
            expected_output=expected_output,
            agent=agent,
            output_pydantic=output_model,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False, memory=False)
        result = crew.kickoff()
        raw = getattr(result, "pydantic", None) or getattr(task.output, "pydantic", None)
        if isinstance(raw, output_model):
            return raw
        raw_json = getattr(result, "raw", None) or getattr(task.output, "raw", None) or str(result)
        try:
            payload = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            return output_model.model_validate(payload)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise CrewAIAdapterError("CrewAI task did not return valid structured output.") from exc


class CrewAIArchitectAgent(_BaseCrewAIAdapter):
    role = "VisionPR Software Architect"
    goal = "Produce a minimal, safe, repository-grounded implementation plan from supplied issue and repository context."

    def create_plan(self, agentic_input: AgenticInput) -> ArchitectPlan:
        model = self._run_task(
            description=(
                ARCHITECT_AGENT_PROMPT
                + "\nAgentic input JSON:\n"
                + json.dumps(agentic_input.to_dict(), indent=2)
            ),
            expected_output="Structured ArchitectPlan JSON only.",
            output_model=ArchitectPlanModel,
        )
        return ArchitectPlan.from_dict(model.model_dump())


class CrewAICoderAgent(_BaseCrewAIAdapter):
    role = "VisionPR Safe Coder"
    goal = "Implement the approved plan using only safe repository tools and allow-listed build commands."

    def _tools(self) -> list[BaseTool]:
        repo = str(self.repo_path)
        return [
            SafeReadFileTool(repo_path=repo),
            SafeWriteFileTool(repo_path=repo),
            ValidatedBuildPlanTool(repo_path=repo),
        ]

    def implement_plan(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        revision_request: RevisionRequest | None = None,
    ) -> CoderResult:
        revision_text = ""
        if revision_request is not None:
            revision_text = "\nRevision request:\n" + json.dumps(revision_request.to_dict(), indent=2)
        model = self._run_task(
            description=(
                CODER_AGENT_PROMPT
                + "\n"
                + REDO_TASK_PROMPT
                + "\nAgentic input JSON:\n"
                + json.dumps(agentic_input.to_dict(), indent=2)
                + "\nArchitect plan JSON:\n"
                + json.dumps(plan.to_dict(), indent=2)
                + revision_text
            ),
            expected_output="Structured CoderResult JSON only.",
            output_model=CoderResultModel,
            tools=self._tools(),
        )
        return CoderResult.from_dict(model.model_dump())


class CrewAIReviewerAgent(_BaseCrewAIAdapter):
    role = "VisionPR Patch Reviewer"
    goal = "Review the patch result and return structured approval or revision feedback."

    def _tools(self) -> list[BaseTool]:
        return [SafeReadFileTool(repo_path=str(self.repo_path))]

    def review_patch(
        self,
        agentic_input: AgenticInput,
        plan: ArchitectPlan,
        coder_result: CoderResult,
    ) -> ReviewerResult:
        model = self._run_task(
            description=(
                REVIEWER_AGENT_PROMPT
                + "\nAgentic input JSON:\n"
                + json.dumps(agentic_input.to_dict(), indent=2)
                + "\nArchitect plan JSON:\n"
                + json.dumps(plan.to_dict(), indent=2)
                + "\nCoder result JSON:\n"
                + json.dumps(coder_result.to_dict(), indent=2)
            ),
            expected_output="Structured ReviewerResult JSON only.",
            output_model=ReviewerResultModel,
            tools=self._tools(),
        )
        return ReviewerResult.from_dict(model.model_dump())
