"""CrewAI-compatible LLM construction for Gemini and Groq."""

from __future__ import annotations

import re
import time
from typing import Any

from src.config_errors import RuntimeConfigError
from src.llm.config import LLMConfig, validate_provider_credentials
from src.llm.providers import LLMProvider
from src.runtime_config import configure_crewai_storage


def _load_crewai_llm() -> type[Any]:
    configure_crewai_storage()
    from crewai import LLM

    return LLM


def _without_cache_breakpoints(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_cache_breakpoints(item)
            for key, item in value.items()
            if key != "cache_breakpoint"
        }
    if isinstance(value, list):
        return [_without_cache_breakpoints(item) for item in value]
    return value


def _groq_compatible_llm_class(base: type[Any]) -> type[Any]:
    class GroqCompatibleLLM(base):
        def call(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
            cleaned = _without_cache_breakpoints(messages)
            for attempt in range(3):
                try:
                    return super().call(cleaned, *args, **kwargs)
                except Exception as exc:
                    text = str(exc)
                    rate_limited = "ratelimit" in type(exc).__name__.lower() or "rate limit" in text.lower()
                    if not rate_limited or attempt == 2:
                        raise
                    match = re.search(r"try again in\s+([0-9.]+)s", text, flags=re.IGNORECASE)
                    delay = float(match.group(1)) if match else 2 ** (attempt + 1)
                    time.sleep(min(max(delay + 0.5, 1.0), 30.0))
            raise AssertionError("Groq retry loop exhausted")

    return GroqCompatibleLLM


class LLMFactory:
    @staticmethod
    def create(config: LLMConfig) -> Any:
        validate_provider_credentials(config.provider)

        if config.provider in {LLMProvider.GEMINI, LLMProvider.GROQ}:
            LLM = _load_crewai_llm()
            options: dict[str, Any] = {
                "model": config.model,
                "temperature": config.temperature,
            }
            if config.provider == LLMProvider.GROQ:
                options.update(
                    {
                        "drop_params": True,
                        "additional_drop_params": ["messages[*].cache_breakpoint"],
                    }
                )
                LLM = _groq_compatible_llm_class(LLM)
            return LLM(**options)
        raise RuntimeConfigError(f"Unsupported LLM provider: {config.provider}")


def create_llm(config: LLMConfig) -> Any:
    return LLMFactory.create(config)
