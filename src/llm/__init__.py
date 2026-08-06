"""LLM configuration and construction for VisionPR Phase 3."""

from src.llm.config import LLMConfig, load_llm_config
from src.llm.providers import LLMProvider, parse_llm_provider

__all__ = [
    "LLMConfig",
    "LLMFactory",
    "LLMProvider",
    "create_llm",
    "load_llm_config",
    "parse_llm_provider",
]


def __getattr__(name: str):
    if name in {"LLMFactory", "create_llm"}:
        from src.llm.factory import LLMFactory, create_llm

        return {"LLMFactory": LLMFactory, "create_llm": create_llm}[name]
    raise AttributeError(name)
