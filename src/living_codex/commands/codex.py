"""CodexCommands Cog — owns the /codex slash command group.

Moving the group into a Cog gives every callback clean access to
self.bot.codex_db without module-level globals or circular imports.
"""

from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from living_codex.formatter import (
    build_candidates_select,
    build_entity_embed,
    build_full_detail_embed,
    build_full_detail_view,
)
from living_codex.search import search

_MAX_UPLOAD_BYTES = 1_000_000  # 1 MB lore doc limit
_TRANSCRIPT_CHAR_BUDGET = 80_000  # Max chars for raw transcripts in query context

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Context formatting helpers for /codex query
# ------------------------------------------------------------------

def _format_entities_for_context(entities: list) -> str:
    """Format entity rows into compact LLM-readable profiles."""
    if not entities:
        return ""
    lines = []
    for e in entities:
        status = f" [{e.get('status_label', 'Unknown')}]" if e.get("status_label") else ""
        header = f"- {e['name']} ({e.get('type', '?')}){status}"
        desc = e.get("description_public") or e.get("description_private") or ""
        if desc:
            header += f"\n  {desc}"
        lines.append(header)
    return "\n".join(lines)


def _format_relationships_for_context(rels: list[dict]) -> str:
    """Format relationship rows into compact arrow notation."""
    if not rels:
        return ""
    lines = []
    for r in rels:
        line = f"- {r['source_name']} -[{r['rel_type']}]-> {r['target_name']}"
        if r.get("citation"):
            line += f" ({r['citation']})"
        lines.append(line)
    return "\n".join(lines)


def _format_summaries_for_context(summaries: list[dict]) -> str:
    """Format session summaries with session headers."""
    if not summaries:
        return ""
    parts = []
    for s in summaries:
        parts.append(f"=== Session {s['session_number']} ===\n{s['summary']}")
    return "\n\n".join(parts)


def _format_lore_for_context(lore_docs: list[dict]) -> str:
    """Format lore documents with title headers."""
    if not lore_docs:
        return ""
    parts = []
    for doc in lore_docs:
        parts.append(f"--- {doc['title']} ---\n{doc['content']}")
    return "\n\n".join(parts)


def _format_transcripts_for_context(sessions: list[dict]) -> str:
    """Concatenate transcripts with session headers, truncating to budget."""
    if not sessions:
        return ""
    parts = []
    total = 0
    for s in sessions:
        header = f"=== Session {s['session_number']} ==="
        text = s["transcript_text"]
        entry = f"{header}\n{text}"
        if total + len(entry) > _TRANSCRIPT_CHAR_BUDGET:
            remaining = _TRANSCRIPT_CHAR_BUDGET - total
            if remaining > 100:
                parts.append(f"{header}\n{text[:remaining]}…[truncated]")
            break
        parts.append(entry)
        total += len(entry)
    return "\n\n".join(parts)


