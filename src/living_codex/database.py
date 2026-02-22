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
    type TEXT CHECK(type IN ('Asset', 'Faction', 'NPC', 'Location', 'Clue', 'PC')),
    campaign_id INTEGER NOT NULL,
    status_label TEXT,
    description_public TEXT,
    description_private TEXT,
    motivation TEXT,
    appearance TEXT,
    first_seen_session_id INTEGER,
    last_seen_session_id INTEGER,
    foundry_id TEXT,
    foundry_hash TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY(first_seen_session_id) REFERENCES sessions(id),
    FOREIGN KEY(last_seen_session_id) REFERENCES sessions(id)
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
    transcript_text TEXT,
    summary TEXT,
    foundry_journal_id TEXT,
    foundry_hash TEXT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

-- Per-entity events extracted from sessions
CREATE TABLE IF NOT EXISTS entity_events (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER,
    entity_name TEXT NOT NULL,
    session_id INTEGER NOT NULL,
    event_timestamp TEXT,
    event_text TEXT NOT NULL,
    visibility TEXT CHECK(visibility IN ('public', 'private')) DEFAULT 'public',
    status TEXT CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id),
    FOREIGN KEY(entity_id) REFERENCES entities(id)
);

-- Player-character roster per campaign
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    real_name TEXT NOT NULL,
    character_name TEXT,
    character_entity_id INTEGER,
    campaign_id INTEGER NOT NULL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY(character_entity_id) REFERENCES entities(id)
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
    action TEXT CHECK(action IN ('create', 'update', 'conflict')),
    payload TEXT,
    queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(entity_id) REFERENCES entities(id)
);

