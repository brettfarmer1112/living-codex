"""AI module — shared utilities for Gemini and Claude clients."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).parent / "codex_rules.md"
_cached_prompt: str = ""
_cached_mtime: float = 0.0


def load_system_prompt() -> str:
    """Load codex_rules.md with mtime-based caching.

    Re-reads from disk only when the file has been modified since the last read.
    This preserves hot-reload behaviour while eliminating redundant I/O.
    """
    global _cached_prompt, _cached_mtime

    try:
        mtime = os.path.getmtime(_RULES_PATH)
    except OSError:
        if _cached_prompt:
            return _cached_prompt
        logger.warning("codex_rules.md not found — running without system prompt.")
        return ""

    if mtime != _cached_mtime:
        _cached_prompt = _RULES_PATH.read_text(encoding="utf-8")
        _cached_mtime = mtime
        logger.debug("System prompt reloaded (mtime=%.0f, %d chars)", mtime, len(_cached_prompt))

    return _cached_prompt
