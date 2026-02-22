"""Shared test fixtures for the Living Codex."""

import uuid

import pytest

from living_codex.database import CodexDB


@pytest.fixture
async def db(tmp_path):
    """Fresh SQLite database with schema applied."""
    db_path = tmp_path / "test_codex.db"
    codex_db = CodexDB(db_path)
    await codex_db.connect()
    yield codex_db
    await codex_db.close()


@pytest.fixture
async def seeded_db(db: CodexDB):
    """Pre-loaded with test entities: Vrax, 4th Fleet, Green Box, Kora."""
    campaign_id = await db.get_or_create_campaign("Armour Astir", "PbtA")

    entities = [
        ("Baron Vrax", "NPC", "Active", "A ruthless baron.", "Secretly plotting a coup."),
        ("The 4th Fleet", "Asset", "Grounded", "Mercenary air fleet.", "Hidden base in the mountains."),
        ("The Green Box", "Location", "Active", "A dusty storage unit.", "Contains a Shoggoth."),
        ("Baroness Kora", "NPC", "Active", "A noble diplomat.", "Double agent for the Authority."),
    ]

    for name, etype, status, pub, priv in entities:
        await db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, "
            "description_public, description_private) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), name, etype, campaign_id, status, pub, priv),
        )

    # Aliases
    aliases = [
        ("Sky Pirates", "The 4th Fleet"),
        ("The Baron", "Baron Vrax"),
        ("Vrax", "Baron Vrax"),
    ]
    for alias, entity_name in aliases:
        cursor = await db.db.execute(
            "SELECT id FROM entities WHERE name = ?", (entity_name,)
        )
        row = await cursor.fetchone()
        await db.db.execute(
            "INSERT INTO aliases (alias, entity_id) VALUES (?, ?)",
            (alias, row[0]),
        )

    # Relationships
    cursor_vrax = await db.db.execute(
        "SELECT id FROM entities WHERE name = 'Baron Vrax'"
    )
    vrax = await cursor_vrax.fetchone()
    cursor_kora = await db.db.execute(
        "SELECT id FROM entities WHERE name = 'Baroness Kora'"
    )
    kora = await cursor_kora.fetchone()
    await db.db.execute(
        "INSERT INTO relationships (source_id, target_id, rel_type, citation) "
        "VALUES (?, ?, ?, ?)",
        (vrax[0], kora[0], "Rival", "Session 4"),
    )

    await db.db.commit()
    yield db
