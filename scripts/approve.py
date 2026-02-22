"""Bulk-approve all pending staged_changes into the entities table.

For Phase 3 testing only — Phase 4 replaces this with a Discord GM approve/reject UI.

Usage:
    python scripts/approve.py

What it does:
1. Reads all staged_changes with status='pending'
2. Groups them by (entity_name, entity_type)
3. For each entity:
   - If entity exists (same name + campaign): UPDATE fields
   - If new: INSERT with a fresh UUID
4. Creates aliases, relationships, events, and new fields from staged_changes
5. After all entities exist: updates last_seen_session_id for all entities seen this session
6. Marks all processed staged_changes as 'approved'
"""

import asyncio
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from living_codex.config import CodexConfig
from living_codex.database import CodexDB


_EVENT_RE = re.compile(r'^(\[[^\]]*\])?:?(public|private):(.+)$', re.DOTALL)


def _parse_event(value: str) -> tuple[str, str, str]:
    """Parse '[HH:MM]:visibility:text' or 'visibility:text' event strings."""
    m = _EVENT_RE.match(value)
    if m:
        return m.group(1) or "", m.group(2), m.group(3)
    return "", "public", value


def _build_push_manager(config: CodexConfig, db: CodexDB):
    """Return a PushManager if Foundry is configured, else None."""
    if not (config.foundry_url and config.foundry_api_key):
        return None
    from living_codex.sync.foundry import FoundryClient
    from living_codex.sync.push import PushManager
    client = FoundryClient(base_url=config.foundry_url, api_key=config.foundry_api_key)
    return PushManager(db, client)


