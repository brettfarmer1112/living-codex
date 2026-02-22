"""ConflictGuard — detects manual GM edits in Foundry before overwriting.

Flow:
  1. Fetch the live journal content from Foundry.
  2. Hash it.
  3. Compare against the stored hash in the DB.
  4. If they differ → raise ConflictDetected (entry was manually edited).
  5. If they match → proceed with update, return new hash.
"""

from __future__ import annotations

import logging

from living_codex.sync.foundry import FoundryClient

logger = logging.getLogger(__name__)


class ConflictDetected(Exception):
    """Raised when a Foundry journal entry was edited manually since last sync."""

    def __init__(self, entity_name: str, foundry_id: str):
        super().__init__(
            f"Manual edit detected on '{entity_name}' (foundry_id={foundry_id}). "
            "Use force=True to overwrite."
        )
        self.entity_name = entity_name
        self.foundry_id = foundry_id


class ConflictGuard:
    """Wraps FoundryClient update calls with hash-based conflict detection."""

    def __init__(self, client: FoundryClient):
        self._client = client

    async def check(
        self, foundry_id: str, stored_hash: str | None, entity_name: str
    ) -> str:
        """Fetch the live journal, hash it, compare against *stored_hash*.

        Returns the live hash (useful for callers that just want to verify without updating).
        Raises ConflictDetected if the hashes differ (meaning a human edited the entry).
        """
        journal = await self._client.get_journal(foundry_id)
        live_content = journal.get("content", "")
        live_hash = FoundryClient.hash_content(live_content)

        if stored_hash and live_hash != stored_hash:
            raise ConflictDetected(entity_name, foundry_id)

        return live_hash

    async def safe_update(
        self,
        foundry_id: str,
        stored_hash: str | None,
        entity_name: str,
        new_content: str,
    ) -> str:
        """Check for conflicts, then update the journal content.

        Returns the hash of *new_content* so the caller can persist it.
        Raises ConflictDetected if the stored_hash doesn't match the live content.
        """
        await self.check(foundry_id, stored_hash, entity_name)
        await self._client.update_journal(foundry_id, new_content)
        new_hash = FoundryClient.hash_content(new_content)
        logger.info("ConflictGuard: updated '%s' (foundry_id=%s)", entity_name, foundry_id)
        return new_hash
