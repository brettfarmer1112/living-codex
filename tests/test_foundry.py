"""Unit tests for the Foundry sync layer.

Tests cover:
- FoundryClient: hash_content, create/update journal, retry on connection error,
  FoundryOfflineError on 5xx, folder graceful degradation.
- ConflictGuard: passes when hashes match, raises ConflictDetected on mismatch.
- PushManager: push_entity creates new entry, enqueues on FoundryOfflineError,
  enqueues as 'conflict' on ConflictDetected.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from living_codex.sync.foundry import FoundryClient, FoundryOfflineError
from living_codex.sync.guard import ConflictDetected, ConflictGuard


# ---------------------------------------------------------------------------
# FoundryClient.hash_content
# ---------------------------------------------------------------------------


def test_hash_content_is_sha256():
    text = "hello world"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert FoundryClient.hash_content(text) == expected


def test_hash_content_empty():
    assert FoundryClient.hash_content("") == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# FoundryClient HTTP helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_httpx_client():
    """Return a mock httpx.AsyncClient injected into FoundryClient."""
    client = FoundryClient(base_url="http://foundry.local", api_key="test-key")
    mock_inner = AsyncMock()
    client._client = mock_inner
    return client, mock_inner


def _make_response(status_code: int, json_data: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = ""
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_create_journal_success(mock_httpx_client):
    client, mock_inner = mock_httpx_client
    mock_inner.request.return_value = _make_response(200, {"_id": "abc123", "name": "Test"})

    result = await client.create_journal("Test", "<p>content</p>")
    assert result["_id"] == "abc123"
    mock_inner.request.assert_called_once()
    call_kwargs = mock_inner.request.call_args
    assert call_kwargs[0][0] == "POST"
    assert "/api/journal" in call_kwargs[0][1]


@pytest.mark.asyncio
async def test_update_journal_success(mock_httpx_client):
    client, mock_inner = mock_httpx_client
    mock_inner.request.return_value = _make_response(200, {"_id": "abc123"})

    result = await client.update_journal("abc123", "<p>new content</p>")
    assert result["_id"] == "abc123"
    call_kwargs = mock_inner.request.call_args
    assert call_kwargs[0][0] == "PUT"
    assert "abc123" in call_kwargs[0][1]


@pytest.mark.asyncio
async def test_5xx_raises_foundry_offline(mock_httpx_client):
    client, mock_inner = mock_httpx_client
    mock_inner.request.return_value = _make_response(503, {})
    # Override raise_for_status to do nothing — we handle 5xx before calling it
    mock_inner.request.return_value.raise_for_status = MagicMock()

    with pytest.raises(FoundryOfflineError, match="503"):
        await client.get_journal("xyz")


@pytest.mark.asyncio
async def test_connection_error_retries_then_raises(mock_httpx_client):
    import httpx
    client, mock_inner = mock_httpx_client
    mock_inner.request.side_effect = httpx.ConnectError("refused")

    with pytest.raises(FoundryOfflineError, match="unreachable"):
        await client.create_journal("Test", "content")

    # Should have attempted 3 times (1 initial + 2 retries)
    assert mock_inner.request.call_count == 3


@pytest.mark.asyncio
async def test_folder_api_degradation(mock_httpx_client):
    """If folder API returns a non-200, get_or_create_folder returns None gracefully."""
    import httpx
    client, mock_inner = mock_httpx_client

    # Simulate folder endpoint not existing (404 → HTTPStatusError)
    bad_resp = MagicMock()
    bad_resp.status_code = 404
    bad_resp.text = "Not Found"

    def raise_on_get(method, url, **kwargs):
        if "/api/folders" in url:
            raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=bad_resp)
        return _make_response(200, {})

    mock_inner.request.side_effect = raise_on_get

    result = await client.get_or_create_folder("Living Codex")
    assert result is None


# ---------------------------------------------------------------------------
# ConflictGuard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_guard_no_conflict():
    """If stored_hash matches live hash, check() returns the hash without raising."""
    content = "<p>hello</p>"
    live_hash = FoundryClient.hash_content(content)

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_journal.return_value = {"content": content}
    mock_client.hash_content = FoundryClient.hash_content

    guard = ConflictGuard(mock_client)
    returned_hash = await guard.check("j123", live_hash, "Test Entity")
    assert returned_hash == live_hash


@pytest.mark.asyncio
async def test_conflict_guard_detects_conflict():
    """If stored_hash differs from live content hash, ConflictDetected is raised."""
    live_content = "<p>manually edited</p>"

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_journal.return_value = {"content": live_content}
    mock_client.hash_content = FoundryClient.hash_content

    guard = ConflictGuard(mock_client)
    stale_hash = FoundryClient.hash_content("<p>original content</p>")

    with pytest.raises(ConflictDetected) as exc_info:
        await guard.check("j123", stale_hash, "Baron Vrax")

    assert exc_info.value.entity_name == "Baron Vrax"
    assert exc_info.value.foundry_id == "j123"


@pytest.mark.asyncio
async def test_conflict_guard_no_stored_hash_passes():
    """A None stored_hash (first push) should not raise ConflictDetected."""
    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_journal.return_value = {"content": "<p>anything</p>"}
    mock_client.hash_content = FoundryClient.hash_content

    guard = ConflictGuard(mock_client)
    # Should not raise — None means we've never synced before
    result = await guard.check("j999", None, "New Entity")
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_safe_update_returns_new_hash():
    content_old = "<p>old</p>"
    content_new = "<p>new content</p>"

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_journal.return_value = {"content": content_old}
    mock_client.update_journal.return_value = {}
    mock_client.hash_content = FoundryClient.hash_content

    guard = ConflictGuard(mock_client)
    stored_hash = FoundryClient.hash_content(content_old)
    new_hash = await guard.safe_update("j1", stored_hash, "Ent", content_new)

    assert new_hash == FoundryClient.hash_content(content_new)
    mock_client.update_journal.assert_called_once_with("j1", content_new)


# ---------------------------------------------------------------------------
# PushManager (integration-ish with a real DB fixture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_entity_creates_new_journal(db):
    """push_entity creates a new Foundry journal for an entity with no foundry_id."""
    campaign_id = await db.get_or_create_campaign("Test Campaign")

    import uuid
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "Baron Vrax", "NPC", campaign_id, "Active", "A ruthless baron."),
    )
    await db.db.commit()

    cursor = await db.db.execute("SELECT id FROM entities WHERE name = 'Baron Vrax'")
    row = await cursor.fetchone()
    entity_id = row["id"]

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_or_create_folder.return_value = "folder-npcs"
    mock_client.create_journal.return_value = {"_id": "journal-abc123"}
    mock_client.hash_content = FoundryClient.hash_content

    from living_codex.sync.push import PushManager
    manager = PushManager(db, mock_client)

    result = await manager.push_entity(entity_id)
    assert result == "journal-abc123"

    # Verify foundry_id was persisted
    cursor = await db.db.execute("SELECT foundry_id FROM entities WHERE id = ?", (entity_id,))
    row = await cursor.fetchone()
    assert row["foundry_id"] == "journal-abc123"


@pytest.mark.asyncio
async def test_push_entity_enqueues_on_offline(db):
    """FoundryOfflineError during create → entity is enqueued in sync_queue."""
    campaign_id = await db.get_or_create_campaign("Test Campaign")

    import uuid
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), "Offline Ent", "NPC", campaign_id),
    )
    await db.db.commit()

    cursor = await db.db.execute("SELECT id FROM entities WHERE name = 'Offline Ent'")
    row = await cursor.fetchone()
    entity_id = row["id"]

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_or_create_folder.return_value = None
    mock_client.create_journal.side_effect = FoundryOfflineError("server down")
    mock_client.hash_content = FoundryClient.hash_content

    from living_codex.sync.push import PushManager
    manager = PushManager(db, mock_client)

    result = await manager.push_entity(entity_id)
    assert result is None

    count = await db.get_sync_queue_count()
    assert count == 1


@pytest.mark.asyncio
async def test_push_entity_conflict_enqueues(db):
    """ConflictDetected → entity queued as 'conflict', not updated."""
    campaign_id = await db.get_or_create_campaign("Test Campaign")

    import uuid
    eid_str = str(uuid.uuid4())
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id, foundry_id, foundry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (eid_str, "Conflicted", "NPC", campaign_id, "j-conflict", "oldhash"),
    )
    await db.db.commit()

    cursor = await db.db.execute("SELECT id FROM entities WHERE name = 'Conflicted'")
    row = await cursor.fetchone()
    entity_id = row["id"]

    mock_client = AsyncMock(spec=FoundryClient)
    mock_client.get_journal.return_value = {"content": "<p>manually edited by GM</p>"}
    mock_client.hash_content = FoundryClient.hash_content

    from living_codex.sync.push import PushManager
    manager = PushManager(db, mock_client)

    result = await manager.push_entity(entity_id, force=False)
    assert result is None

    # Check a 'conflict' entry exists in the queue
    cursor = await db.db.execute(
        "SELECT action FROM sync_queue WHERE entity_id = ?", (entity_id,)
    )
    qrow = await cursor.fetchone()
    assert qrow is not None
    assert qrow["action"] == "conflict"