async def run() -> None:
    config = CodexConfig()
    db = CodexDB(config.db_path)
    await db.connect()
    push_manager = _build_push_manager(config, db)

    # Fetch all pending staged_changes
    cursor = await db.db.execute(
        "SELECT * FROM staged_changes WHERE status = 'pending' ORDER BY id"
    )
    rows = await cursor.fetchall()

    if not rows:
        print("No pending staged_changes found.")
        await db.close()
        return

    print(f"Found {len(rows)} pending staged_changes.\n")

    # Group by (entity_name, entity_type, session_id) to process per-entity
    entities: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        key = (row["entity_name"], row["entity_type"])
        entities[key] = entities.get(key, [])
        entities[key].append(row)

    # Determine campaign_id from the session
    campaign_id = config.default_campaign_id

    approved_ids = []
    created = 0
    updated = 0
    aliases_created = 0
    rels_created = 0
    events_staged = 0

    # Track which entities were seen per session (for last_seen_session_id)
    entity_session_map: dict[int, set[int]] = defaultdict(set)  # entity_id → {session_ids}

    for (entity_name, entity_type), changes in entities.items():
        # Check if entity already exists
        existing = await db.get_entity_by_name(entity_name, campaign_id)

        if existing:
            entity_id = existing["id"]
            for change in changes:
                field = change["field_name"]
                value = change["new_value"]
                session_id = change["session_id"]

                if field in ("description_public", "description_private", "status_label",
                             "motivation", "appearance"):
                    await db.db.execute(
                        f"UPDATE entities SET {field} = ?, last_updated = CURRENT_TIMESTAMP "
                        f"WHERE id = ?",
                        (value, entity_id),
                    )
                elif field == "alias":
                    cur = await db.db.execute(
                        "SELECT id FROM aliases WHERE alias = ? AND entity_id = ?",
                        (value, entity_id),
                    )
                    if not await cur.fetchone():
                        await db.db.execute(
                            "INSERT INTO aliases (alias, entity_id) VALUES (?, ?)",
                            (value, entity_id),
                        )
                        aliases_created += 1
                elif field == "relationship":
                    parts = value.split(":", 2)
                    if len(parts) == 3:
                        rel_type, target_name, citation = parts
                        target = await db.get_entity_by_name(target_name, campaign_id)
                        if target:
                            cur = await db.db.execute(
                                "SELECT id FROM relationships "
                                "WHERE source_id = ? AND target_id = ? AND rel_type = ?",
                                (entity_id, target["id"], rel_type),
                            )
                            if not await cur.fetchone():
                                await db.db.execute(
                                    "INSERT INTO relationships (source_id, target_id, rel_type, citation) "
                                    "VALUES (?, ?, ?, ?)",
                                    (entity_id, target["id"], rel_type, citation),
                                )
                                rels_created += 1
                elif field == "event":
                    ts, vis, event_text = _parse_event(value)
                    await db.db.execute(
                        "INSERT INTO entity_events "
                        "(entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'approved')",
                        (entity_id, entity_name, session_id, ts or None, event_text, vis),
                    )
                    events_staged += 1
                elif field == "first_appearance":
                    # Only set first_seen_session_id if not already set
                    await db.db.execute(
                        "UPDATE entities SET first_seen_session_id = ? "
                        "WHERE id = ? AND first_seen_session_id IS NULL",
                        (session_id, entity_id),
                    )

                if session_id:
                    entity_session_map[entity_id].add(session_id)

                approved_ids.append(change["id"])

            updated += 1
            print(f"  UPDATED  {entity_name} ({entity_type})")

        else:
            # Build entity from staged_changes
            fields = {}
            alias_list = []
            rel_list = []
            event_list = []
            first_appearance_session_id = None
            session_ids_seen = set()

            for change in changes:
                field = change["field_name"]
                value = change["new_value"]
                session_id = change["session_id"]

                if field in ("description_public", "description_private", "status_label",
                             "motivation", "appearance"):
                    fields[field] = value
                elif field == "alias":
                    alias_list.append(value)
                elif field == "relationship":
                    rel_list.append(value)
                elif field == "event":
                    event_list.append((value, session_id))
                elif field == "first_appearance" and session_id:
                    first_appearance_session_id = session_id

                if session_id:
                    session_ids_seen.add(session_id)

                approved_ids.append(change["id"])

            # Insert entity
            entity_uuid = str(uuid.uuid4())
            await db.db.execute(
                "INSERT INTO entities "
                "(uuid, name, type, campaign_id, status_label, "
                "description_public, description_private, motivation, appearance, "
                "first_seen_session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_uuid,
                    entity_name,
                    entity_type,
                    campaign_id,
                    fields.get("status_label", "Unknown"),
                    fields.get("description_public", ""),
                    fields.get("description_private", ""),
                    fields.get("motivation", ""),
                    fields.get("appearance", ""),
                    first_appearance_session_id,
                ),
            )
            # Get the new entity's ID
            cur = await db.db.execute(
                "SELECT id FROM entities WHERE uuid = ?", (entity_uuid,)
            )
            row = await cur.fetchone()
            entity_id = row["id"]

            # Track sessions seen
            for sid in session_ids_seen:
                entity_session_map[entity_id].add(sid)

            # Insert aliases
            for alias in alias_list:
                await db.db.execute(
                    "INSERT INTO aliases (alias, entity_id) VALUES (?, ?)",
                    (alias, entity_id),
                )
                aliases_created += 1

            # Insert relationships (deferred — target may not exist yet)
            for rel_value in rel_list:
                parts = rel_value.split(":", 2)
                if len(parts) == 3:
                    rel_type, target_name, citation = parts
                    target = await db.get_entity_by_name(target_name, campaign_id)
                    if target:
                        await db.db.execute(
                            "INSERT INTO relationships (source_id, target_id, rel_type, citation) "
                            "VALUES (?, ?, ?, ?)",
                            (entity_id, target["id"], rel_type, citation),
                        )
                        rels_created += 1

            # Insert events
            for event_value, session_id in event_list:
                ts, vis, event_text = _parse_event(event_value)
                await db.db.execute(
                    "INSERT INTO entity_events "
                    "(entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'approved')",
                    (entity_id, entity_name, session_id, ts or None, event_text, vis),
                )
                events_staged += 1

            created += 1
            print(f"  CREATED  {entity_name} ({entity_type}) — uuid={entity_uuid[:8]}...")

    # Update last_seen_session_id for all entities that appeared in processed sessions
    for entity_id, session_ids in entity_session_map.items():
        if session_ids:
            # Find the session with the highest session_number among those seen
            placeholders = ",".join("?" * len(session_ids))
            cur = await db.db.execute(
                f"SELECT id FROM sessions WHERE id IN ({placeholders}) "
                f"ORDER BY session_number DESC LIMIT 1",
                list(session_ids),
            )
            row = await cur.fetchone()
            if row:
                await db.db.execute(
                    "UPDATE entities SET last_seen_session_id = ? WHERE id = ?",
                    (row["id"], entity_id),
                )

    # Mark all processed staged_changes as approved
    if approved_ids:
        placeholders = ",".join("?" * len(approved_ids))
        await db.db.execute(
            f"UPDATE staged_changes SET status = 'approved' WHERE id IN ({placeholders})",
            approved_ids,
        )

    await db.db.commit()

    # Push approved entities to Foundry VTT
    if push_manager is not None:
        from living_codex.sync.foundry import FoundryOfflineError
        from living_codex.sync.guard import ConflictDetected

        approved_entity_ids: list[int] = []
        for (entity_name, entity_type), changes in entities.items():
            ent = await db.get_entity_by_name(entity_name, campaign_id)
            if ent:
                approved_entity_ids.append(ent["id"])

        print(f"\nPushing {len(approved_entity_ids)} entities to Foundry...")
        for eid in approved_entity_ids:
            try:
                result = await push_manager.push_entity(eid)
                if result:
                    cursor = await db.db.execute("SELECT name FROM entities WHERE id = ?", (eid,))
                    row = await cursor.fetchone()
                    ename = row["name"] if row else str(eid)
                    print(f"  SYNCED   {ename} → Foundry ({result[:8]}...)")
            except ConflictDetected as exc:
                print(f"  CONFLICT {exc.entity_name} — manual edit detected, queued for review")
            except FoundryOfflineError as exc:
                print(f"  OFFLINE  entity_id={eid} queued for retry: {exc}")
            except Exception as exc:
                print(f"  ERROR    entity_id={eid}: {exc}")

        await push_manager._client.close()

    await db.close()

    print(f"\nDone: {created} created, {updated} updated, "
          f"{aliases_created} aliases, {rels_created} relationships, {events_staged} events.")
    print(f"Approved {len(approved_ids)} staged_changes.")


if __name__ == "__main__":
    asyncio.run(run())
