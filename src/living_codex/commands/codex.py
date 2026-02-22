"""CodexCommands Cog — owns the /codex slash command group.

Moving the group into a Cog gives every callback clean access to
self.bot.codex_db without module-level globals or circular imports.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from living_codex.formatter import (
    build_candidates_select,
    build_entity_embed,
    build_full_detail_view,
)
from living_codex.search import search

logger = logging.getLogger(__name__)


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
            # Resolve campaign name for the "Seen in:" bullet
            entity_with_campaign = await self._with_campaign_name(entity)
            embed = build_entity_embed(entity_with_campaign)
            view = build_full_detail_view(entity_with_campaign)
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True
            )

        elif result.kind == "candidates":
            select = build_candidates_select(result.candidates)
            # Stash candidates keyed by str(id) for the select callback lookup
            candidates_by_id = {str(c["id"]): c for c in result.candidates}

            async def _on_select(select_interaction: discord.Interaction) -> None:
                chosen_id = select.values[0]
                entity = candidates_by_id.get(chosen_id)
                if entity is None:
                    await select_interaction.response.send_message(
                        "Entity not found — please try again.", ephemeral=True
                    )
                    return
                entity_with_campaign = await self._with_campaign_name(entity)
                embed = build_entity_embed(entity_with_campaign)
                view = build_full_detail_view(entity_with_campaign)
                # Fresh interaction from the select — use response, not followup
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
    # Helpers
    # ------------------------------------------------------------------

    async def _with_campaign_name(self, entity: dict) -> dict:
        """Return entity dict with 'campaign_name' resolved from the DB."""
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
