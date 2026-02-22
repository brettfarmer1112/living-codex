"""Discord bot setup, Cog and command registration."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from living_codex.config import CodexConfig
from living_codex.database import CodexDB

logger = logging.getLogger(__name__)

_QUEUE_DRAIN_INTERVAL = 300  # seconds between drain attempts


class LivingCodex(commands.Bot):
    """The Living Codex Discord bot."""

    def __init__(self, config: CodexConfig):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.codex_db = CodexDB(config.db_path)

        self.ai_client = None       # GeminiProClient | ClaudeClient | None
        self.foundry_client = None  # FoundryClient | None
        self.push_manager = None    # PushManager | None

    async def setup_hook(self) -> None:
        await self.codex_db.connect()
        logger.info("Database connected.")

        # Instantiate AI client via router (model name prefix selects SDK)
        from living_codex.ai.router import create_ai_client
        try:
            self.ai_client = create_ai_client(self.config.ai_model, self.config)
        except ValueError as exc:
            logger.warning("AI client disabled: %s", exc)

        # Instantiate Foundry client + push manager if configured
        if self.config.foundry_url and self.config.foundry_api_key:
            from living_codex.sync.foundry import FoundryClient
            from living_codex.sync.push import PushManager
            self.foundry_client = FoundryClient(
                base_url=self.config.foundry_url,
                api_key=self.config.foundry_api_key,
            )
            self.push_manager = PushManager(self.codex_db, self.foundry_client)
            self.loop.create_task(self._queue_drain_loop())
            logger.info("Foundry client initialized (%s). Queue drain every %ds.",
                        self.config.foundry_url, _QUEUE_DRAIN_INTERVAL)
        else:
            logger.info("Foundry URL/key not set — Foundry sync disabled.")

        from living_codex.commands.codex import CodexCommands

        await self.add_cog(CodexCommands(self))

        # Register the simple app command group used by some integrations
        guild = discord.Object(id=self.config.discord_guild_id)
        self.tree.add_command(codex_group, guild=guild)
        # Copy any global commands (from cogs) to guild and sync
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Commands synced to guild %s.", self.config.discord_guild_id)

        # Start the Scribe audio watcher if Gemini (transcription) and AI client (extraction) are configured
        if self.config.gemini_api_key and self.ai_client is not None:
            from living_codex.ai.gemini import GeminiClient
            from living_codex.scribe.watcher import AudioWatcher

            gemini = GeminiClient(self.config.gemini_api_key)
            watcher = AudioWatcher(
                input_dir=self.config.input_dir,
                db=self.codex_db,
                gemini=gemini,
                claude=self.ai_client,
                campaign_id=self.config.default_campaign_id,
                push_manager=self.push_manager,
            )
            self.loop.create_task(watcher.watch())
            logger.info("AudioWatcher started on %s.", self.config.input_dir)
        elif self.config.gemini_api_key:
            logger.info("Gemini key set but no AI client for extraction — Scribe pipeline disabled.")
        else:
            logger.info("Gemini API key not set — Scribe pipeline disabled.")

    async def _queue_drain_loop(self) -> None:
        """Background task: drain the sync queue every 5 minutes."""
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(_QUEUE_DRAIN_INTERVAL)
            if self.push_manager is not None:
                try:
                    succeeded, failed = await self.push_manager.drain_queue()
                    if succeeded or failed:
                        logger.info("Queue drain: %d synced, %d failed.", succeeded, failed)
                except Exception as exc:
                    logger.error("Queue drain error: %s", exc)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def close(self) -> None:
        if self.foundry_client is not None:
            await self.foundry_client.close()
        await self.codex_db.close()
        logger.info("Database and Foundry client closed.")
        await super().close()


# -- Slash command group: /codex --

codex_group = app_commands.Group(name="codex", description="The Living Codex commands")


@codex_group.command(name="ping", description="Check if the Codex is alive")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(interaction.client.latency * 1000)
    await interaction.response.send_message(
        f"Pong! Latency: {latency_ms}ms", ephemeral=True
    )
