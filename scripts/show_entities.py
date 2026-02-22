"""Show all entities, aliases, and relationships in the DB."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from living_codex.config import CodexConfig
from living_codex.database import CodexDB

async def run():
    config = CodexConfig()
    db = CodexDB(config.db_path)
    await db.connect()

    print("=== 3 SESSIONS (one per MP3) ===")
    cur = await db.db.execute("SELECT id, session_number, audio_path, processed_at FROM sessions ORDER BY id")
    rows = await cur.fetchall()
    for r in rows:
        fname = r["audio_path"].split("/")[-1].split("\\")[-1]
        print(f"  Session {r['session_number']}: {fname}  → processed {r['processed_at']}")

    for etype in ("NPC", "Location", "Faction", "Asset", "Clue"):
        cur = await db.db.execute(
            "SELECT name, status_label, description_public FROM entities WHERE type=? ORDER BY name", (etype,)
        )
        rows = await cur.fetchall()
        print(f"\n=== {etype}s ({len(rows)}) ===")
        for r in rows:
            desc = r["description_public"] or ""
            if len(desc) > 80:
                desc = desc[:80] + "..."
            print(f"  {r['name']} [{r['status_label']}]")
            print(f"    {desc}")

    print("\n=== ALIASES ===")
    cur = await db.db.execute(
        "SELECT a.alias, e.name FROM aliases a JOIN entities e ON a.entity_id=e.id ORDER BY a.alias"
    )
    rows = await cur.fetchall()
    for r in rows:
        print(f"  {r['alias']} → {r['name']}")

    print("\n=== RELATIONSHIPS ===")
    cur = await db.db.execute(
        "SELECT s.name as src, t.name as tgt, r.rel_type, r.citation "
        "FROM relationships r "
        "JOIN entities s ON r.source_id=s.id "
        "JOIN entities t ON r.target_id=t.id"
    )
    rows = await cur.fetchall()
    for r in rows:
        print(f"  {r['src']} --[{r['rel_type']}]--> {r['tgt']} (cited: {r['citation']})")

    await db.close()

if __name__ == "__main__":
    asyncio.run(run())
