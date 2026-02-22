"""Configuration via pydantic-settings with CODEX_ env prefix."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class CodexConfig(BaseSettings):
    model_config = {
        "env_prefix": "CODEX_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # tolerate unknown CODEX_* vars in .env
    }

    # Discord
    discord_token: str
    discord_guild_id: int
    gm_role_id: int
    gm_channel_id: int
    player_channel_id: int

    # Gemini
    gemini_api_key: str = ""
    gemini_pro_model: str = "gemini-3.1-pro-preview"

    # Anthropic / Claude — fallback if Gemini key not set
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CODEX_CLAUDE_API_KEY", "CODEX_ANTHROPIC_API_KEY"),
    )
    anthropic_model: str = "claude-sonnet-4-6"

    # Database
    db_path: Path = Path("./data/codex.db")

    # Audio input directory
    input_dir: Path = Path("./inputs")

    # Foundry VTT (Phase 5)
    foundry_url: str = ""
    foundry_api_key: str = ""

    # Scribe pipeline (Phase 3)
    default_campaign_id: int = 1
    rclone_gdrive_path: str = "living-codex-transcriptions"
    gdrive_craig_path: str = ""
