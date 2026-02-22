"""Tests for the approve.py event parser and approval integration flow."""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# approve.py is a script, not a package module — import its internals directly
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from approve import _parse_event, _EVENT_RE  # noqa: E402

from living_codex.database import CodexDB


# ---------------------------------------------------------------------------
# _parse_event unit tests
# ---------------------------------------------------------------------------


class TestParseEvent:
    """Test the event string parser that splits '[HH:MM]:visibility:text'."""

    def test_standard_format_with_timestamp(self):
        ts, vis, text = _parse_event("[01:17]:public:The party finds a trap.")
        assert ts == "[01:17]"
        assert vis == "public"
        assert text == "The party finds a trap."

    def test_private_visibility(self):
        ts, vis, text = _parse_event("[02:30]:private:The GM reveals a secret.")
        assert ts == "[02:30]"
        assert vis == "private"
        assert text == "The GM reveals a secret."

    def test_no_timestamp(self):
        ts, vis, text = _parse_event("public:Something happened.")
        assert ts == ""
        assert vis == "public"
        assert text == "Something happened."

    def test_colons_in_event_text(self):
        """Event text itself may contain colons — these must not break parsing."""
        ts, vis, text = _parse_event("[03:45]:public:The guard says: 'Halt! Who goes there?'")
        assert ts == "[03:45]"
        assert vis == "public"
        assert text == "The guard says: 'Halt! Who goes there?'"

    def test_timestamp_with_hours(self):
        """Timestamps like [01:02:30] should also work."""
        ts, vis, text = _parse_event("[01:02:30]:public:An event at hour one.")
        assert ts == "[01:02:30]"
        assert vis == "public"
        assert text == "An event at hour one."

    def test_empty_timestamp_brackets(self):
        ts, vis, text = _parse_event("[]:public:Unclear timing.")
        assert ts == "[]"
        assert vis == "public"
        assert text == "Unclear timing."

    def test_fallback_for_malformed_string(self):
        """Strings that don't match any known format default to public with full text."""
        ts, vis, text = _parse_event("Just some random text with no structure")
        assert ts == ""
        assert vis == "public"
        assert text == "Just some random text with no structure"

    def test_real_pipeline_output_samples(self):
        """Exact strings from the actual pipeline output on the server."""
        samples = [
            (
                "[00:40]:public:The party ventures deep into Dark Moon Vale, camping overnight before pressing on the following morning.",
                "[00:40]", "public", "The party ventures deep into Dark Moon Vale, camping overnight before pressing on the following morning.",
            ),
            (
                "[11:27]:public:The Drake spits acid in a cone, dealing 7 damage to Astraya and 3 damage to Clove.",
                "[11:27]", "public", "The Drake spits acid in a cone, dealing 7 damage to Astraya and 3 damage to Clove.",
            ),
            (
                "[15:31]:public:Celestine's second attack is deflected as the Drake wheels its head and bats the weapon aside.",
                "[15:31]", "public", "Celestine's second attack is deflected as the Drake wheels its head and bats the weapon aside.",
            ),
        ]
        for raw, expected_ts, expected_vis, expected_text in samples:
            ts, vis, text = _parse_event(raw)
            assert ts == expected_ts, f"Failed on: {raw}"
            assert vis == expected_vis, f"Failed on: {raw}"
            assert text == expected_text, f"Failed on: {raw}"


# ---------------------------------------------------------------------------
# Approval integration tests (real SQLite, no external services)
# ---------------------------------------------------------------------------


@pytest.fixture
async def approval_db(tmp_path):
    """DB with a campaign, a session, and staged_changes ready for approval."""
    db = CodexDB(tmp_path / "approve_test.db")
    await db.connect()

    # Create campaign
    campaign_id = await db.get_or_create_campaign("Test Campaign", "PbtA")

    # Create a session
    cursor = await db.db.execute(
        "INSERT INTO sessions (campaign_id, session_number, processed_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (campaign_id, 1),
    )
    await db.db.commit()
    session_id = cursor.lastrowid

    yield db, campaign_id, session_id
    await db.close()


async def _stage(db, session_id, entity_name, entity_type, field_name, new_value, visibility="public"):
    """Helper to insert a staged_changes row."""
    await db.db.execute(
        "INSERT INTO staged_changes "
        "(session_id, entity_id, entity_name, entity_type, change_type, "
        "field_name, new_value, visibility, status) "
        "VALUES (?, NULL, ?, ?, 'create', ?, ?, ?, 'pending')",
        (session_id, entity_name, entity_type, field_name, new_value, visibility),
    )
    await db.db.commit()


