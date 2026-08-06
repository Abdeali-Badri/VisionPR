"""Tiny target repository module for the Phase 3 local demo."""

from __future__ import annotations

# DEMO_PATCH_PENDING


def save_profile(current_profile: dict[str, str], updates: dict[str, str]) -> dict[str, str]:
    """Return a profile with allowed updates applied."""
    next_profile = dict(current_profile)
    for field in ("name", "email"):
        if field in updates:
            next_profile[field] = updates[field]
    return next_profile
