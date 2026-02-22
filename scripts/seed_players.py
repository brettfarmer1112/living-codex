"""Seed the players table with the DMV campaign roster.

Usage:
    python scripts/seed_players.py

Idempotent: skips existing players by real_name + campaign_id.
Run after seed.py so the campaign row exists.
"""

import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from living_codex.config import CodexConfig
from living_codex.database import CodexDB

# DMV campaign player roster
# (real_name, character_name, notes)
DMV_PLAYERS = [
    ("Brett",       "Clove",  "Main protagonist — runs the sessions"),
    ("Rick Sannage", "Grim",  "The muscle"),
    ("Erin",        "Laurel", "The face / diplomat"),
    ("Mikey",       "Sable",  "Stealth and recon"),
]

CAMPAIGN_NAME = "DMV"


async def run() -> None:
    config = CodexConfig()
    db = CodexDB(config.db_path)
    await db.connect()

    campaign_id = await db.get_or_create_campaign(CAMPAIGN_NAME, "Blades in the Dark")
    print(f"Campaign: {CAMPAIGN_NAME} (id={campaign_id})")

    print("\nPlayers:")
    for real_name, character_name, notes in DMV_PLAYERS:
        cursor = await db.db.execute(
            "SELECT id FROM players WHERE real_name = ? AND campaign_id = ?",
            (real_name, campaign_id),
        )
        if await cursor.fetchone():
            print(f"  SKIPPED  {real_name} ({character_name})")
            continue

        # Try to link to existing entity with matching character name (type='PC')
        cursor_ent = await db.db.execute(
            "SELECT id FROM entities WHERE name = ? AND campaign_id = ? AND type = 'PC'",
            (character_name, campaign_id),
        )
        ent_row = await cursor_ent.fetchone()
        character_entity_id = ent_row["id"] if ent_row else None

        await db.db.execute(
            "INSERT INTO players (real_name, character_name, character_entity_id, campaign_id, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (real_name, character_name, character_entity_id, campaign_id, notes),
        )
        link_note = f"→ entity_id={character_entity_id}" if character_entity_id else "(no entity yet)"
        print(f"  INSERTED {real_name} / {character_name} {link_note}")

    await db.db.commit()
    await db.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
