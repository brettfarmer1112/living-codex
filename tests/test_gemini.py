"""Tests for the Gemini client wrapper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from living_codex.ai.gemini import GeminiClient, MODEL_NAME, _detect_mime


@pytest.fixture
def gemini():
    with patch("living_codex.ai.gemini.genai") as mock_genai:
        client = GeminiClient(api_key="test-key")
        yield client, mock_genai


@pytest.mark.asyncio
async def test_upload_uses_files_api(gemini, tmp_path):
    """Audio must go through the Files API, not raw bytes in generate_content."""
    client, mock_genai = gemini
    mock_genai.upload_file.return_value = MagicMock(name="files/abc123")

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")
    result = await client.upload_audio(audio)

    mock_genai.upload_file.assert_called_once_with(str(audio), mime_type="audio/mpeg")
    assert result == mock_genai.upload_file.return_value


@pytest.mark.asyncio
async def test_model_is_flash():
    """Model must be gemini-2.0-flash, never 1.5-flash."""
    assert MODEL_NAME == "gemini-2.0-flash"
    with patch("living_codex.ai.gemini.genai"):
        client = GeminiClient(api_key="test-key")
        assert client._model_name == "gemini-2.0-flash"


def test_detect_mime_mp3():
    assert _detect_mime(Path("test.mp3")) == "audio/mpeg"


def test_detect_mime_flac():
    assert _detect_mime(Path("test.flac")) == "audio/flac"


def test_detect_mime_unsupported():
    with pytest.raises(ValueError, match="Unsupported audio format"):
        _detect_mime(Path("test.m4a"))
