"""Inspect staged_changes from the Scribe pipeline."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from living_codex.config import CodexConfig
from living_codex.database import CodexDB


async def run():
    config = CodexConfig()
    db = CodexDB(config.db_path)
    await db.connect()

    # Sessions
    print("=== SESSIONS ===")
    cur = await db.db.execute("SELECT * FROM sessions")
    rows = await cur.fetchall()
    for r in rows:
        print(f"  id={r['id']} campaign={r['campaign_id']} "
              f"session#={r['session_number']} processed={r['processed_at']}")

    # Staged changes summary by entity
    print("\n=== STAGED CHANGES (by entity) ===")
    cur = await db.db.execute(
        "SELECT entity_name, entity_type, COUNT(*) as field_count "
        "FROM staged_changes WHERE status = 'pending' "
        "GROUP BY entity_name, entity_type ORDER BY entity_name"
    )
    rows = await cur.fetchall()
    for r in rows:
        print(f"  {r['entity_name']} ({r['entity_type']}): {r['field_count']} fields")

    # Detailed view
    print("\n=== ALL STAGED CHANGES ===")
    cur = await db.db.execute(
        "SELECT entity_name, entity_type, field_name, new_value, visibility "
        "FROM staged_changes WHERE status = 'pending' "
        "ORDER BY entity_name, field_name"
    )
    rows = await cur.fetchall()
    for r in rows:
        val = r['new_value']
        if len(val) > 120:
            val = val[:120] + "..."
        print(f"  {r['entity_name']} | {r['field_name']} ({r['visibility']}): {val}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(run())
