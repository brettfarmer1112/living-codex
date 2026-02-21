"""Configuration via pydantic-settings with CODEX_ env prefix."""

from pathlib import Path

from pydantic_settings import BaseSettings


class CodexConfig(BaseSettings):
    model_config = {"env_prefix": "CODEX_"}

    # Discord
    discord_token: str
    discord_guild_id: int
    gm_role_id: int
    gm_channel_id: int
    player_channel_id: int

    # Gemini
    gemini_api_key: str = ""

    # Database
    db_path: Path = Path("./data/codex.db")

    # Audio input directory
    input_dir: Path = Path("./inputs")

    # Foundry VTT (Phase 5)
    foundry_url: str = ""
    foundry_api_key: str = ""

    # Craig/rclone (Phase 3)
    gdrive_craig_path: str = ""
