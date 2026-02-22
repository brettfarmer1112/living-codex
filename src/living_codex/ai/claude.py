"""Async Anthropic Claude client for entity extraction, session summarization, and queries.

The Anthropic SDK is natively async — no thread executor required.
"""

import json
import logging
from pathlib import Path

import anthropic

from living_codex.ai.prompts import EXTRACT_ENTITIES, QUERY_CODEX, SUMMARIZE_SESSION

logger = logging.getLogger(__name__)


def _load_system_prompt() -> str:
    """Load codex_rules.md as the system prompt for all Claude calls.

    Falls back to an empty string if the file is missing.
    """
    rules_path = Path(__file__).parent / "codex_rules.md"
    try:
        return rules_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("codex_rules.md not found — running without system prompt.")
        return ""


class ClaudeClient:
    """Async Claude client for Living Codex AI tasks."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self._system_prompt = _load_system_prompt()

    def _call_kwargs(self, prompt: str, max_tokens: int) -> dict:
        """Build the base kwargs dict for a messages.create call."""
        kwargs: dict = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if self._system_prompt:
            kwargs["system"] = self._system_prompt
        return kwargs

    async def extract_entities(
        self, transcript: str, campaign_name: str, known_pcs: list[str]
    ) -> list[dict]:
        """Extract structured entities from a transcript. Returns list of dicts.

        known_pcs: list of canonical player character names to tag as type='PC'.
        """
        known_pcs_str = "\n".join(f"- {name}" for name in known_pcs) if known_pcs else "(none known yet)"
        prompt = EXTRACT_ENTITIES.format(
            campaign_name=campaign_name,
            known_pcs=known_pcs_str,
            transcript=transcript,
        )
        logger.info("Claude: extracting entities (model=%s, transcript_len=%d)", self.model, len(transcript))

        message = await self.client.messages.create(**self._call_kwargs(prompt, 8192))
        text = message.content[0].text.strip()

        # Strip markdown fences if Claude wraps anyway
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        result = json.loads(text)
        logger.info("Claude: extracted %d entities", len(result))
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
        logger.info("Claude: summarizing session %d (model=%s)", session_number, self.model)

        message = await self.client.messages.create(**self._call_kwargs(prompt, 4096))
        return message.content[0].text.strip()

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
        logger.info("Claude: answering query (model=%s, question=%r)", self.model, question[:80])

        message = await self.client.messages.create(**self._call_kwargs(prompt, 4096))
        return message.content[0].text.strip()
