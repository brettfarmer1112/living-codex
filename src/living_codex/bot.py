"""Discord bot setup and command registration."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from living_codex.config import CodexConfig
from living_codex.database import CodexDB

logger = logging.getLogger(__name__)


class LivingCodex(commands.Bot):
    """The Living Codex Discord bot."""

    def __init__(self, config: CodexConfig):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.codex_db = CodexDB(config.db_path)

    async def setup_hook(self) -> None:
        await self.codex_db.connect()
        logger.info("Database connected.")

        # Register commands to the configured guild for instant sync
        guild = discord.Object(id=self.config.discord_guild_id)
        self.tree.add_command(codex_group, guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Commands synced to guild %s.", self.config.discord_guild_id)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.codex_db.close()
        logger.info("Database closed.")
        await super().close()


# -- Slash command group: /codex --

codex_group = app_commands.Group(name="codex", description="The Living Codex commands")


@codex_group.command(name="ping", description="Check if the Codex is alive")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(interaction.client.latency * 1000)
    await interaction.response.send_message(
        f"Pong! Latency: {latency_ms}ms", ephemeral=True
    )
