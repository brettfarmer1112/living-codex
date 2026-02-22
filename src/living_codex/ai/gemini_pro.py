"""Async Gemini Pro client for entity extraction, session summarization, and queries.

Uses the google-genai SDK with native async support (client.aio).
Replaces the Anthropic Claude client with the same public interface.
"""

import json
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from living_codex.ai.prompts import EXTRACT_ENTITIES, QUERY_CODEX, SUMMARIZE_SESSION

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash-lite"


def _load_system_prompt() -> str:
    """Load codex_rules.md as the system instruction for Gemini calls.
    """Load codex_rules.md as the system instruction for Gemini calls.

    Re-read from disk on every call so edits take effect without a restart.
    Re-read from disk on every call so edits take effect without a restart.
    Falls back to an empty string if the file is missing.
    """
    rules_path = Path(__file__).parent / "codex_rules.md"
    try:
        return rules_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("codex_rules.md not found — running without system prompt.")
        return ""


class GeminiProClient:
    """Async Gemini Pro client for Living Codex AI tasks.

    Drop-in replacement for ClaudeClient — same method signatures.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _base_config(self, max_tokens: int, **extra: Any) -> types.GenerateContentConfig:
        """Build a GenerateContentConfig with system instruction and token limit.

        Re-reads codex_rules.md from disk on every call so edits take
        effect without restarting the bot.
        """
        """Build a GenerateContentConfig with system instruction and token limit.

        Re-reads codex_rules.md from disk on every call so edits take
        effect without restarting the bot.
        """
        kwargs: dict[str, Any] = dict(
            max_output_tokens=max_tokens,
        )
        system_prompt = _load_system_prompt()
        if system_prompt:
            kwargs["system_instruction"] = system_prompt
        system_prompt = _load_system_prompt()
        if system_prompt:
            kwargs["system_instruction"] = system_prompt
        kwargs.update(extra)
        return types.GenerateContentConfig(**kwargs)

    async def extract_entities(
        self, transcript: str, campaign_name: str, known_pcs: list[str]
    ) -> list[dict]:
        """Extract structured entities from a transcript. Returns list of dicts."""
        known_pcs_str = "\n".join(f"- {name}" for name in known_pcs) if known_pcs else "(none known yet)"
        prompt = EXTRACT_ENTITIES.format(
            campaign_name=campaign_name,
            known_pcs=known_pcs_str,
            transcript=transcript,
        )
        logger.info("Gemini Pro: extracting entities (model=%s, transcript_len=%d)", self.model, len(transcript))

        config = self._base_config(
            8192,
            response_mime_type="application/json",
        )
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        text = response.text.strip()
        result = json.loads(text)
        logger.info("Gemini Pro: extracted %d entities", len(result))
        return result

    async def summarize_session(
        self, transcript: str, campaign_name: str, session_number: int
    ) -> str:
        """Generate a verbose narrative summary of a session. Returns markdown string."""
        prompt = SUMMARIZE_SESSION.format(
            campaign_name=campaign_name,
            session_number=session_number,
            transcript=transcript,
        )
        logger.info("Gemini Pro: summarizing session %d (model=%s)", session_number, self.model)

        config = self._base_config(4096)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return response.text.strip()

    async def query(
        self,
        question: str,
        campaign_name: str,
        *,
        entities: str = "",
        relationships: str = "",
        summaries: str = "",
        lore_docs: str = "",
        transcripts: str = "",
    ) -> str:
        """Answer a natural language question using all available campaign context."""
        prompt = QUERY_CODEX.format(
            campaign_name=campaign_name,
            question=question,
            entities=entities or "(none)",
            relationships=relationships or "(none)",
            summaries=summaries or "(none)",
            lore_docs=lore_docs or "(none)",
            transcripts=transcripts or "(none)",
        )
        logger.info("Gemini Pro: answering query (model=%s, question=%r)", self.model, question[:80])

        config = self._base_config(4096)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return response.text.strip()
