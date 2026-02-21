"""Entry point for the Living Codex bot."""

import asyncio
import logging
import signal
import sys

from living_codex.bot import LivingCodex
from living_codex.config import CodexConfig


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet noisy discord.py internals
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        config = CodexConfig()
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        sys.exit(1)

    bot = LivingCodex(config)

    # Graceful shutdown on SIGINT/SIGTERM
    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        asyncio.get_event_loop().create_task(bot.close())

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        bot.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, exiting.")


if __name__ == "__main__":
    main()
