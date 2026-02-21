"""SQLite database with async access via aiosqlite. WAL mode, foreign keys ON."""

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
-- Campaigns: multi-campaign support (Armour Astir, Delta Green, MotW)
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    system TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Core entity table
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('Asset', 'Faction', 'NPC', 'Location', 'Clue')),
    campaign_id INTEGER NOT NULL,
    status_label TEXT,
    description_public TEXT,
    description_private TEXT,
    foundry_id TEXT,
    foundry_hash TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

-- Search aliases for fuzzy matching
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    alias TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

-- Entity relationships
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    rel_type TEXT,
    citation TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES entities(id) ON DELETE CASCADE
);

-- Session-level attribution
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    session_number INTEGER,
    title TEXT,
    recorded_at DATETIME,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    audio_path TEXT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

-- Staged changes from Scribe pipeline (Phase 3+)
CREATE TABLE IF NOT EXISTS staged_changes (
    id INTEGER PRIMARY KEY,
    session_id INTEGER,
    entity_id INTEGER,
    entity_name TEXT,
    entity_type TEXT,
    change_type TEXT CHECK(change_type IN ('create', 'update')),
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    visibility TEXT CHECK(visibility IN ('public', 'private')) DEFAULT 'private',
    status TEXT CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id),
    FOREIGN KEY(entity_id) REFERENCES entities(id)
);

-- Sync queue for Foundry offline handling (Phase 5)
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    action TEXT CHECK(action IN ('create', 'update')),
    payload TEXT,
    queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(entity_id) REFERENCES entities(id)
);

-- Key-value settings / sync state
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_campaign_id ON entities(campaign_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);
CREATE INDEX IF NOT EXISTS idx_aliases_entity_id ON aliases(entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id);
CREATE INDEX IF NOT EXISTS idx_staged_changes_status ON staged_changes(status);
"""

EXPECTED_TABLES = {
    "campaigns",
    "entities",
    "aliases",
    "relationships",
    "sessions",
    "staged_changes",
    "sync_queue",
    "meta",
}

EXPECTED_INDEXES = {
    "idx_entities_name",
    "idx_entities_campaign_id",
    "idx_entities_type",
    "idx_aliases_alias",
    "idx_aliases_entity_id",
    "idx_relationships_source",
    "idx_relationships_target",
    "idx_staged_changes_status",
}


class CodexDB:
    """Async SQLite database following the garmin/hevy MCP pattern."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # WAL mode for concurrent reads/writes
        await self._db.execute("PRAGMA journal_mode=WAL")
        # Enforce foreign keys
        await self._db.execute("PRAGMA foreign_keys=ON")

        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    # -- Campaign helpers --

    async def get_or_create_campaign(self, name: str, system: str = "") -> int:
        cursor = await self.db.execute(
            "SELECT id FROM campaigns WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row:
            return row[0]
        cursor = await self.db.execute(
            "INSERT INTO campaigns (name, system) VALUES (?, ?)", (name, system)
        )
        await self.db.commit()
        return cursor.lastrowid

    # -- Entity helpers --

    async def get_entity_by_name(self, name: str, campaign_id: int | None = None):
        if campaign_id is not None:
            cursor = await self.db.execute(
                "SELECT * FROM entities WHERE name = ? AND campaign_id = ?",
                (name, campaign_id),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM entities WHERE name = ?", (name,)
            )
        return await cursor.fetchone()

    async def get_all_entities(self, campaign_id: int | None = None):
        if campaign_id is not None:
            cursor = await self.db.execute(
                "SELECT * FROM entities WHERE campaign_id = ?", (campaign_id,)
            )
        else:
            cursor = await self.db.execute("SELECT * FROM entities")
        return await cursor.fetchall()

    async def get_all_aliases(self):
        cursor = await self.db.execute(
            "SELECT a.alias, a.entity_id, e.name FROM aliases a "
            "JOIN entities e ON a.entity_id = e.id"
        )
        return await cursor.fetchall()

    # -- Meta helpers --

    async def get_meta(self, key: str, default: str | None = None) -> str | None:
        cursor = await self.db.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default

    async def set_meta(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        await self.db.commit()
