"""Scribe Pipeline: audio file → Gemini transcription → AI extraction → staged_changes.

Supports two modes:
- Single-file mode: one .mp3/.wav/.flac → transcribe with speaker identification
- Craig folder mode: directory of per-speaker .flac files → transcribe each, merge

AI architecture split:
  - Gemini 2.0 Flash (Files API): audio → transcript
  - Gemini 2.5 Pro (or Claude fallback): transcript → entities, session summary

Foundry push:
  - After summary is generated and session is marked processed, push_session() is called
    on the PushManager if one is wired in. Failures are logged but never crash the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from living_codex.ai.gemini import GeminiClient
from living_codex.database import CodexDB

if TYPE_CHECKING:
    from living_codex.sync.push import PushManager

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg"}


class ScribePipeline:
    """Processes audio into staged entity changes."""

    def __init__(
        self,
        db: CodexDB,
        gemini: GeminiClient,
        claude: Any,
        campaign_id: int,
        push_manager: "PushManager | None" = None,
    ):
        self.db = db
        self.gemini = gemini
        self.claude = claude
        self.campaign_id = campaign_id
        self.push_manager = push_manager

    async def process_file(self, audio_path: Path) -> int:
        """Process a single audio file through the full pipeline.

        Returns the number of staged_changes rows created.
        """
        logger.info("Pipeline: processing %s", audio_path.name)

        # 1. Create session row
        session_id = await self._create_session(audio_path)

        try:
            # 2. Upload to Gemini Files API
            uploaded_file = await self.gemini.upload_audio(audio_path)

            try:
                # 3. Transcribe via Gemini
                transcript = await self.gemini.transcribe_single(uploaded_file)
                logger.info("Transcription complete: %d chars", len(transcript))

                # 4. Save transcript to session row
                await self.db.db.execute(
                    "UPDATE sessions SET transcript_text = ? WHERE id = ?",
                    (transcript, session_id),
                )
                await self.db.db.commit()

                # 5. Extract entities via Claude
                campaign_name = await self._get_campaign_name()
                known_pcs = await self._get_known_pcs()
                entities = await self.claude.extract_entities(transcript, campaign_name, known_pcs)
                logger.info("Claude extracted %d entities", len(entities))

                # 6. Generate session summary via Claude
                session_number = await self._get_session_number(session_id)
                summary = await self.claude.summarize_session(transcript, campaign_name, session_number)
                await self.db.db.execute(
                    "UPDATE sessions SET summary = ? WHERE id = ?",
                    (summary, session_id),
                )
                await self.db.db.commit()
                logger.info("Session summary generated (%d chars)", len(summary))

                # 7. Write staged_changes
                count = await self._stage_entities(session_id, entities)

                # 8. Mark session as processed
                await self.db.db.execute(
                    "UPDATE sessions SET processed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,),
                )
                await self.db.db.commit()

                # 9. Push session journal to Foundry (non-blocking — failure is logged, not raised)
                await self._push_session(session_id)

            finally:
                # Always delete Gemini uploaded file
                try:
                    await self.gemini.delete_file(uploaded_file)
                except Exception as e:
                    logger.warning("Failed to delete Gemini file: %s", e)

        except Exception:
            # On failure: keep audio, mark session unprocessed
            logger.exception("Pipeline failed for %s", audio_path.name)
            await self.db.db.execute(
                "UPDATE sessions SET processed_at = NULL WHERE id = ?",
                (session_id,),
            )
            await self.db.db.commit()
            raise

        # 9. Delete local audio only after successful extraction
        audio_path.unlink()
        logger.info("Deleted audio: %s", audio_path.name)

        logger.info("Pipeline complete: %d staged changes from %s", count, audio_path.name)
        return count

    async def process_folder(self, folder: Path) -> int:
        """Process a Craig-style folder of per-speaker FLAC files.

        Each .flac is transcribed as an individual speaker, transcripts are merged,
        then entity extraction runs on the combined text.
        """
        logger.info("Pipeline: processing Craig folder %s", folder.name)

        flac_files = sorted(folder.glob("*.flac"))
        if not flac_files:
            logger.warning("No .flac files found in %s", folder)
            return 0

        session_id = await self._create_session(folder)

        try:
            transcripts = []
            uploaded_files = []

            for flac in flac_files:
                # Speaker name from filename (e.g., "1-PlayerName.flac" → "PlayerName")
                speaker = flac.stem.split("-", 1)[-1].strip() if "-" in flac.stem else flac.stem

                uploaded = await self.gemini.upload_audio(flac)
                uploaded_files.append(uploaded)

                text = await self.gemini.transcribe_speaker(uploaded, speaker)
                transcripts.append(f"[{speaker}]\n{text}")

            merged = "\n\n".join(transcripts)
            logger.info("Craig transcription complete: %d speakers, %d chars", len(flac_files), len(merged))

            # Save transcript
            await self.db.db.execute(
                "UPDATE sessions SET transcript_text = ? WHERE id = ?",
                (merged, session_id),
            )
            await self.db.db.commit()

            # Extract entities via Claude
            campaign_name = await self._get_campaign_name()
            known_pcs = await self._get_known_pcs()
            entities = await self.claude.extract_entities(merged, campaign_name, known_pcs)
            logger.info("Claude extracted %d entities", len(entities))

            # Generate session summary
            session_number = await self._get_session_number(session_id)
            summary = await self.claude.summarize_session(merged, campaign_name, session_number)
            await self.db.db.execute(
                "UPDATE sessions SET summary = ? WHERE id = ?",
                (summary, session_id),
            )
            await self.db.db.commit()
            logger.info("Session summary generated (%d chars)", len(summary))

            count = await self._stage_entities(session_id, entities)

            await self.db.db.execute(
                "UPDATE sessions SET processed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            await self.db.db.commit()

            # Push session journal to Foundry
            await self._push_session(session_id)

            # Clean up Gemini files
            for f in uploaded_files:
                try:
                    await self.gemini.delete_file(f)
                except Exception as e:
                    logger.warning("Failed to delete Gemini file %s: %s", f.name, e)

        except Exception:
            logger.exception("Pipeline failed for Craig folder %s", folder.name)
            await self.db.db.execute(
                "UPDATE sessions SET processed_at = NULL WHERE id = ?",
                (session_id,),
            )
            await self.db.db.commit()
            raise

        # Delete audio files after success
        for flac in flac_files:
            flac.unlink()
        logger.info("Deleted %d audio files from %s", len(flac_files), folder.name)

        logger.info("Pipeline complete: %d staged changes from %s", count, folder.name)
        return count

    async def _push_session(self, session_id: int) -> None:
        """Push the session journal to Foundry if push_manager is configured.

        Failures are caught and logged — never propagated to the caller.
        """
        if self.push_manager is None:
            return
        try:
            journal_id = await self.push_manager.push_session(session_id)
            if journal_id:
                logger.info("Foundry: session_id=%d pushed (journal_id=%s)", session_id, journal_id)
        except Exception as exc:
            logger.warning("Foundry: session push failed for session_id=%d: %s", session_id, exc)

    async def _create_session(self, audio_path: Path) -> int:
        """Create a session row with auto-incremented session_number."""
        cursor = await self.db.db.execute(
            "SELECT COALESCE(MAX(session_number), 0) + 1 FROM sessions WHERE campaign_id = ?",
            (self.campaign_id,),
        )
        row = await cursor.fetchone()
        next_number = row[0]

        cursor = await self.db.db.execute(
            "INSERT INTO sessions (campaign_id, session_number, audio_path, processed_at) "
            "VALUES (?, ?, ?, NULL)",
            (self.campaign_id, next_number, str(audio_path)),
        )
        await self.db.db.commit()
        session_id = cursor.lastrowid
        logger.info("Created session #%d (id=%d)", next_number, session_id)
        return session_id

    async def _get_campaign_name(self) -> str:
        """Look up the campaign name for extraction prompts."""
        cursor = await self.db.db.execute(
            "SELECT name FROM campaigns WHERE id = ?", (self.campaign_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else "Unknown Campaign"

    async def _get_session_number(self, session_id: int) -> int:
        """Look up the session_number for a given session id."""
        cursor = await self.db.db.execute(
            "SELECT session_number FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 1

    async def _get_known_pcs(self) -> list[str]:
        """Return list of player character names for this campaign."""
        cursor = await self.db.db.execute(
            "SELECT character_name FROM players WHERE campaign_id = ? AND character_name IS NOT NULL",
            (self.campaign_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def _stage_entities(self, session_id: int, entities: list[dict]) -> int:
        """Write staged_changes rows from extracted entities. Returns row count."""
        count = 0

        for entity in entities:
            name = entity.get("name", "Unknown")
            etype = entity.get("type", "NPC")

            # Public description
            if entity.get("public_description"):
                await self._insert_staged(
                    session_id, name, etype,
                    "description_public", entity["public_description"], "public",
                )
                count += 1

            # Private description
            if entity.get("private_description"):
                await self._insert_staged(
                    session_id, name, etype,
                    "description_private", entity["private_description"], "private",
                )
                count += 1

            # Status label
            if entity.get("status_label"):
                await self._insert_staged(
                    session_id, name, etype,
                    "status_label", entity["status_label"], "public",
                )
                count += 1

            # Motivation (NPC wants)
            if entity.get("motivation"):
                await self._insert_staged(
                    session_id, name, etype,
                    "motivation", entity["motivation"], "private",
                )
                count += 1

            # Appearance
            if entity.get("appearance"):
                await self._insert_staged(
                    session_id, name, etype,
                    "appearance", entity["appearance"], "public",
                )
                count += 1

            # First appearance timestamp
            if entity.get("first_appearance"):
                await self._insert_staged(
                    session_id, name, etype,
                    "first_appearance", entity["first_appearance"], "public",
                )
                count += 1

            # Aliases
            for alias in entity.get("aliases", []):
                await self._insert_staged(
                    session_id, name, etype,
                    "alias", alias, "public",
                )
                count += 1

            # Relationships
            for rel in entity.get("relationships", []):
                value = f"{rel.get('rel_type', '')}:{rel.get('target_name', '')}:{rel.get('citation', '')}"
                await self._insert_staged(
                    session_id, name, etype,
                    "relationship", value, "public",
                )
                count += 1

            # Events (things that happened to/by this entity this session)
            for event in entity.get("events", []):
                ts = event.get("timestamp", "")
                desc = event.get("description", "")
                vis = event.get("visibility", "public")
                if desc:
                    value = f"{ts}:{vis}:{desc}"
                    await self._insert_staged(
                        session_id, name, etype,
                        "event", value, vis,
                    )
                    count += 1

        await self.db.db.commit()
        return count

    async def _insert_staged(
        self,
        session_id: int,
        entity_name: str,
        entity_type: str,
        field_name: str,
        new_value: str,
        visibility: str,
    ) -> None:
        """Insert a single staged_changes row."""
        await self.db.db.execute(
            "INSERT INTO staged_changes "
            "(session_id, entity_id, entity_name, entity_type, change_type, "
            "field_name, new_value, visibility, status) "
            "VALUES (?, NULL, ?, ?, 'create', ?, ?, ?, 'pending')",
            (session_id, entity_name, entity_type, field_name, new_value, visibility),
        )