-- Key-value settings / sync state
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Lore documents uploaded via Discord
CREATE TABLE IF NOT EXISTS lore_docs (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT 'discord_upload',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
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
CREATE INDEX IF NOT EXISTS idx_entity_events_entity_id ON entity_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_events_session_id ON entity_events(session_id);
CREATE INDEX IF NOT EXISTS idx_players_campaign_id ON players(campaign_id);
CREATE INDEX IF NOT EXISTS idx_sync_queue_entity_id ON sync_queue(entity_id);
CREATE INDEX IF NOT EXISTS idx_lore_docs_campaign_id ON lore_docs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_sessions_campaign_id ON sessions(campaign_id);
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
    "entity_events",
    "players",
    "lore_docs",
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
    "idx_entity_events_entity_id",
    "idx_entity_events_session_id",
    "idx_players_campaign_id",
    "idx_sync_queue_entity_id",
    "idx_lore_docs_campaign_id",
    "idx_sessions_campaign_id",
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

    # -- Session helpers --

    async def get_latest_session(self, campaign_id: int | None = None):
        """Return the most recent processed session row."""
        if campaign_id is not None:
            cursor = await self.db.execute(
                "SELECT * FROM sessions WHERE processed_at IS NOT NULL AND campaign_id = ? "
                "ORDER BY session_number DESC LIMIT 1",
                (campaign_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM sessions WHERE processed_at IS NOT NULL "
                "ORDER BY session_number DESC LIMIT 1"
            )
        return await cursor.fetchone()

    async def get_all_transcripts(self, campaign_id: int) -> list[dict]:
        """Return all sessions with transcript_text, ordered by session_number."""
        cursor = await self.db.execute(
            "SELECT session_number, transcript_text FROM sessions "
            "WHERE campaign_id = ? AND transcript_text IS NOT NULL "
            "ORDER BY session_number ASC",
            (campaign_id,),
        )
        rows = await cursor.fetchall()
        return [{"session_number": r["session_number"], "transcript_text": r["transcript_text"]}
                for r in rows]

    async def get_session_number(self, session_id: int) -> int | None:
        """Look up session_number for a given session_id."""
        cursor = await self.db.execute(
            "SELECT session_number FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return row["session_number"] if row else None

    async def get_entity_events(self, entity_id: int, approved_only: bool = True) -> list:
        """Return events for an entity, ordered by session then timestamp."""
        status_filter = "AND ee.status = 'approved'" if approved_only else ""
        cursor = await self.db.execute(
            f"SELECT ee.*, s.session_number FROM entity_events ee "
            f"JOIN sessions s ON ee.session_id = s.id "
            f"WHERE ee.entity_id = ? {status_filter} "
            f"ORDER BY s.session_number, ee.event_timestamp",
            (entity_id,),
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

    # -- Sync queue helpers --

    async def get_sync_queue_items(self, limit: int = 50) -> list:
        """Return up to *limit* pending sync queue rows, oldest first."""
        cursor = await self.db.execute(
            "SELECT * FROM sync_queue ORDER BY queued_at ASC LIMIT ?", (limit,)
        )
        return await cursor.fetchall()

    async def remove_from_sync_queue(self, queue_id: int) -> None:
        """Delete a processed sync queue row by its primary key."""
        await self.db.execute("DELETE FROM sync_queue WHERE id = ?", (queue_id,))
        await self.db.commit()

    async def enqueue_sync(self, entity_id: int, action: str, payload: str) -> None:
        """Insert a new row into sync_queue (create / update / conflict)."""
        await self.db.execute(
            "INSERT INTO sync_queue (entity_id, action, payload) VALUES (?, ?, ?)",
            (entity_id, action, payload),
        )
        await self.db.commit()

    async def update_entity_foundry(
        self, entity_id: int, foundry_id: str, foundry_hash: str
    ) -> None:
        """Persist the Foundry journal ID and content hash on an entity row."""
        await self.db.execute(
            "UPDATE entities SET foundry_id = ?, foundry_hash = ?, "
            "last_updated = CURRENT_TIMESTAMP WHERE id = ?",
            (foundry_id, foundry_hash, entity_id),
        )
        await self.db.commit()

    async def update_session_foundry(
        self, session_id: int, foundry_journal_id: str, foundry_hash: str
    ) -> None:
        """Persist the Foundry journal ID and content hash on a session row."""
        await self.db.execute(
            "UPDATE sessions SET foundry_journal_id = ?, foundry_hash = ? WHERE id = ?",
            (foundry_journal_id, foundry_hash, session_id),
        )
        await self.db.commit()

    async def get_entity_relationships(self, entity_id: int) -> list:
        """Return all relationships where entity_id is the source, with target names."""
        cursor = await self.db.execute(
            "SELECT r.rel_type, r.citation, e.name AS target_name "
            "FROM relationships r "
            "JOIN entities e ON r.target_id = e.id "
            "WHERE r.source_id = ?",
            (entity_id,),
        )
        return await cursor.fetchall()

    # -- Lore doc helpers --

    async def insert_lore_doc(
        self, campaign_id: int, title: str, content: str, source: str = "discord_upload"
    ) -> int:
        """Insert a lore document and return its row ID."""
        cursor = await self.db.execute(
            "INSERT INTO lore_docs (campaign_id, title, content, source) VALUES (?, ?, ?, ?)",
            (campaign_id, title, content, source),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_all_lore_docs(self, campaign_id: int) -> list[dict]:
        """Return all lore docs for a campaign, ordered by creation date."""
        cursor = await self.db.execute(
            "SELECT id, title, content, source, created_at FROM lore_docs "
            "WHERE campaign_id = ? ORDER BY created_at ASC",
            (campaign_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- Query enrichment helpers --

    async def get_all_relationships(self, campaign_id: int) -> list[dict]:
        """Return all relationships for a campaign with source/target names."""
        cursor = await self.db.execute(
            "SELECT e1.name AS source_name, e2.name AS target_name, "
            "r.rel_type, r.citation "
            "FROM relationships r "
            "JOIN entities e1 ON r.source_id = e1.id "
            "JOIN entities e2 ON r.target_id = e2.id "
            "WHERE e1.campaign_id = ?",
            (campaign_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_unsummarized_transcripts(self, campaign_id: int) -> list[dict]:
        """Return transcripts only for sessions that lack a summary.

        Sessions with summaries are already captured by get_all_session_summaries(),
        so including their raw transcript in the AI prompt is redundant and expensive.
        """
        cursor = await self.db.execute(
            "SELECT session_number, transcript_text FROM sessions "
            "WHERE campaign_id = ? AND transcript_text IS NOT NULL "
            "AND (summary IS NULL OR summary = '') "
            "ORDER BY session_number ASC",
            (campaign_id,),
        )
        rows = await cursor.fetchall()
        return [{"session_number": r["session_number"], "transcript_text": r["transcript_text"]}
                for r in rows]

    async def get_all_session_summaries(self, campaign_id: int) -> list[dict]:
        """Return session_number + summary for all sessions that have summaries."""
        cursor = await self.db.execute(
            "SELECT session_number, summary FROM sessions "
            "WHERE campaign_id = ? AND summary IS NOT NULL "
            "ORDER BY session_number ASC",
            (campaign_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- Sync queue helpers --

    async def get_sync_queue_count(self) -> int:
        """Return total number of rows in sync_queue."""
        cursor = await self.db.execute("SELECT COUNT(*) FROM sync_queue")
        row = await cursor.fetchone()
        return row[0] if row else 0
