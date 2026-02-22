"""File watcher for audio input directory. Detects new files/folders and triggers the pipeline."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchfiles import Change, awatch

from living_codex.ai.gemini import GeminiClient
from living_codex.database import CodexDB
from living_codex.scribe.pipeline import AUDIO_EXTENSIONS, ScribePipeline

if TYPE_CHECKING:
    from living_codex.sync.push import PushManager

logger = logging.getLogger(__name__)

# Wait after first detection to avoid processing partial writes (rclone transfers)
DEBOUNCE_SECONDS = 10


class AudioWatcher:
    """Watches input directory for new audio files and Craig folders."""

    def __init__(
        self,
        input_dir: Path,
        db: CodexDB,
        gemini: GeminiClient,
        claude: Any,
        campaign_id: int,
        push_manager: "PushManager | None" = None,
    ):
        self.input_dir = input_dir
        self.db = db
        self.gemini = gemini
        self.claude = claude
        self.campaign_id = campaign_id
        self.push_manager = push_manager

    async def watch(self) -> None:
        """Watch the input directory forever, processing new audio as it arrives."""
        logger.info("AudioWatcher: monitoring %s", self.input_dir)

        async for changes in awatch(self.input_dir):
            for change_type, raw_path in changes:
                path = Path(raw_path)

                if change_type not in (Change.added, Change.modified):
                    continue

                if path.suffix.lower() in AUDIO_EXTENSIONS:
                    await self._handle_file(path)
                elif path.is_dir():
                    await self._handle_folder(path)

    async def _handle_file(self, path: Path) -> None:
        """Debounce and process a single audio file."""
        logger.info("Detected audio file: %s (waiting %ds for transfer to complete)",
                     path.name, DEBOUNCE_SECONDS)
        await asyncio.sleep(DEBOUNCE_SECONDS)

        if not path.exists():
            logger.warning("File disappeared after debounce: %s", path.name)
            return

        pipeline = ScribePipeline(
            self.db, self.gemini, self.claude, self.campaign_id,
            push_manager=self.push_manager,
        )
        try:
            count = await pipeline.process_file(path)
            logger.info("Watcher: %d staged changes from %s", count, path.name)
        except Exception:
            logger.exception("Watcher: failed to process %s", path.name)

    async def _handle_folder(self, path: Path) -> None:
        """Debounce and process a Craig-style folder of per-speaker FLACs."""
        flac_files = list(path.glob("*.flac"))
        if not flac_files:
            return  # Not a Craig folder

        logger.info("Detected Craig folder: %s (%d FLAC files, waiting %ds)",
                     path.name, len(flac_files), DEBOUNCE_SECONDS)
        await asyncio.sleep(DEBOUNCE_SECONDS)

        if not path.exists():
            logger.warning("Folder disappeared after debounce: %s", path.name)
            return

        pipeline = ScribePipeline(
            self.db, self.gemini, self.claude, self.campaign_id,
            push_manager=self.push_manager,
        )
        try:
            count = await pipeline.process_folder(path)
            logger.info("Watcher: %d staged changes from Craig folder %s", count, path.name)
        except Exception:
            logger.exception("Watcher: failed to process Craig folder %s", path.name)
