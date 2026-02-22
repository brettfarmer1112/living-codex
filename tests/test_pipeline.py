"""Tests for the Scribe pipeline — mocked Gemini + Claude, real SQLite."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from living_codex.database import CodexDB
from living_codex.scribe.pipeline import ScribePipeline

_ENTITY_FIXTURE = {
    "name": "The Dungeon",
    "type": "Location",
    "aliases": ["Dark Caves"],
    "public_description": "A sprawling underground complex.",
    "private_description": "Contains the Lich's phylactery.",
    "motivation": "",
    "appearance": "",
    "first_appearance": "",
    "relationships": [
        {"target_name": "The Lich", "rel_type": "Contains", "citation": "Session 1"}
    ],
    "status_label": "Active",
    "events": [],
}


@pytest.fixture
def mock_gemini():
    """A mock GeminiClient — transcription only (no extract_entities)."""
    gemini = AsyncMock()
    gemini.upload_audio.return_value = MagicMock(name="files/test123")
    gemini.transcribe_single.return_value = "[00:00] GM: You enter the dungeon."
    gemini.delete_file.return_value = None
    return gemini


@pytest.fixture
def mock_claude():
    """A mock ClaudeClient with sensible entity extraction defaults."""
    claude = AsyncMock()
    claude.extract_entities.return_value = [_ENTITY_FIXTURE]
    claude.summarize_session.return_value = "The party entered the dungeon."
    return claude


@pytest.fixture
async def pipeline_db(tmp_path):
    """Fresh DB with a seeded campaign."""
    db = CodexDB(tmp_path / "test.db")
    await db.connect()
    await db.get_or_create_campaign("Test Campaign", "PbtA")
    yield db
    await db.close()


@pytest.fixture
def audio_file(tmp_path):
    """Create a fake audio file for testing."""
    f = tmp_path / "session01.mp3"
    f.write_bytes(b"fake audio data")
    return f


@pytest.mark.asyncio
async def test_single_file_creates_session(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Processing a file should create a sessions row."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute("SELECT * FROM sessions WHERE campaign_id = 1")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["session_number"] == 1
    assert rows[0]["processed_at"] is not None


@pytest.mark.asyncio
async def test_session_number_auto_increments(pipeline_db, mock_gemini, mock_claude, tmp_path):
    """Session numbers should auto-increment per campaign."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)

    f1 = tmp_path / "s1.mp3"
    f1.write_bytes(b"audio1")
    await pipeline.process_file(f1)

    f2 = tmp_path / "s2.mp3"
    f2.write_bytes(b"audio2")
    await pipeline.process_file(f2)

    cursor = await pipeline_db.db.execute(
        "SELECT session_number FROM sessions ORDER BY session_number"
    )
    rows = await cursor.fetchall()
    assert [r[0] for r in rows] == [1, 2]


@pytest.mark.asyncio
async def test_staged_changes_per_field(pipeline_db, mock_gemini, mock_claude, audio_file):
    """One entity with description_public, description_private, status, 1 alias, 1 relationship = 5 rows."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    count = await pipeline.process_file(audio_file)

    assert count == 5  # pub desc + priv desc + status + 1 alias + 1 relationship

    cursor = await pipeline_db.db.execute(
        "SELECT field_name FROM staged_changes ORDER BY field_name"
    )
    rows = await cursor.fetchall()
    fields = [r[0] for r in rows]
    assert "description_public" in fields
    assert "description_private" in fields
    assert "status_label" in fields
    assert "alias" in fields
    assert "relationship" in fields


@pytest.mark.asyncio
async def test_alias_staged_correctly(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Aliases should produce staged_changes with field_name='alias'."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute(
        "SELECT entity_name, new_value, visibility FROM staged_changes WHERE field_name = 'alias'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["entity_name"] == "The Dungeon"
    assert rows[0]["new_value"] == "Dark Caves"
    assert rows[0]["visibility"] == "public"


@pytest.mark.asyncio
async def test_relationship_format(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Relationship new_value should be 'rel_type:target_name:citation'."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute(
        "SELECT new_value FROM staged_changes WHERE field_name = 'relationship'"
    )
    row = await cursor.fetchone()
    parts = row[0].split(":")
    assert parts[0] == "Contains"
    assert parts[1] == "The Lich"
    assert parts[2] == "Session 1"


@pytest.mark.asyncio
async def test_private_description_visibility(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Private descriptions must have visibility='private'."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute(
        "SELECT visibility FROM staged_changes WHERE field_name = 'description_private'"
    )
    row = await cursor.fetchone()
    assert row["visibility"] == "private"


@pytest.mark.asyncio
async def test_audio_deleted_after_success(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Audio file should be deleted after successful processing."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    assert not audio_file.exists()


@pytest.mark.asyncio
async def test_audio_preserved_on_failure(pipeline_db, mock_claude, audio_file):
    """Audio file should NOT be deleted if Gemini fails."""
    bad_gemini = AsyncMock()
    bad_gemini.upload_audio.side_effect = RuntimeError("Gemini API down")

    pipeline = ScribePipeline(pipeline_db, bad_gemini, mock_claude, campaign_id=1)

    with pytest.raises(RuntimeError, match="Gemini API down"):
        await pipeline.process_file(audio_file)

    assert audio_file.exists()  # Audio preserved for retry


@pytest.mark.asyncio
async def test_gemini_file_deleted_after_processing(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Gemini uploaded file should be cleaned up after processing."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    mock_gemini.delete_file.assert_called_once()


@pytest.mark.asyncio
async def test_transcript_saved_to_session(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Transcript text should be persisted to the session row."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute("SELECT transcript_text FROM sessions WHERE campaign_id = 1")
    row = await cursor.fetchone()
    assert row["transcript_text"] == "[00:00] GM: You enter the dungeon."


@pytest.mark.asyncio
async def test_summary_saved_to_session(pipeline_db, mock_gemini, mock_claude, audio_file):
    """Session summary should be persisted to the session row."""
    pipeline = ScribePipeline(pipeline_db, mock_gemini, mock_claude, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute("SELECT summary FROM sessions WHERE campaign_id = 1")
    row = await cursor.fetchone()
    assert row["summary"] == "The party entered the dungeon."


@pytest.mark.asyncio
async def test_event_staged_correctly(pipeline_db, mock_gemini, audio_file):
    """Events from Claude extraction should produce staged_changes with field_name='event'."""
    entity_with_events = {
        **_ENTITY_FIXTURE,
        "events": [
            {"timestamp": "[01:17]", "description": "The party finds a trap.", "visibility": "public"},
        ],
    }
    claude_with_events = AsyncMock()
    claude_with_events.extract_entities.return_value = [entity_with_events]
    claude_with_events.summarize_session.return_value = "Summary."

    gemini = AsyncMock()
    gemini.upload_audio.return_value = MagicMock(name="files/x")
    gemini.transcribe_single.return_value = "[00:00] GM: text"
    gemini.delete_file.return_value = None

    pipeline = ScribePipeline(pipeline_db, gemini, claude_with_events, campaign_id=1)
    await pipeline.process_file(audio_file)

    cursor = await pipeline_db.db.execute(
        "SELECT new_value, visibility FROM staged_changes WHERE field_name = 'event'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "[01:17]" in row["new_value"]
    assert "trap" in row["new_value"]
    assert row["visibility"] == "public"
