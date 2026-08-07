"""Normalized LLM configuration for CrewAI online mode."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.config_errors import RuntimeConfigError
from src.llm.providers import LLMProvider, parse_llm_provider


GEMINI_DEFAULT_MODEL = "gemini/gemini-2.5-flash"
GROQ_DEFAULT_MODEL = "groq/llama-3.3-70b-versatile"


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProvider
    model: str
    temperature: float = 0.1


def _provider_from_model(model: str | None) -> LLMProvider | None:
    if not model:
        return None
    lowered = model.lower()
    if lowered.startswith("gemini/") or lowered.startswith("gemini-"):
        return LLMProvider.GEMINI
    if lowered.startswith("groq/"):
        return LLMProvider.GROQ
    return None


def _has_key(provider: LLMProvider) -> bool:
    if provider == LLMProvider.GEMINI:
        return bool(os.getenv("GEMINI_API_KEY"))
    if provider == LLMProvider.GROQ:
        return bool(os.getenv("GROQ_API_KEY"))
    return False


def _infer_provider(model: str | None = None) -> LLMProvider:
    model_provider = _provider_from_model(model)
    if model_provider is not None:
        return model_provider

    has_gemini = _has_key(LLMProvider.GEMINI)
    has_groq = _has_key(LLMProvider.GROQ)
    if has_gemini:
        return LLMProvider.GEMINI
    if has_groq:
        return LLMProvider.GROQ
    raise RuntimeConfigError("No supported LLM API key was found for CrewAI mode.")


def normalize_model(provider: LLMProvider, model: str | None) -> str:
    cleaned = (model or "").strip()
    if provider == LLMProvider.GEMINI:
        if not cleaned:
            return GEMINI_DEFAULT_MODEL
        if cleaned.startswith("gemini/"):
            return cleaned
        return f"gemini/{cleaned}"
    if provider == LLMProvider.GROQ:
        if not cleaned:
            return GROQ_DEFAULT_MODEL
        if cleaned.startswith("groq/"):
            return cleaned
        return f"groq/{cleaned}"
    raise RuntimeConfigError(f"Unsupported LLM provider: {provider}")


def validate_provider_credentials(provider: LLMProvider) -> None:
    if provider == LLMProvider.GEMINI and not os.getenv("GEMINI_API_KEY"):
        raise RuntimeConfigError("Gemini was selected, but GEMINI_API_KEY is not configured.")
    if provider == LLMProvider.GROQ and not os.getenv("GROQ_API_KEY"):
        raise RuntimeConfigError("Groq was selected, but GROQ_API_KEY is not configured.")


def load_llm_config() -> LLMConfig:
    explicit_provider = os.getenv("VISIONPR_LLM_PROVIDER")
    model = os.getenv("VISIONPR_LLM_MODEL") or None
    provider = parse_llm_provider(explicit_provider) if explicit_provider else _infer_provider(model)
    if not model:
        model = os.getenv("GEMINI_MODEL") if provider == LLMProvider.GEMINI else os.getenv("GROQ_MODEL")
    validate_provider_credentials(provider)
    return LLMConfig(provider=provider, model=normalize_model(provider, model))
