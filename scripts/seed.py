"""Standalone seed script — populates the live database with demo entities.

Usage:
    python scripts/seed.py

Idempotent: checks for existing records before inserting, so re-running is safe.
This script is independent of tests/conftest.py (which uses in-memory fixtures).
"""

import asyncio
import sys
import uuid
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from living_codex.config import CodexConfig
from living_codex.database import CodexDB

ENTITIES = [
    # (name, type, status_label, description_public, description_private)
    ("Baron Vrax", "NPC", "Active", "A ruthless baron.", "Secretly plotting a coup."),
    (
        "The 4th Fleet",
        "Asset",
        "Grounded",
        "Mercenary air fleet.",
        "Hidden base in the mountains.",
    ),
    (
        "The Green Box",
        "Location",
        "Active",
        "A dusty storage unit.",
        "Contains a Shoggoth.",
    ),
    (
        "Baroness Kora",
        "NPC",
        "Active",
        "A noble diplomat.",
        "Double agent for the Authority.",
    ),
]

ALIASES = [
    # (alias_text, entity_name)
    ("Sky Pirates", "The 4th Fleet"),
    ("The Baron", "Baron Vrax"),
    ("Vrax", "Baron Vrax"),
]

RELATIONSHIPS = [
    # (source_name, target_name, rel_type, citation)
    ("Baron Vrax", "Baroness Kora", "Rival", "Session 4"),
]


async def run() -> None:
    config = CodexConfig()
    db = CodexDB(config.db_path)
    await db.connect()

    campaign_id = await db.get_or_create_campaign("Armour Astir", "PbtA")
    print(f"Campaign: Armour Astir (id={campaign_id})")

    # ── Entities ──────────────────────────────────────────────────────────
    print("\nEntities:")
    for name, etype, status, pub, priv in ENTITIES:
        cursor = await db.db.execute(
            "SELECT id FROM entities WHERE name = ? AND campaign_id = ?",
            (name, campaign_id),
        )
        if await cursor.fetchone():
            print(f"  SKIPPED  {name}")
        else:
            await db.db.execute(
                "INSERT INTO entities "
                "(uuid, name, type, campaign_id, status_label, "
                "description_public, description_private) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), name, etype, campaign_id, status, pub, priv),
            )
            print(f"  INSERTED {name}")

    await db.db.commit()

    # ── Aliases ───────────────────────────────────────────────────────────
    print("\nAliases:")
    for alias_text, entity_name in ALIASES:
        cursor_ent = await db.db.execute(
            "SELECT id FROM entities WHERE name = ? AND campaign_id = ?",
            (entity_name, campaign_id),
        )
        ent_row = await cursor_ent.fetchone()
        if not ent_row:
            print(f"  WARNING  alias '{alias_text}' skipped — entity '{entity_name}' not found")
            continue

        entity_id = ent_row["id"]
        cursor_check = await db.db.execute(
            "SELECT id FROM aliases WHERE alias = ? AND entity_id = ?",
            (alias_text, entity_id),
        )
        if await cursor_check.fetchone():
            print(f"  SKIPPED  '{alias_text}' → {entity_name}")
        else:
            await db.db.execute(
                "INSERT INTO aliases (alias, entity_id) VALUES (?, ?)",
                (alias_text, entity_id),
            )
            print(f"  INSERTED '{alias_text}' -> {entity_name}")

    await db.db.commit()

    # ── Relationships ─────────────────────────────────────────────────────
    print("\nRelationships:")
    for src_name, tgt_name, rel_type, citation in RELATIONSHIPS:
        cursor_src = await db.db.execute(
            "SELECT id FROM entities WHERE name = ? AND campaign_id = ?",
            (src_name, campaign_id),
        )
        cursor_tgt = await db.db.execute(
            "SELECT id FROM entities WHERE name = ? AND campaign_id = ?",
            (tgt_name, campaign_id),
        )
        src_row = await cursor_src.fetchone()
        tgt_row = await cursor_tgt.fetchone()

        if not src_row or not tgt_row:
            print(f"  WARNING  relationship {src_name!r} → {tgt_name!r} skipped — entity missing")
            continue

        cursor_check = await db.db.execute(
            "SELECT id FROM relationships "
            "WHERE source_id = ? AND target_id = ? AND rel_type = ?",
            (src_row["id"], tgt_row["id"], rel_type),
        )
        if await cursor_check.fetchone():
            print(f"  SKIPPED  {src_name} --[{rel_type}]--> {tgt_name}")
        else:
            await db.db.execute(
                "INSERT INTO relationships (source_id, target_id, rel_type, citation) "
                "VALUES (?, ?, ?, ?)",
                (src_row["id"], tgt_row["id"], rel_type, citation),
            )
            print(f"  INSERTED {src_name} --[{rel_type}]--> {tgt_name}")

    await db.db.commit()
    await db.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
