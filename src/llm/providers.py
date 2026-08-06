"""Supported online LLM providers for VisionPR Phase 3."""

from __future__ import annotations

from enum import Enum

from src.config_errors import RuntimeConfigError


class LLMProvider(str, Enum):
    GEMINI = "gemini"
    GROQ = "groq"


def supported_provider_names() -> str:
    return ", ".join(provider.value for provider in LLMProvider)


def parse_llm_provider(value: str) -> LLMProvider:
    normalized = value.strip().lower()
    for provider in LLMProvider:
        if normalized == provider.value:
            return provider
    raise RuntimeConfigError(
        f"Unsupported LLM provider '{value}'. Supported providers: {supported_provider_names()}."
    )
