"""Factory for Phase 3 online and offline agent bundles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.agent_interfaces import ArchitectAgent, CoderAgent, ReviewerAgent
from src.config_errors import RuntimeConfigError
from src.llm.factory import create_llm
from src.runtime_config import AgentEngine, RuntimeConfig, detect_runtime


@dataclass(frozen=True)
class AgentBundle:
    architect: ArchitectAgent
    coder: CoderAgent
    reviewer: ReviewerAgent
    runtime: RuntimeConfig


def create_agent_bundle(runtime: RuntimeConfig | None = None, *, repo_path: str | Path = ".") -> AgentBundle:
    runtime = runtime or detect_runtime()
    if runtime.engine == AgentEngine.CREWAI:
        if runtime.llm is None:
            raise RuntimeConfigError("CrewAI agent bundle requires a configured Gemini or Groq LLM.")
        from src.crewai_agents import CrewAIArchitectAgent, CrewAICoderAgent, CrewAIReviewerAgent

        llm = create_llm(runtime.llm)
        return AgentBundle(
            architect=CrewAIArchitectAgent(llm),
            coder=CrewAICoderAgent(llm, repo_path=repo_path),
            reviewer=CrewAIReviewerAgent(llm, repo_path=repo_path),
            runtime=runtime,
        )

    if runtime.engine == AgentEngine.HEURISTIC:
        from src.offline_agents import OfflineArchitectAgent, OfflineCoderAgent, OfflineReviewerAgent

        return AgentBundle(
            architect=OfflineArchitectAgent(),
            coder=OfflineCoderAgent(repo_path=repo_path),
            reviewer=OfflineReviewerAgent(repo_path=repo_path),
            runtime=runtime,
        )

    raise RuntimeConfigError(f"Unsupported agent engine: {runtime.engine}")