async def test_event_with_timestamp_inserts_correctly(approval_db):
    """Events with [HH:MM]:public:text format should insert into entity_events without violating constraints."""
    db, campaign_id, session_id = approval_db

    # Create the entity first (approve.py needs it to exist for the "existing" path,
    # or it creates one for the "new" path)
    entity_uuid = str(uuid.uuid4())
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entity_uuid, "River Drake", "NPC", campaign_id, "Active", "A large drake."),
    )
    await db.db.commit()

    # Stage an event with the real format from the pipeline
    await _stage(db, session_id, "River Drake", "NPC", "event",
                 "[02:30]:public:The River Drake descends from the tree and charges the party.")

    # Now simulate what approve.py does for the "existing entity" path
    cursor = await db.db.execute("SELECT * FROM staged_changes WHERE status='pending'")
    changes = await cursor.fetchall()
    assert len(changes) == 1

    change = changes[0]
    ts, vis, event_text = _parse_event(change["new_value"])

    # This is the INSERT that was failing with CHECK constraint before the fix
    await db.db.execute(
        "INSERT INTO entity_events "
        "(entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'approved')",
        (1, "River Drake", session_id, ts or None, event_text, vis),
    )
    await db.db.commit()

    # Verify
    cursor = await db.db.execute("SELECT * FROM entity_events")
    events = await cursor.fetchall()
    assert len(events) == 1
    assert events[0]["event_timestamp"] == "[02:30]"
    assert events[0]["visibility"] == "public"
    assert "charges the party" in events[0]["event_text"]


async def test_event_without_timestamp_inserts_correctly(approval_db):
    """Events with just 'public:text' format should also work."""
    db, campaign_id, session_id = approval_db

    entity_uuid = str(uuid.uuid4())
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id, status_label) VALUES (?, ?, ?, ?, ?)",
        (entity_uuid, "Clove", "PC", campaign_id, "Active"),
    )
    await db.db.commit()

    await _stage(db, session_id, "Clove", "PC", "event", "public:Clove picks the lock.")

    cursor = await db.db.execute("SELECT new_value FROM staged_changes WHERE field_name='event'")
    row = await cursor.fetchone()
    ts, vis, event_text = _parse_event(row["new_value"])

    await db.db.execute(
        "INSERT INTO entity_events "
        "(entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'approved')",
        (1, "Clove", session_id, ts or None, event_text, vis),
    )
    await db.db.commit()

    cursor = await db.db.execute("SELECT * FROM entity_events")
    events = await cursor.fetchall()
    assert len(events) == 1
    assert events[0]["event_timestamp"] is None
    assert events[0]["visibility"] == "public"


async def test_private_event_visibility_check(approval_db):
    """Private events should pass the visibility CHECK constraint."""
    db, campaign_id, session_id = approval_db

    entity_uuid = str(uuid.uuid4())
    await db.db.execute(
        "INSERT INTO entities (uuid, name, type, campaign_id, status_label) VALUES (?, ?, ?, ?, ?)",
        (entity_uuid, "Baron Vrax", "NPC", campaign_id, "Active"),
    )
    await db.db.commit()

    await _stage(db, session_id, "Baron Vrax", "NPC", "event",
                 "[05:00]:private:Vrax secretly signals the assassin.")

    cursor = await db.db.execute("SELECT new_value FROM staged_changes WHERE field_name='event'")
    row = await cursor.fetchone()
    ts, vis, event_text = _parse_event(row["new_value"])

    assert vis == "private"

    await db.db.execute(
        "INSERT INTO entity_events "
        "(entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'approved')",
        (1, "Baron Vrax", session_id, ts or None, event_text, vis),
    )
    await db.db.commit()

    cursor = await db.db.execute("SELECT visibility FROM entity_events")
    row = await cursor.fetchone()
    assert row["visibility"] == "private"


async def test_broken_split_would_fail_check_constraint(approval_db):
    """Demonstrate that the old split(':',2) approach would fail the CHECK constraint."""
    db, campaign_id, session_id = approval_db

    value = "[01:17]:public:The party finds a trap."

    # Old broken approach: split(":", 2) → ["[01", "17]", "public:The party finds a trap."]
    parts = value.split(":", 2)
    old_ts, old_vis, old_text = parts[0], parts[1], parts[2]

    assert old_ts == "[01"          # Wrong!
    assert old_vis == "17]"          # Wrong! This would fail CHECK(visibility IN ('public','private'))
    assert old_text == "public:The party finds a trap."  # Wrong!

    # New regex approach
    ts, vis, text = _parse_event(value)
    assert ts == "[01:17]"           # Correct
    assert vis == "public"           # Correct — passes CHECK constraint
    assert text == "The party finds a trap."  # Correct
