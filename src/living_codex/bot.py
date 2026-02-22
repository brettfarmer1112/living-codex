"""Discord bot setup and Cog registration."""

import logging

import discord
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
        from living_codex.commands.codex import CodexCommands

        await self.add_cog(CodexCommands(self))
        guild = discord.Object(id=self.config.discord_guild_id)
        await self.tree.sync(guild=guild)
        logger.info("Commands synced to guild %s.", self.config.discord_guild_id)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.codex_db.close()
        logger.info("Database closed.")
        await super().close()
