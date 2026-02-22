"""AI client router — auto-selects Gemini or Claude SDK based on model name.

Usage:
    client = create_ai_client("claude-haiku-4-5", config)
    client = create_ai_client("gemini-2.5-flash-lite", config)

Both clients expose the same async interface:
    .extract_entities(transcript, campaign_name, known_pcs) -> list[dict]
    .summarize_session(transcript, campaign_name, session_number) -> str
    .query(question, campaign_name, *, entities, relationships, summaries, lore_docs, transcripts) -> str
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from living_codex.config import CodexConfig

logger = logging.getLogger(__name__)


def create_ai_client(model: str, config: "CodexConfig") -> Any:
    """Instantiate the correct AI client based on model name prefix.

    - "gemini-*"  → GeminiProClient (requires CODEX_GEMINI_API_KEY)
    - "claude-*"  → ClaudeClient    (requires CODEX_ANTHROPIC_API_KEY or CODEX_CLAUDE_API_KEY)

    Raises ValueError if the model prefix is unrecognised or the required API key is missing.
    """
    if model.startswith("gemini-"):
        if not config.gemini_api_key:
            raise ValueError(
                f"Model '{model}' requires CODEX_GEMINI_API_KEY to be set."
            )
        from living_codex.ai.gemini_pro import GeminiProClient

        logger.info("AI router: using GeminiProClient (model=%s)", model)
        return GeminiProClient(api_key=config.gemini_api_key, model=model)

    if model.startswith("claude-"):
        if not config.anthropic_api_key:
            raise ValueError(
                f"Model '{model}' requires CODEX_ANTHROPIC_API_KEY (or CODEX_CLAUDE_API_KEY) to be set."
            )
        from living_codex.ai.claude import ClaudeClient

        logger.info("AI router: using ClaudeClient (model=%s)", model)
        return ClaudeClient(api_key=config.anthropic_api_key, model=model)

    raise ValueError(
        f"Unknown model '{model}' — name must start with 'gemini-' or 'claude-'."
    )