class CodexCommands(commands.Cog):
    """All /codex slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    codex = app_commands.Group(name="codex", description="The Living Codex commands")

    # ------------------------------------------------------------------
    # /codex ping
    # ------------------------------------------------------------------

    @codex.command(name="ping", description="Check if the Codex is alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(interaction.client.latency * 1000)
        await interaction.response.send_message(
            f"Pong! Latency: {latency_ms}ms", ephemeral=True
        )

    # ------------------------------------------------------------------
    # /codex check
    # ------------------------------------------------------------------

    @codex.command(name="check", description="Look up an entity in the Codex")
    @app_commands.describe(query="Name or alias to search for")
    async def check(self, interaction: discord.Interaction, query: str) -> None:
        result = await search(self.bot.codex_db, query)  # type: ignore[attr-defined]

        if result.kind == "direct":
            entity = result.entity
            entity_enriched = await self._enrich_entity(entity)
            embed = build_entity_embed(entity_enriched)
            view = self._build_view_with_callback(entity_enriched)
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True
            )

        elif result.kind == "candidates":
            select = build_candidates_select(result.candidates)
            candidates_by_id = {str(c["id"]): c for c in result.candidates}

            async def _on_select(select_interaction: discord.Interaction) -> None:
                chosen_id = select.values[0]
                entity = candidates_by_id.get(chosen_id)
                if entity is None:
                    await select_interaction.response.send_message(
                        "Entity not found — please try again.", ephemeral=True
                    )
                    return
                entity_enriched = await self._enrich_entity(entity)
                embed = build_entity_embed(entity_enriched)
                view = self._build_view_with_callback(entity_enriched)
                await select_interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )

            select.callback = _on_select
            view = discord.ui.View(timeout=300)
            view.add_item(select)
            await interaction.response.send_message(
                "Did you mean…?", view=view, ephemeral=True
            )

        else:  # kind == "none"
            await interaction.response.send_message(
                f'No results found for "{query}".', ephemeral=True
            )

    # ------------------------------------------------------------------
    # /codex lastsession
    # ------------------------------------------------------------------

    @codex.command(name="lastsession", description="Show a narrative summary of the most recent session")
    async def lastsession(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        db = self.bot.codex_db  # type: ignore[attr-defined]
        campaign_id = self.bot.config.default_campaign_id  # type: ignore[attr-defined]

        session = await db.get_latest_session(campaign_id)
        if session is None:
            await interaction.followup.send("No processed sessions found.")
            return

        # Fast path: summary already cached
        if session["summary"]:
            summary_text = session["summary"]
        else:
            # Generate summary on demand via Claude
            claude = getattr(self.bot, "ai_client", None)
            if claude is None:
                await interaction.followup.send(
                    "AI client not configured — cannot generate session summary."
                )
                return
            if not session["transcript_text"]:
                await interaction.followup.send(
                    "No transcript available for this session."
                )
                return

            cursor = await db.db.execute(
                "SELECT name FROM campaigns WHERE id = ?", (campaign_id,)
            )
            row = await cursor.fetchone()
            campaign_name = row["name"] if row else "Unknown Campaign"

            summary_text = await claude.summarize_session(
                session["transcript_text"],
                campaign_name,
                session["session_number"],
            )
            # Cache for future calls
            await db.db.execute(
                "UPDATE sessions SET summary = ? WHERE id = ?",
                (summary_text, session["id"]),
            )
            await db.db.commit()

        title = f"Session {session['session_number']} Summary"
        session_num = session["session_number"]
        await _send_long_response(
            interaction, summary_text,
            prefix=f"**{title}**\n\n",
            filename=f"session_{session_num}_summary.md",
        )

    # ------------------------------------------------------------------
    # /codex query
    # ------------------------------------------------------------------

    @codex.command(name="query", description="Ask a question about the campaign")
    @app_commands.describe(question="Your question about the campaign")
    async def query(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.defer(ephemeral=True)

        ai = getattr(self.bot, "ai_client", None)
        if ai is None:
            await interaction.followup.send(
                "AI client not configured — /codex query is unavailable.",
                ephemeral=True,
            )
            return

        db = self.bot.codex_db  # type: ignore[attr-defined]
        campaign_id = self.bot.config.default_campaign_id  # type: ignore[attr-defined]

        # Fire all DB reads concurrently — they're independent
        async def _campaign_name() -> str:
            cursor = await db.db.execute(
                "SELECT name FROM campaigns WHERE id = ?", (campaign_id,)
            )
            row = await cursor.fetchone()
            return row["name"] if row else "Unknown Campaign"

        (
            campaign_name,
            entities_raw,
            rels_raw,
            summaries_raw,
            lore_raw,
            sessions_data,
        ) = await asyncio.gather(
            _campaign_name(),
            db.get_all_entities(campaign_id),
            db.get_all_relationships(campaign_id),
            db.get_all_session_summaries(campaign_id),
            db.get_all_lore_docs(campaign_id),
            db.get_unsummarized_transcripts(campaign_id),
        )

        # Format context sections
        entities_text = _format_entities_for_context(
            [dict(e) for e in entities_raw]
        )
        rels_text = _format_relationships_for_context(rels_raw)
        summaries_text = _format_summaries_for_context(summaries_raw)
        lore_text = _format_lore_for_context(lore_raw)
        transcripts_text = _format_transcripts_for_context(sessions_data)

        answer = await ai.query(
            question,
            campaign_name,
            entities=entities_text,
            relationships=rels_text,
            summaries=summaries_text,
            lore_docs=lore_text,
            transcripts=transcripts_text,
        )

        await _send_long_response(
            interaction, answer,
            prefix=f"**Q: {question}**\n\n",
            ephemeral=True,
            filename="query_result.md",
        )

    # ------------------------------------------------------------------
    # /codex syncstatus
    # ------------------------------------------------------------------

    @codex.command(name="syncstatus", description="Show Foundry sync status: synced, queued, and conflicted entries")
    async def syncstatus(self, interaction: discord.Interaction) -> None:
        push_manager = getattr(self.bot, "push_manager", None)
        if push_manager is None:
            await interaction.response.send_message(
                "Foundry integration is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        db = self.bot.codex_db  # type: ignore[attr-defined]

        # Count synced entities (foundry_id IS NOT NULL)
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM entities WHERE foundry_id IS NOT NULL"
        )
        row = await cursor.fetchone()
        synced_count = row[0] if row else 0

        # Queue breakdown
        cursor = await db.db.execute(
            "SELECT action, COUNT(*) as cnt FROM sync_queue GROUP BY action"
        )
        queue_rows = await cursor.fetchall()
        queued_total = 0
        conflict_count = 0
        for qrow in queue_rows:
            if qrow["action"] == "conflict":
                conflict_count = qrow["cnt"]
            else:
                queued_total += qrow["cnt"]

        # List conflicting entity names
        conflict_names: list[str] = []
        if conflict_count:
            cursor = await db.db.execute(
                "SELECT e.name FROM sync_queue sq "
                "JOIN entities e ON sq.entity_id = e.id "
                "WHERE sq.action = 'conflict'"
            )
            conflict_rows = await cursor.fetchall()
            conflict_names = [r["name"] for r in conflict_rows]

        lines = [
            f"**Foundry Sync Status**",
            f"• Synced: {synced_count} entities",
            f"• Queued: {queued_total} pending",
            f"• Conflicts: {conflict_count}",
        ]
        if conflict_names:
            names_str = ", ".join(conflict_names)
            lines.append(f"  *Conflicting: {names_str}*")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # /codex sync
    # ------------------------------------------------------------------

    @codex.command(name="sync", description="Push entities to Foundry VTT")
    @app_commands.describe(
        entity_name="Specific entity to sync (leave blank to sync all unsynced)",
        force="Override conflict guard and force-overwrite manual edits",
    )
    async def sync(
        self,
        interaction: discord.Interaction,
        entity_name: str = "",
        force: bool = False,
    ) -> None:
        push_manager = getattr(self.bot, "push_manager", None)
        if push_manager is None:
            await interaction.response.send_message(
                "Foundry integration is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        db = self.bot.codex_db  # type: ignore[attr-defined]
        campaign_id = self.bot.config.default_campaign_id  # type: ignore[attr-defined]

        from living_codex.sync.foundry import FoundryOfflineError
        from living_codex.sync.guard import ConflictDetected

        if entity_name:
            # Single entity sync
            entity_row = await db.get_entity_by_name(entity_name, campaign_id)
            if entity_row is None:
                await interaction.followup.send(
                    f'Entity "{entity_name}" not found.', ephemeral=True
                )
                return
            entity_ids = [entity_row["id"]]
        else:
            # Bulk: all entities not yet synced (no foundry_id)
            cursor = await db.db.execute(
                "SELECT id FROM entities WHERE campaign_id = ? AND foundry_id IS NULL",
                (campaign_id,),
            )
            rows = await cursor.fetchall()
            entity_ids = [r["id"] for r in rows]

        if not entity_ids:
            await interaction.followup.send("Nothing to sync — all entities already synced.", ephemeral=True)
            return

        results: list[str] = []
        for eid in entity_ids:
            cursor = await db.db.execute("SELECT name FROM entities WHERE id = ?", (eid,))
            row = await cursor.fetchone()
            ename = row["name"] if row else str(eid)
            try:
                fid = await push_manager.push_entity(eid, force=force)
                if fid:
                    results.append(f"✅ {ename}")
                else:
                    results.append(f"⏳ {ename} (queued)")
            except ConflictDetected as exc:
                results.append(f"⚠️ {exc.entity_name} — conflict, use force:True to override")
            except FoundryOfflineError:
                results.append(f"📵 {ename} — Foundry offline, queued")
            except Exception as exc:
                results.append(f"❌ {ename} — {exc}")

        summary = "\n".join(results) or "No entities processed."
        chunks = _split_text(summary, 1900)
        await interaction.followup.send(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    # ------------------------------------------------------------------
    # /codex upload
    # ------------------------------------------------------------------

    @codex.command(name="upload", description="Upload a .md or .txt lore document to the Codex")
    @app_commands.describe(
        attachment="Markdown or text file to upload",
        title="Journal entry title (defaults to filename without extension)",
    )
    async def upload(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        title: str = "",
    ) -> None:
        if not attachment.filename.endswith((".md", ".txt")):
            await interaction.response.send_message(
                "Only .md and .txt files are supported.", ephemeral=True
            )
            return

        if attachment.size > _MAX_UPLOAD_BYTES:
            await interaction.response.send_message(
                f"File too large ({attachment.size} bytes). Max 1 MB.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        raw_bytes = await attachment.read()
        raw_content = raw_bytes.decode("utf-8", errors="replace")

        doc_title = title.strip() or attachment.filename.rsplit(".", 1)[0]

        # Always store in the Codex database
        db = self.bot.codex_db  # type: ignore[attr-defined]
        campaign_id = self.bot.config.default_campaign_id  # type: ignore[attr-defined]
        await db.insert_lore_doc(campaign_id, doc_title, raw_content)

        # Optionally push to Foundry if configured
        push_manager = getattr(self.bot, "push_manager", None)
        if push_manager is None:
            await interaction.followup.send(
                f'Lore doc "{doc_title}" saved to Codex database.',
                ephemeral=True,
            )
            return

        from living_codex.sync.foundry import FoundryOfflineError
        try:
            journal_id = await push_manager.push_lore_doc(doc_title, raw_content)
            if journal_id:
                await interaction.followup.send(
                    f'Lore doc "{doc_title}" saved to Codex database and uploaded to Foundry (id={journal_id[:8]}…)',
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f'Lore doc "{doc_title}" saved to Codex database. Foundry is offline — Foundry upload skipped.',
                    ephemeral=True,
                )
        except FoundryOfflineError as exc:
            await interaction.followup.send(
                f'Lore doc "{doc_title}" saved to Codex database. Foundry offline: {exc}',
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _enrich_entity(self, entity: dict) -> dict:
        """Return entity dict enriched with campaign_name and resolved session numbers."""
        db = self.bot.codex_db  # type: ignore[attr-defined]

        # Campaign name
        campaign_id = entity.get("campaign_id")
        campaign_name = "Unknown"
        if campaign_id is not None:
            cursor = await db.db.execute(
                "SELECT name FROM campaigns WHERE id = ?", (campaign_id,)
            )
            row = await cursor.fetchone()
            if row:
                campaign_name = row["name"]

        # Resolve session numbers for first/last seen
        first_num = None
        last_num = None
        if entity.get("first_seen_session_id"):
            first_num = await db.get_session_number(entity["first_seen_session_id"])
        if entity.get("last_seen_session_id"):
            last_num = await db.get_session_number(entity["last_seen_session_id"])

        return {
            **entity,
            "campaign_name": campaign_name,
            "first_seen_session_number": first_num,
            "last_seen_session_number": last_num,
            "foundry_url": getattr(self.bot.config, "foundry_url", ""),  # type: ignore[attr-defined]
        }

    def _build_view_with_callback(self, entity_enriched: dict) -> discord.ui.View:
        """Build 'View Full' button that loads events + relationships on click."""
        view = build_full_detail_view(entity_enriched)
        button = view.children[0]  # the single button added in formatter

        async def _on_click(interaction: discord.Interaction) -> None:
            db = self.bot.codex_db  # type: ignore[attr-defined]
            entity_id = entity_enriched.get("id")

            # Fetch approved events
            events_rows = []
            if entity_id:
                events_rows = await db.get_entity_events(entity_id, approved_only=True)
            events = [dict(r) for r in events_rows]

            # Fetch relationships with target names
            rels = []
            if entity_id:
                cursor = await db.db.execute(
                    "SELECT r.rel_type, r.citation, e.name AS target_name, "
                    "s.session_number "
                    "FROM relationships r "
                    "JOIN entities e ON r.target_id = e.id "
                    "LEFT JOIN sessions s ON r.citation LIKE '%Session%' "
                    "WHERE r.source_id = ?",
                    (entity_id,),
                )
                rels = [dict(r) for r in await cursor.fetchall()]

            full_embed = build_full_detail_embed(entity_enriched, events, rels)
            await interaction.response.send_message(embed=full_embed, ephemeral=True)

        button.callback = _on_click
        return view

    async def _with_campaign_name(self, entity: dict) -> dict:
        """Legacy helper — returns entity dict with 'campaign_name' resolved from the DB."""
        campaign_id = entity.get("campaign_id")
        campaign_name = "Unknown"
        if campaign_id is not None:
            cursor = await self.bot.codex_db.db.execute(  # type: ignore[attr-defined]
                "SELECT name FROM campaigns WHERE id = ?", (campaign_id,)
            )
            row = await cursor.fetchone()
            if row:
                campaign_name = row["name"]
        return {**entity, "campaign_name": campaign_name}


async def _send_long_response(
    interaction: discord.Interaction,
    text: str,
    *,
    prefix: str = "",
    ephemeral: bool = False,
    filename: str = "response.md",
) -> None:
    """Send a response as a single message if short, or preview + .md file if long."""
    full_content = prefix + text
    if len(full_content) <= 1900:
        await interaction.followup.send(full_content, ephemeral=ephemeral)
        return

    # Build a preview: prefix + first paragraph (up to ~500 chars)
    first_para_end = text.find("\n\n")
    if first_para_end == -1 or first_para_end > 500:
        first_para_end = 500
    preview = prefix + text[:first_para_end] + "…\n\n*Full response attached below.*"

    # Attach the complete response as a .md file
    file_bytes = text.encode("utf-8")
    file = discord.File(io.BytesIO(file_bytes), filename=filename)
    await interaction.followup.send(preview, file=file, ephemeral=ephemeral)


def _split_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of at most max_len characters, splitting on newlines where possible."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find last newline before max_len
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
