"""PushManager — orchestrates entity/session/lore-doc pushes to Foundry VTT.

Folder layout in Foundry:
    Living Codex/
      NPCs/
      PCs/
      Factions/
      Locations/
      Assets/
      Clues/
      Sessions/
      Lore/

Journal formats:
  Entity  — H1 name + status emoji, description, ## Events, ## Relationships, footer
  Session — H1 "Session N — {date}", summary body, footer
  Lore    — raw content converted to HTML
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import markdown as md_lib

from living_codex.sync.foundry import FoundryClient, FoundryOfflineError
from living_codex.sync.guard import ConflictDetected, ConflictGuard

if TYPE_CHECKING:
    from living_codex.database import CodexDB

logger = logging.getLogger(__name__)

# Status emoji map (mirrors formatter.py but used in journal headings)
_STATUS_EMOJI: dict[str, str] = {
    "active": "🟢",
    "grounded": "🔴",
    "inactive": "🔴",
    "dead": "💀",
    "destroyed": "💀",
}
_DEFAULT_EMOJI = "⬜"

# Foundry entity type → subfolder name
_TYPE_FOLDER: dict[str, str] = {
    "NPC": "NPCs",
    "PC": "PCs",
    "Faction": "Factions",
    "Location": "Locations",
    "Asset": "Assets",
    "Clue": "Clues",
}

_ROOT_FOLDER = "Living Codex"


def _status_emoji(status: str | None) -> str:
    if not status:
        return _DEFAULT_EMOJI
    return _STATUS_EMOJI.get(status.casefold(), _DEFAULT_EMOJI)


def _md_to_html(text: str) -> str:
    """Convert Markdown text to HTML for Foundry journal rendering."""
    return md_lib.markdown(text, extensions=["nl2br"])


def _render_entity_journal(entity: dict, events: list, relationships: list) -> str:
    """Build the full Markdown journal for an entity, then convert to HTML."""
    name = entity.get("name", "Unknown")
    emoji = _status_emoji(entity.get("status_label"))
    pub_desc = entity.get("description_public") or ""
    appearance = entity.get("appearance") or ""

    last_num = entity.get("last_seen_session_number")
    footer_session = f"Session {last_num}" if last_num is not None else "Unknown"

    lines: list[str] = [f"# {name} {emoji}"]

    if pub_desc:
        lines.append("")
        lines.append(pub_desc)

    if appearance:
        lines.append("")
        lines.append(f"*{appearance}*")

    # Events
    if events:
        lines.append("")
        lines.append("## Events")
        for ev in events:
            sn = ev.get("session_number", "?")
            ts = ev.get("event_timestamp") or ""
            text = ev.get("event_text", "")
            ts_part = f" {ts}" if ts else ""
            lines.append(f"- **S{sn}{ts_part}** {text}")

    # Relationships
    if relationships:
        lines.append("")
        lines.append("## Relationships")
        for rel in relationships:
            rel_type = rel.get("rel_type", "")
            target = rel.get("target_name", "")
            citation = rel.get("citation", "")
            cite_part = f" ({citation})" if citation else ""
            lines.append(f"- {rel_type} → {target}{cite_part}")

    lines.append("")
    lines.append(f"---\n*Last updated: {footer_session} | Living Codex*")

    return _md_to_html("\n".join(lines))


def _render_session_journal(session: dict) -> str:
    """Build the HTML journal for a session summary."""
    session_number = session.get("session_number", "?")
    recorded_at = session.get("recorded_at") or ""
    date_str = ""
    if recorded_at:
        try:
            dt = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
            date_str = f" — {dt.strftime('%Y-%m-%d')}"
        except ValueError:
            date_str = f" — {recorded_at}"

    summary = session.get("summary") or ""

    lines: list[str] = [
        f"# Session {session_number}{date_str}",
        "",
        summary,
        "",
        "---",
        "*Processed by Living Codex*",
    ]
    return _md_to_html("\n".join(lines))


class PushManager:
    """Orchestrates pushing entities, sessions, and lore docs to Foundry VTT."""

    def __init__(self, db: "CodexDB", client: FoundryClient):
        self._db = db
        self._client = client
        self._guard = ConflictGuard(client)
        self._folder_cache: dict[str, str | None] = {}  # folder_name → folder_id

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------

    async def _get_root_folder_id(self) -> str | None:
        if _ROOT_FOLDER not in self._folder_cache:
            fid = await self._client.get_or_create_folder(_ROOT_FOLDER)
            self._folder_cache[_ROOT_FOLDER] = fid
        return self._folder_cache[_ROOT_FOLDER]

    async def _get_type_folder_id(self, entity_type: str) -> str | None:
        subfolder_name = _TYPE_FOLDER.get(entity_type, entity_type + "s")
        cache_key = f"{_ROOT_FOLDER}/{subfolder_name}"
        if cache_key not in self._folder_cache:
            root_id = await self._get_root_folder_id()
            fid = await self._client.get_or_create_folder(subfolder_name, root_id)
            self._folder_cache[cache_key] = fid
        return self._folder_cache[cache_key]

    async def _get_named_folder_id(self, name: str) -> str | None:
        cache_key = f"{_ROOT_FOLDER}/{name}"
        if cache_key not in self._folder_cache:
            root_id = await self._get_root_folder_id()
            fid = await self._client.get_or_create_folder(name, root_id)
            self._folder_cache[cache_key] = fid
        return self._folder_cache[cache_key]

    # ------------------------------------------------------------------
    # Entity push
    # ------------------------------------------------------------------

    async def push_entity(self, entity_id: int, *, force: bool = False) -> str | None:
        """Push a single entity to Foundry.

        Returns the foundry_id on success, None on failure.
        Enqueues for retry on FoundryOfflineError.
        Enqueues as 'conflict' on ConflictDetected (unless force=True).
        """
        cursor = await self._db.db.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        )
        entity_row = await cursor.fetchone()
        if not entity_row:
            logger.warning("push_entity: entity_id=%d not found", entity_id)
            return None

        entity = dict(entity_row)

        # Resolve last_seen_session_number for the journal footer
        last_num = None
        if entity.get("last_seen_session_id"):
            last_num = await self._db.get_session_number(entity["last_seen_session_id"])
        entity["last_seen_session_number"] = last_num

        events_rows = await self._db.get_entity_events(entity_id, approved_only=True)
        events = [dict(r) for r in events_rows]
        rels_rows = await self._db.get_entity_relationships(entity_id)
        rels = [dict(r) for r in rels_rows]

        content = _render_entity_journal(entity, events, rels)

        try:
            foundry_id = entity.get("foundry_id")
            stored_hash = entity.get("foundry_hash")

            if foundry_id:
                # Existing entry — check for conflicts first
                if force:
                    await self._client.update_journal(foundry_id, content)
                    new_hash = FoundryClient.hash_content(content)
                else:
                    new_hash = await self._guard.safe_update(
                        foundry_id, stored_hash, entity["name"], content
                    )
            else:
                # New entry — create it
                folder_id = await self._get_type_folder_id(entity.get("type", ""))
                result = await self._client.create_journal(entity["name"], content, folder_id)
                foundry_id = result.get("_id") or result.get("id")
                new_hash = FoundryClient.hash_content(content)

            await self._db.update_entity_foundry(entity_id, foundry_id, new_hash)
            logger.info("Foundry: synced entity '%s' (id=%s)", entity["name"], foundry_id)
            return foundry_id

        except FoundryOfflineError as exc:
            logger.warning("Foundry offline — queuing entity_id=%d: %s", entity_id, exc)
            action = "update" if entity.get("foundry_id") else "create"
            await self._db.enqueue_sync(entity_id, action, json.dumps({"force": force}))
            return None

        except ConflictDetected as exc:
            logger.warning("CONFLICT: %s", exc)
            await self._db.enqueue_sync(entity_id, "conflict", "{}")
            return None

    # ------------------------------------------------------------------
    # Session push
    # ------------------------------------------------------------------

    async def push_session(self, session_id: int) -> str | None:
        """Push a session summary journal to Foundry.

        Returns the foundry_journal_id on success, None on failure.
        """
        cursor = await self._db.db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        session_row = await cursor.fetchone()
        if not session_row:
            logger.warning("push_session: session_id=%d not found", session_id)
            return None

        session = dict(session_row)
        if not session.get("summary"):
            logger.info("push_session: no summary yet for session_id=%d — skipping", session_id)
            return None

        content = _render_session_journal(session)
        session_name = f"Session {session.get('session_number', session_id)}"

        try:
            foundry_journal_id = session.get("foundry_journal_id")

            if foundry_journal_id:
                await self._client.update_journal(foundry_journal_id, content)
                new_hash = FoundryClient.hash_content(content)
            else:
                folder_id = await self._get_named_folder_id("Sessions")
                result = await self._client.create_journal(session_name, content, folder_id)
                foundry_journal_id = result.get("_id") or result.get("id")
                new_hash = FoundryClient.hash_content(content)

            await self._db.update_session_foundry(session_id, foundry_journal_id, new_hash)
            logger.info("Foundry: synced session '%s' (id=%s)", session_name, foundry_journal_id)
            return foundry_journal_id

        except FoundryOfflineError as exc:
            logger.warning("Foundry offline — skipping session push: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Lore doc push
    # ------------------------------------------------------------------

    async def push_lore_doc(self, name: str, raw_content: str) -> str | None:
        """Upload a raw Markdown/text lore document to the Lore folder in Foundry.

        Returns the created journal entry ID on success, None on failure.
        """
        content = _md_to_html(raw_content)
        try:
            folder_id = await self._get_named_folder_id("Lore")
            result = await self._client.create_journal(name, content, folder_id)
            journal_id = result.get("_id") or result.get("id")
            logger.info("Foundry: uploaded lore doc '%s' (id=%s)", name, journal_id)
            return journal_id
        except FoundryOfflineError as exc:
            logger.warning("Foundry offline — lore doc '%s' not uploaded: %s", name, exc)
            return None

    # ------------------------------------------------------------------
    # Queue drain
    # ------------------------------------------------------------------

    async def drain_queue(self) -> tuple[int, int]:
        """Process all queued sync items. Returns (succeeded, failed) counts.

        Skips 'conflict' entries — those require explicit GM force-override.
        """
        items = await self._db.get_sync_queue_items(limit=50)
        if not items:
            return (0, 0)

        succeeded = 0
        failed = 0

        for item in items:
            action = item["action"]
            entity_id = item["entity_id"]
            queue_id = item["id"]

            if action == "conflict":
                # Leave in queue — requires human intervention
                continue

            try:
                payload_raw = item["payload"] or "{}"
                payload = json.loads(payload_raw)
                force = bool(payload.get("force", False))

                result = await self.push_entity(entity_id, force=force)
                if result is not None:
                    await self._db.remove_from_sync_queue(queue_id)
                    succeeded += 1
                else:
                    # push_entity itself re-queued if offline; remove the old entry
                    # to avoid duplicate queue rows on repeated offline drain attempts.
                    # push_entity only re-queues on FoundryOfflineError, so if it
                    # returned None for a different reason, keep the entry.
                    failed += 1

            except Exception as exc:
                logger.error("drain_queue: unexpected error for entity_id=%d: %s", entity_id, exc)
                failed += 1

        logger.info("Foundry drain_queue: %d succeeded, %d failed", succeeded, failed)
        return (succeeded, failed)
