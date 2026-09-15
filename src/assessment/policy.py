"""Load runtime chat policy from disk (never inline the policy into source)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# repo root / policy / chat-policy.md
_POLICY_PATH = Path(__file__).resolve().parents[2] / "policy" / "chat-policy.md"


class PolicyLoadError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_chat_policy() -> str:
    """Return policy/chat-policy.md in full, unmodified."""
    if not _POLICY_PATH.is_file():
        raise PolicyLoadError(f"Missing chat policy at {_POLICY_PATH}")
    text = _POLICY_PATH.read_text(encoding="utf-8")
    if not text.strip():
        raise PolicyLoadError(f"Chat policy is empty: {_POLICY_PATH}")
    return text
