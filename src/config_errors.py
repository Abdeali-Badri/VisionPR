"""Shared configuration exceptions for VisionPR."""

from __future__ import annotations


class RuntimeConfigError(ValueError):
    """Raised when the requested Phase 3 runtime cannot be configured."""
