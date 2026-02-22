"""Async wrapper around google-generativeai for audio transcription.

The google-generativeai SDK is synchronous — all blocking calls run in a thread executor
to avoid blocking the Discord event loop. Audio is uploaded via the Files API (never loaded
into memory as raw bytes).

Entity extraction has moved to ai/claude.py (Claude Sonnet).
"""

import asyncio
import logging
from functools import partial
from pathlib import Path

import google.generativeai as genai

from living_codex.ai.prompts import TRANSCRIBE_SINGLE, TRANSCRIBE_SPEAKER

logger = logging.getLogger(__name__)

MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}

MODEL_NAME = "gemini-2.0-flash"


def _detect_mime(path: Path) -> str:
    """Map file extension to MIME type."""
    mime = MIME_MAP.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"Unsupported audio format: {path.suffix}")
    return mime


class GeminiClient:
    """Async Gemini client using the Files API for audio processing."""

    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self._model_name = MODEL_NAME

    async def _run(self, fn, *args, **kwargs):
        """Run a blocking genai call in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def upload_audio(self, path: Path) -> genai.types.File:
        """Upload audio via the Files API. Returns a File object with URI."""
        mime = _detect_mime(path)
        logger.info("Uploading %s (%s) to Gemini Files API...", path.name, mime)
        uploaded = await self._run(genai.upload_file, str(path), mime_type=mime)
        logger.info("Uploaded: %s", uploaded.name)
        return uploaded

    async def transcribe_single(self, audio_file: genai.types.File) -> str:
        """Transcribe a single audio file with speaker identification."""
        model = genai.GenerativeModel(self._model_name)
        response = await self._run(model.generate_content, [TRANSCRIBE_SINGLE, audio_file])
        return response.text

    async def transcribe_speaker(self, audio_file: genai.types.File, speaker_name: str) -> str:
        """Transcribe audio from a specific speaker (Craig per-track mode)."""
        prompt = TRANSCRIBE_SPEAKER.format(speaker_name=speaker_name)
        model = genai.GenerativeModel(self._model_name)
        response = await self._run(model.generate_content, [prompt, audio_file])
        return response.text

    async def delete_file(self, file: genai.types.File) -> None:
        """Delete an uploaded file from Gemini storage."""
        logger.info("Deleting Gemini file: %s", file.name)
        await self._run(genai.delete_file, file.name)
