"""JSON loading helpers for VisionPR pipeline handoff artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_AGENT_RESULT_KEYS = {
    "status",
    "changed_files",
    "coder_result",
    "reviewer_result",
    "review_attempts",
}


def load_agent_result(path: str | Path) -> dict[str, Any]:
    """Load and validate Phase 3's AgentWorkflowResult JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Phase 3 AgentWorkflowResult JSON must be an object.")

    missing_keys = sorted(REQUIRED_AGENT_RESULT_KEYS - payload.keys())
    if missing_keys:
        raise ValueError(
            "Phase 3 AgentWorkflowResult JSON is missing required keys: "
            + ", ".join(missing_keys)
        )
    return payload


def load_transcript(path: str | Path) -> Any:
    """Load a transcript JSON artifact from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
