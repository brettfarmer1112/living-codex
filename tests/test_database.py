"""Phase 1 tests: schema, WAL mode, foreign keys, indexes, config."""

import os
import uuid

import pytest

from living_codex.database import CodexDB, EXPECTED_INDEXES, EXPECTED_TABLES


# -- Schema tests --


async def test_schema_creates_all_tables(db: CodexDB):
    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    rows = await cursor.fetchall()
    table_names = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names), (
        f"Missing tables: {EXPECTED_TABLES - table_names}"
    )


async def test_wal_mode_enabled(db: CodexDB):
    cursor = await db.db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row[0] == "wal"


async def test_foreign_keys_enabled(db: CodexDB):
    cursor = await db.db.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_indexes_exist(db: CodexDB):
    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    rows = await cursor.fetchall()
    index_names = {row[0] for row in rows}
    assert EXPECTED_INDEXES.issubset(index_names), (
        f"Missing indexes: {EXPECTED_INDEXES - index_names}"
    )


async def test_entity_type_check_constraint(db: CodexDB):
    campaign_id = await db.get_or_create_campaign("Test Campaign")
    with pytest.raises(Exception):
        await db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), "Bad Entity", "Garbage", campaign_id),
        )
        await db.db.commit()


# -- Config tests --


def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CODEX_DISCORD_GUILD_ID", "123456")
    monkeypatch.setenv("CODEX_GM_ROLE_ID", "111")
    monkeypatch.setenv("CODEX_GM_CHANNEL_ID", "222")
    monkeypatch.setenv("CODEX_PLAYER_CHANNEL_ID", "333")

    from living_codex.config import CodexConfig

    config = CodexConfig()
    assert config.discord_token == "test-token"
    assert config.discord_guild_id == 123456
    assert config.gm_role_id == 111
    assert config.gm_channel_id == 222
    assert config.player_channel_id == 333


def test_config_missing_required_field(monkeypatch):
    # Clear any CODEX_ env vars that might exist
    for key in list(os.environ.keys()):
        if key.startswith("CODEX_"):
            monkeypatch.delenv(key, raising=False)

    from living_codex.config import CodexConfig

    with pytest.raises(Exception):
        CodexConfig()


# -- Campaign & entity helpers --


async def test_get_or_create_campaign(db: CodexDB):
    cid1 = await db.get_or_create_campaign("Delta Green", "CoC")
    cid2 = await db.get_or_create_campaign("Delta Green", "CoC")
    assert cid1 == cid2  # idempotent


async def test_seeded_data(seeded_db: CodexDB):
    entities = await seeded_db.get_all_entities()
    assert len(entities) == 4

    aliases = await seeded_db.get_all_aliases()
    assert len(aliases) == 3

    vrax = await seeded_db.get_entity_by_name("Baron Vrax")
    assert vrax is not None
    assert vrax["status_label"] == "Active"
    assert vrax["description_private"] == "Secretly plotting a coup."


async def test_meta_get_set(db: CodexDB):
    assert await db.get_meta("missing") is None
    assert await db.get_meta("missing", "fallback") == "fallback"

    await db.set_meta("version", "0.1.0")
    assert await db.get_meta("version") == "0.1.0"

    await db.set_meta("version", "0.2.0")
    assert await db.get_meta("version") == "0.2.0"
