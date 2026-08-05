"""Run the deterministic Phase 3 demo against the mock target repository."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from src.crew_engine import run_agentic_workflow_from_file


def to_serializable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    input_path = repo_root / "mock_data" / "agentic_input.json"
    target_repo = repo_root / "mock_target_repo"

    result = run_agentic_workflow_from_file(input_path, repo_path=target_repo)
    print(json.dumps(to_serializable(result), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
