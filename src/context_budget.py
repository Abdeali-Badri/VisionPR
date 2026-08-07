"""Provider-neutral prompt compaction for repository agent calls."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


AGENTIC_INPUT_CHAR_BUDGET = _positive_env_int("VISIONPR_AGENT_CONTEXT_MAX_CHARS", 16_000)
REVIEW_DIFF_CHAR_BUDGET = _positive_env_int("VISIONPR_REVIEW_DIFF_MAX_CHARS", 8_000)
TOOL_READ_CHAR_BUDGET = _positive_env_int("VISIONPR_TOOL_READ_MAX_CHARS", 12_000)
MAX_CONTEXT_FILES = 8


def truncate_text(value: Any, max_chars: int, *, keep_tail: bool = False) -> str:
    """Truncate text with an explicit marker so agents do not mistake it for a full file."""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    marker = "\n...[truncated by VisionPR context budget]...\n"
    usable = max(0, max_chars - len(marker))
    if keep_tail and usable > 1:
        head = usable // 2
        return text[:head] + marker + text[-(usable - head) :]
    return text[:usable] + marker


def _compact_value(value: Any, *, string_chars: int, list_items: int, depth: int = 0) -> Any:
    if depth >= 4:
        return truncate_text(value, string_chars)
    if isinstance(value, Mapping):
        return {
            str(key): _compact_value(item, string_chars=string_chars, list_items=list_items, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, (list, tuple)):
        return [
            _compact_value(item, string_chars=string_chars, list_items=list_items, depth=depth + 1)
            for item in list(value)[:list_items]
        ]
    if isinstance(value, str):
        return truncate_text(value, string_chars)
    return value


def _bounded_mapping(value: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    """Keep arbitrary meeting metadata useful while bounding unknown provider payloads."""
    for string_chars, list_items in ((900, 8), (500, 6), (250, 4), (120, 3)):
        compacted = _compact_value(value, string_chars=string_chars, list_items=list_items)
        if len(json.dumps(compacted, ensure_ascii=False)) <= max_chars:
            return dict(compacted)
    return {
        str(key): truncate_text(item, 120)
        for key, item in list(value.items())[:8]
    }


def compact_agentic_input(
    agentic_input: Any,
    *,
    include_file_contents: bool,
    char_budget: int | None = None,
) -> dict[str, Any]:
    """Build a valid, ranked AgenticInput payload that fits a stable character budget."""
    budget = max(4_000, char_budget or AGENTIC_INPUT_CHAR_BUDGET)
    source = agentic_input.to_dict() if hasattr(agentic_input, "to_dict") else dict(agentic_input)
    repository = dict(source.get("repository_context") or {})
    files = list(repository.get("relevant_files") or [])[:MAX_CONTEXT_FILES]

    payload: dict[str, Any] = {
        "run_id": truncate_text(source.get("run_id"), 200),
        "issue_summary": truncate_text(source.get("issue_summary"), 2_000),
        "meeting_issue_context": _bounded_mapping(
            dict(source.get("meeting_issue_context") or {}),
            max_chars=3_500,
        ),
        "repository_context": {
            "repo_tree": truncate_text(repository.get("repo_tree"), 2_500),
            "relevant_files": [],
            "files_scanned": repository.get("files_scanned"),
            "context_files_selected": repository.get("context_files_selected"),
        },
        "build_commands": [truncate_text(item, 300) for item in list(source.get("build_commands") or [])[:12]],
        "constraints": [truncate_text(item, 500) for item in list(source.get("constraints") or [])[:12]],
        "max_review_attempts": source.get("max_review_attempts", 3),
    }

    compact_files: list[dict[str, Any]] = payload["repository_context"]["relevant_files"]
    for item in files:
        if not isinstance(item, Mapping):
            continue
        compact_files.append(
            {
                "path": truncate_text(item.get("path"), 300),
                "summary": truncate_text(item.get("summary"), 300),
                "symbols": [truncate_text(symbol, 80) for symbol in list(item.get("symbols") or [])[:8]],
            }
        )

    # Preserve the highest-ranked source excerpts first. Each addition is checked
    # against the serialized payload because JSON escaping can expand source text.
    if include_file_contents:
        for compact, original in zip(compact_files, files):
            excerpt = truncate_text(original.get("content_excerpt"), 1_600)
            if not excerpt:
                continue
            compact["content_excerpt"] = excerpt
            while len(json.dumps(payload, ensure_ascii=False)) > budget and len(excerpt) > 160:
                excerpt = truncate_text(excerpt, max(160, len(excerpt) // 2))
                compact["content_excerpt"] = excerpt
            if len(json.dumps(payload, ensure_ascii=False)) > budget:
                compact.pop("content_excerpt", None)

    # Pathological user-supplied metadata may still be larger than the target.
    # Drop lower-ranked file metadata before touching the task statement.
    while len(json.dumps(payload, ensure_ascii=False)) > budget and len(compact_files) > 1:
        compact_files.pop()
    if len(json.dumps(payload, ensure_ascii=False)) > budget:
        payload["meeting_issue_context"] = _bounded_mapping(
            dict(source.get("meeting_issue_context") or {}),
            max_chars=1_200,
        )
        payload["repository_context"]["repo_tree"] = truncate_text(repository.get("repo_tree"), 800)

    return payload
