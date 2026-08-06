"""Runtime detection for VisionPR Phase 3 execution modes."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from src.config_errors import RuntimeConfigError
from src.llm.config import LLMConfig, load_llm_config

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency check catches this
    load_dotenv = None


class ExecutionMode(str, Enum):
    CREWAI = "crewai"
    OFFLINE_DEMO = "offline_demo"


class AgentEngine(str, Enum):
    CREWAI = "crewai"
    HEURISTIC = "heuristic"


@dataclass(frozen=True)
class RuntimeConfig:
    engine: AgentEngine
    llm: LLMConfig | None
    reason: str
    requested_mode: str
    crewai_installed: bool

    def __post_init__(self) -> None:
        if self.engine == AgentEngine.CREWAI and self.llm is None:
            raise RuntimeConfigError("CrewAI engine requires a Gemini or Groq LLM configuration.")

    @property
    def mode(self) -> ExecutionMode:
        if self.engine == AgentEngine.CREWAI:
            return ExecutionMode.CREWAI
        return ExecutionMode.OFFLINE_DEMO

    @property
    def model(self) -> str | None:
        return self.llm.model if self.llm is not None else None

    @property
    def provider(self) -> str | None:
        return self.llm.provider.value if self.llm is not None else None

    @property
    def llm_used(self) -> bool:
        return self.engine == AgentEngine.CREWAI

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["engine"] = self.engine.value
        if self.llm is not None:
            result["llm"]["provider"] = self.llm.provider.value
        result["mode"] = self.mode.value
        result["model"] = self.model
        result["provider"] = self.provider
        result["llm_used"] = self.llm_used
        return result


def configure_crewai_storage() -> None:
    """Keep CrewAI's local storage inside the repository when possible."""
    local_data = Path.cwd() / ".crewai-local"
    os.environ["LOCALAPPDATA"] = str(local_data)
    os.environ["XDG_DATA_HOME"] = str(local_data)
    try:
        import appdirs

        appdirs.system = "linux"
    except ImportError:
        pass


def crewai_is_installed() -> bool:
    configure_crewai_storage()
    try:
        import crewai  # noqa: F401
    except ImportError:
        return False
    return True


def _load_environment() -> None:
    if os.getenv("PYTHON_DOTENV_DISABLED"):
        return
    if load_dotenv is not None:
        load_dotenv()


def _has_supported_llm_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"))


def detect_runtime() -> RuntimeConfig:
    """Detect whether Phase 3 should run in CrewAI online mode or offline demo mode."""
    _load_environment()
    requested = os.getenv("VISIONPR_MODE", "auto").strip().lower() or "auto"
    if requested == "offline_demo":
        requested = "offline"
    if requested not in {"auto", "crewai", "offline"}:
        raise RuntimeConfigError(f"Unsupported VISIONPR_MODE: {requested}")

    installed = crewai_is_installed()
    if requested == "offline":
        return RuntimeConfig(
            engine=AgentEngine.HEURISTIC,
            llm=None,
            reason="VISIONPR_MODE=offline requested deterministic offline demo agents.",
            requested_mode=requested,
            crewai_installed=installed,
        )
    if requested == "crewai":
        llm_config = load_llm_config()
        return RuntimeConfig(
            engine=AgentEngine.CREWAI,
            llm=llm_config,
            reason="VISIONPR_MODE=crewai requested online CrewAI agents with configured credentials.",
            requested_mode=requested,
            crewai_installed=installed,
        )
    if not _has_supported_llm_key():
        return RuntimeConfig(
            engine=AgentEngine.HEURISTIC,
            llm=None,
            reason="VISIONPR_MODE=auto found no supported API key, so offline demo agents were selected.",
            requested_mode=requested,
            crewai_installed=installed,
        )
    llm_config = load_llm_config()
    return RuntimeConfig(
        engine=AgentEngine.CREWAI,
        llm=llm_config,
        reason=f"VISIONPR_MODE=auto selected {llm_config.provider.value} for online CrewAI agents.",
        requested_mode=requested,
        crewai_installed=installed,
    )
