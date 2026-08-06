"""CrewAI-compatible LLM construction for Gemini and Groq."""

from __future__ import annotations

from typing import Any

from src.config_errors import RuntimeConfigError
from src.llm.config import LLMConfig, validate_provider_credentials
from src.llm.providers import LLMProvider
from src.runtime_config import configure_crewai_storage


def _load_crewai_llm() -> type[Any]:
    configure_crewai_storage()
    from crewai import LLM

    return LLM


class LLMFactory:
    @staticmethod
    def create(config: LLMConfig) -> Any:
        validate_provider_credentials(config.provider)

        if config.provider in {LLMProvider.GEMINI, LLMProvider.GROQ}:
            LLM = _load_crewai_llm()
            return LLM(model=config.model, temperature=config.temperature)
        raise RuntimeConfigError(f"Unsupported LLM provider: {config.provider}")


def create_llm(config: LLMConfig) -> Any:
    return LLMFactory.create(config)
