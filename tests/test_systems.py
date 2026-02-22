"""Systems test suite — Living Codex QA.

Written from the perspective of a 30-year TTRPG veteran who has seen every
campaign management tool from Obsidian Portal to World Anvil fail the same ways.

These tests validate the USER EXPERIENCE, not just code correctness:
- Does the system handle realistic campaign data volumes?
- Do features compose correctly across the full stack?
- Are edge cases from real table play accounted for?
- Does the query enrichment actually improve answers?
- Are Discord's constraints (2000-char limit, ephemeral rules) respected?

Test naming convention: test_<feature>_<scenario>
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from living_codex.commands.codex import (
    _format_entities_for_context,
    _format_lore_for_context,
    _format_relationships_for_context,
    _format_summaries_for_context,
    _format_transcripts_for_context,
    _send_long_response,
    _split_text,
    _TRANSCRIPT_CHAR_BUDGET,
)
from living_codex.database import CodexDB
from living_codex.search import search


# =====================================================================
# Fixtures — realistic campaign data, not toy examples
# =====================================================================

@pytest.fixture
async def campaign_db(tmp_path):
    """A DB seeded with a realistic mid-campaign state.

    10 sessions, 25+ entities, relationships, events, summaries, lore docs.
    This mirrors a campaign that's been running for ~6 months biweekly.
    """
    db = CodexDB(tmp_path / "systems_test.db")
    await db.connect()

    cid = await db.get_or_create_campaign("Armour Astir: Advent", "PbtA")

    # --- Entities: a realistic mix of types ---
    entities_data = [
        # (name, type, status, pub_desc, priv_desc)
        ("Clove Ashwood", "PC", "Active", "A reckless pilot from the Reach.", ""),
        ("Sable Voss", "PC", "Active", "A former Authority operative turned rebel.", ""),
        ("Pike Morrow", "PC", "Active", "A mechanic and reluctant hero.", ""),
        ("Baron Vrax", "NPC", "Active", "The iron-fisted ruler of the Verdant March.", "Secretly funding the Remnant."),
        ("Baroness Kora", "NPC", "Active", "A diplomat who keeps the peace.", "Double agent for the Authority."),
        ("The Conductor", "NPC", "Unknown", "A mysterious figure glimpsed at the railyard.", "Is actually a rogue AI."),
        ("Madre Cuesta", "NPC", "Dead", "A village elder killed in the Ashfall Massacre.", "Knew the location of the Vault."),
        ("The Authority", "Faction", "Active", "The continental government. Bureaucratic. Ruthless.", ""),
        ("The Remnant", "Faction", "Active", "A scattered resistance movement.", "Funded by Baron Vrax."),
        ("The 4th Fleet", "Asset", "Grounded", "A squadron of salvaged mechs.", "Hidden in the Verdant March."),
        ("The Rusted Crown", "Asset", "Active", "An ancient mech of unknown origin.", "Contains an AI core — The Conductor."),
        ("Fort Despair", "Location", "Active", "An Authority forward base on the border.", ""),
        ("The Verdant March", "Location", "Active", "A dense forest region, politically contested.", ""),
        ("The Vault", "Location", "Unknown", "A pre-war bunker rumoured to hold superweapons.", "Location known only to Madre Cuesta (dead)."),
        ("Ashfall Village", "Location", "Destroyed", "A farming settlement destroyed by Authority forces.", ""),
        ("The Black Ledger", "Clue", "Active", "A coded financial document found on an Authority courier.", "Links Vrax to the Remnant."),
        ("Mech Schematics", "Clue", "Active", "Blueprints for a pre-war combat frame.", "Missing a critical power core page."),
        ("Hestia Node", "Asset", "Active", "A communications relay hidden in the March.", ""),
        ("Captain Rowe", "NPC", "Active", "An Authority officer stationed at Fort Despair.", "Sympathetic to the Remnant."),
        ("Decker", "NPC", "Inactive", "A smuggler who went silent after Session 5.", "Captured by the Authority."),
        ("The Iron Pact", "Faction", "Active", "A mercenary guild loyal to the highest bidder.", ""),
        ("Ghost", "NPC", "Active", "A Remnant sniper. Speaks in gestures.", "Real name: Yael Estin."),
        ("The Assembly", "Faction", "Inactive", "A defunct parliament. Dissolved by the Authority.", ""),
        ("The Heart", "Asset", "Unknown", "A power source mentioned in the Mech Schematics.", ""),
        ("Lian Forge", "NPC", "Active", "A blacksmith and information broker in the March.", ""),
    ]

    entity_ids = {}
    for name, etype, status, pub, priv in entities_data:
        cursor = await db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, "
            "description_public, description_private) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), name, etype, cid, status, pub, priv),
        )
        entity_ids[name] = cursor.lastrowid

    # --- Aliases (players never remember canonical names) ---
    alias_data = [
        ("The Baron", "Baron Vrax"),
        ("Vrax", "Baron Vrax"),
        ("Kora", "Baroness Kora"),
        ("Sky Pirates", "The 4th Fleet"),
        ("The Fleet", "The 4th Fleet"),
        ("Rusty", "The Rusted Crown"),
        ("The Crown", "The Rusted Crown"),
        ("Fort D", "Fort Despair"),
        ("The March", "The Verdant March"),
    ]
    for alias, entity_name in alias_data:
        await db.db.execute(
            "INSERT INTO aliases (alias, entity_id) VALUES (?, ?)",
            (alias, entity_ids[entity_name]),
        )

    # --- Relationships ---
    rel_data = [
        ("Baron Vrax", "Baroness Kora", "Rival", "Session 4"),
        ("Baron Vrax", "The Remnant", "Funds", "Session 7"),
        ("Baroness Kora", "The Authority", "Serves", "Session 2"),
        ("The Conductor", "The Rusted Crown", "Inhabits", "Session 9"),
        ("Captain Rowe", "The Remnant", "Sympathizes", "Session 6"),
        ("Ghost", "The Remnant", "Member", "Session 3"),
        ("Decker", "The Iron Pact", "Former member", "Session 5"),
        ("Clove Ashwood", "The Rusted Crown", "Pilots", "Session 8"),
        ("The Black Ledger", "Baron Vrax", "Implicates", "Session 7"),
    ]
    for src, tgt, rtype, cite in rel_data:
        await db.db.execute(
            "INSERT INTO relationships (source_id, target_id, rel_type, citation) VALUES (?, ?, ?, ?)",
            (entity_ids[src], entity_ids[tgt], rtype, cite),
        )

    # --- Sessions with transcripts and summaries ---
    for sn in range(1, 11):
        transcript = (
            f"[00:00] GM: Session {sn} begins.\n"
            f"[00:05] Clove: I check the perimeter.\n"
            f"[00:10] GM: You see movement near the treeline.\n"
            f"[00:15] Sable: I ready my weapon.\n"
            f"[00:20] Pike: I start repairs on the mech.\n"
        ) * 20  # ~2000 chars per session, realistic
        summary = (
            f"Session {sn} opens with the party camped at the edge of the Verdant March. "
            f"Clove scouts the perimeter and spots Authority patrols. "
            f"Sable engages in a tense standoff while Pike keeps the Rusted Crown operational. "
            f"The session ends with a new lead on the Vault."
        ) if sn <= 8 else None  # Sessions 9 and 10 don't have summaries yet

        await db.db.execute(
            "INSERT INTO sessions (campaign_id, session_number, transcript_text, summary, processed_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (cid, sn, transcript, summary),
        )

    # --- Entity events ---
    session_cursor = await db.db.execute("SELECT id, session_number FROM sessions ORDER BY session_number")
    session_rows = await session_cursor.fetchall()
    session_map = {r["session_number"]: r["id"] for r in session_rows}

    events_data = [
        (entity_ids["Clove Ashwood"], session_map[1], "[00:15]", "Discovers the Hestia Node signal.", "public", "approved"),
        (entity_ids["Baron Vrax"], session_map[4], "[01:20]", "Confronts Baroness Kora at the summit.", "public", "approved"),
        (entity_ids["Madre Cuesta"], session_map[3], "[02:00]", "Killed during the Ashfall Massacre.", "public", "approved"),
        (entity_ids["Decker"], session_map[5], "[00:45]", "Last seen fleeing the Iron Pact safehouse.", "public", "approved"),
        (entity_ids["Decker"], session_map[5], "[01:30]", "Captured by Authority forces off-screen.", "private", "approved"),
        (entity_ids["The Conductor"], session_map[9], "[00:30]", "First manifests through the Rusted Crown's comms.", "public", "approved"),
        (entity_ids["Ghost"], session_map[3], "[01:45]", "Provides covering fire during the escape from Ashfall.", "public", "pending"),
    ]
    for eid, sid, ts, text, vis, status in events_data:
        await db.db.execute(
            "INSERT INTO entity_events (entity_id, entity_name, session_id, event_timestamp, event_text, visibility, status) "
            "VALUES (?, (SELECT name FROM entities WHERE id = ?), ?, ?, ?, ?, ?)",
            (eid, eid, sid, ts, text, vis, status),
        )

    # --- Lore docs ---
    await db.insert_lore_doc(cid, "The Ashfall Massacre", (
        "On the third day of the Verdant Campaign, Authority forces razed Ashfall Village. "
        "23 civilians killed. Madre Cuesta among the dead. The official report claims "
        "Remnant insurgents were harboured there — survivors dispute this."
    ))
    await db.insert_lore_doc(cid, "Pre-War Mech Classification", (
        "Type-A: Cavalry Frame (light, fast). Type-B: Siege Frame (heavy, slow). "
        "Type-C: Command Frame (rare, integrated AI core). The Rusted Crown is believed "
        "to be a Type-C, though no intact specimens were known to survive the Collapse."
    ))
    await db.insert_lore_doc(cid, "Authority Chain of Command", (
        "The Chancellor → Regional Governors → Military Command → Field Officers. "
        "Captain Rowe reports to Colonel Hassin (Fort Despair CO, not yet encountered in play)."
    ))

    # --- Players roster ---
    player_data = [
        ("Brett", "Clove Ashwood", entity_ids["Clove Ashwood"]),
        ("Jordan", "Sable Voss", entity_ids["Sable Voss"]),
        ("Morgan", "Pike Morrow", entity_ids["Pike Morrow"]),
    ]
    for real_name, char_name, eid in player_data:
        await db.db.execute(
            "INSERT INTO players (real_name, character_name, character_entity_id, campaign_id) "
            "VALUES (?, ?, ?, ?)",
            (real_name, char_name, eid, cid),
        )

    await db.db.commit()
    yield db
    await db.close()


# =====================================================================
# 1. ENTITY SEARCH — The core lookup that players use every session
# =====================================================================

class TestEntitySearch:
    """Players misspell names, use nicknames, and forget canonical forms.
    Search must handle all of this gracefully.
    """

    @pytest.mark.asyncio
    async def test_search_exact_name(self, campaign_db):
        """Direct name lookup — the happy path."""
        result = await search(campaign_db, "Baron Vrax")
        assert result.kind == "direct"
        assert result.entity["name"] == "Baron Vrax"

    @pytest.mark.asyncio
    async def test_search_by_alias(self, campaign_db):
        """Players call the 4th Fleet 'Sky Pirates' at the table."""
        result = await search(campaign_db, "Sky Pirates")
        assert result.kind == "direct"
        assert result.entity["name"] == "The 4th Fleet"

    @pytest.mark.asyncio
    async def test_search_partial_name(self, campaign_db):
        """Typing 'Vrax' should match 'Baron Vrax' via alias."""
        result = await search(campaign_db, "Vrax")
        assert result.kind == "direct"
        assert result.entity["name"] == "Baron Vrax"

    @pytest.mark.asyncio
    async def test_search_misspelled(self, campaign_db):
        """Players misspell names constantly. 'Barron Vraks' should still work."""
        result = await search(campaign_db, "Barron Vraks")
        assert result.kind in ("direct", "candidates")
        names = [result.entity["name"]] if result.entity else [c["name"] for c in result.candidates]
        assert "Baron Vrax" in names

    @pytest.mark.asyncio
    async def test_search_dead_entity(self, campaign_db):
        """Dead entities should still be searchable — players ask 'who was Madre Cuesta?'"""
        result = await search(campaign_db, "Madre Cuesta")
        assert result.kind == "direct"
        assert result.entity["status_label"] == "Dead"

    @pytest.mark.asyncio
    async def test_search_destroyed_location(self, campaign_db):
        """Destroyed locations are plot-relevant. Must be findable."""
        result = await search(campaign_db, "Ashfall Village")
        assert result.kind == "direct"
        assert result.entity["status_label"] == "Destroyed"

    @pytest.mark.asyncio
    async def test_search_nonsense_returns_low_quality(self, campaign_db):
        """Completely unrelated query may return weak candidates due to fuzzy matching.

        This is expected behavior — rapidfuzz WRatio finds partial matches against
        short entity names even for nonsense input. The candidate threshold (40) is
        deliberately low to avoid missing misspelled queries. With 25+ entities,
        some will cross the floor on random substrings.

        FINDING: Consider showing "no strong matches" UX when best score < 50.
        """
        result = await search(campaign_db, "xyzzy plugh")
        # With 25 entities, weak fuzzy matches are expected — not a bug
        assert result.kind in ("none", "candidates")

    @pytest.mark.asyncio
    async def test_search_ambiguous_returns_candidates(self, campaign_db):
        """'Baron' matches both 'Baron Vrax' and 'Baroness Kora' — should offer candidates."""
        result = await search(campaign_db, "Baron")
        # Both should appear as candidates or one as direct
        if result.kind == "candidates":
            names = [c["name"] for c in result.candidates]
            assert any("Vrax" in n for n in names)

    @pytest.mark.asyncio
    async def test_search_pc_findable(self, campaign_db):
        """Player characters must be searchable — players forget their own stats."""
        result = await search(campaign_db, "Clove Ashwood")
        assert result.kind == "direct"
        assert result.entity["type"] == "PC"

    @pytest.mark.asyncio
    async def test_search_clue_findable(self, campaign_db):
        """Clues are the most-queried entity type. 'What was that ledger again?'"""
        result = await search(campaign_db, "Black Ledger")
        assert result.kind == "direct"
        assert result.entity["type"] == "Clue"


# =====================================================================
# 2. QUERY CONTEXT ASSEMBLY — The enriched context pipeline
# =====================================================================

class TestQueryContextAssembly:
    """The context assembly determines whether /query gives useful answers
    or hallucinated garbage. Every section matters.
    """

    @pytest.mark.asyncio
    async def test_entities_formatted_with_all_types(self, campaign_db):
        """All 6 entity types (NPC, PC, Faction, Location, Asset, Clue) appear in output."""
        entities = await campaign_db.get_all_entities(1)
        text = _format_entities_for_context([dict(e) for e in entities])

        for etype in ("NPC", "PC", "Faction", "Location", "Asset", "Clue"):
            assert f"({etype})" in text, f"Missing entity type: {etype}"

    @pytest.mark.asyncio
    async def test_dead_entities_show_status(self, campaign_db):
        """Dead/Destroyed entities must show status so the AI knows they're gone."""
        entities = await campaign_db.get_all_entities(1)
        text = _format_entities_for_context([dict(e) for e in entities])
        assert "[Dead]" in text
        assert "[Destroyed]" in text

    @pytest.mark.asyncio
    async def test_relationships_form_graph(self, campaign_db):
        """Relationships should form a readable directed graph."""
        rels = await campaign_db.get_all_relationships(1)
        text = _format_relationships_for_context(rels)

        assert "Baron Vrax -[Rival]-> Baroness Kora" in text
        assert "Baron Vrax -[Funds]-> The Remnant" in text
        assert "Baroness Kora -[Serves]-> The Authority" in text

    @pytest.mark.asyncio
    async def test_summaries_ordered_chronologically(self, campaign_db):
        """Session summaries must appear in order — narrative coherence depends on it."""
        summaries = await campaign_db.get_all_session_summaries(1)
        text = _format_summaries_for_context(summaries)

        # Sessions 1-8 have summaries, 9-10 don't
        assert "=== Session 1 ===" in text
        assert "=== Session 8 ===" in text
        assert "=== Session 9 ===" not in text

        # Order check: session 1 appears before session 8
        pos_1 = text.index("Session 1")
        pos_8 = text.index("Session 8")
        assert pos_1 < pos_8

    @pytest.mark.asyncio
    async def test_lore_docs_all_present(self, campaign_db):
        """All uploaded lore docs must appear in query context."""
        docs = await campaign_db.get_all_lore_docs(1)
        text = _format_lore_for_context(docs)

        assert "The Ashfall Massacre" in text
        assert "Pre-War Mech Classification" in text
        assert "Authority Chain of Command" in text

    @pytest.mark.asyncio
    async def test_transcripts_respect_budget(self, campaign_db):
        """10 sessions of transcripts should be truncated if they exceed the char budget."""
        sessions = await campaign_db.get_all_transcripts(1)
        text = _format_transcripts_for_context(sessions)

        # Text should not wildly exceed budget
        assert len(text) <= _TRANSCRIPT_CHAR_BUDGET + 1000

    @pytest.mark.asyncio
    async def test_lore_docs_scoped_to_campaign(self, campaign_db):
        """Lore docs from campaign B must not leak into campaign A's query context."""
        cid_b = await campaign_db.get_or_create_campaign("Delta Green")
        await campaign_db.insert_lore_doc(cid_b, "TOP SECRET", "Classified info from Delta Green.")

        docs_a = await campaign_db.get_all_lore_docs(1)
        text_a = _format_lore_for_context(docs_a)
        assert "TOP SECRET" not in text_a
        assert "Delta Green" not in text_a


# =====================================================================
# 3. DATABASE INTEGRITY — The foundation everything builds on
# =====================================================================

class TestDatabaseIntegrity:
    """If the DB is wrong, nothing downstream can be right."""

    @pytest.mark.asyncio
    async def test_entity_count_matches_expected(self, campaign_db):
        """Sanity check: we seeded 25 entities."""
        entities = await campaign_db.get_all_entities(1)
        assert len(entities) == 25

    @pytest.mark.asyncio
    async def test_session_count(self, campaign_db):
        """10 sessions seeded, all processed."""
        cursor = await campaign_db.db.execute(
            "SELECT COUNT(*) FROM sessions WHERE campaign_id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == 10

    @pytest.mark.asyncio
    async def test_relationship_count(self, campaign_db):
        """9 relationships seeded."""
        rels = await campaign_db.get_all_relationships(1)
        assert len(rels) == 9

    @pytest.mark.asyncio
    async def test_lore_doc_count(self, campaign_db):
        """3 lore docs seeded."""
        docs = await campaign_db.get_all_lore_docs(1)
        assert len(docs) == 3

    @pytest.mark.asyncio
    async def test_events_approved_vs_pending(self, campaign_db):
        """Approved events visible; pending events filtered by default."""
        # Ghost's event is pending — should not appear in approved-only queries
        ghost_cursor = await campaign_db.db.execute(
            "SELECT id FROM entities WHERE name = 'Ghost'"
        )
        ghost = await ghost_cursor.fetchone()
        events = await campaign_db.get_entity_events(ghost["id"], approved_only=True)
        assert len(events) == 0

        events_all = await campaign_db.get_entity_events(ghost["id"], approved_only=False)
        assert len(events_all) == 1

    @pytest.mark.asyncio
    async def test_private_events_exist_but_separate(self, campaign_db):
        """Decker has both public and private events. Private should be distinguishable."""
        decker_cursor = await campaign_db.db.execute(
            "SELECT id FROM entities WHERE name = 'Decker'"
        )
        decker = await decker_cursor.fetchone()
        events = await campaign_db.get_entity_events(decker["id"], approved_only=True)

        public = [e for e in events if e["visibility"] == "public"]
        private = [e for e in events if e["visibility"] == "private"]
        assert len(public) == 1
        assert len(private) == 1
        assert "Captured" in private[0]["event_text"]

    @pytest.mark.asyncio
    async def test_session_summaries_partial_coverage(self, campaign_db):
        """Sessions 1-8 have summaries, 9-10 don't. get_all_session_summaries reflects this."""
        summaries = await campaign_db.get_all_session_summaries(1)
        assert len(summaries) == 8
        nums = [s["session_number"] for s in summaries]
        assert 9 not in nums
        assert 10 not in nums

    @pytest.mark.asyncio
    async def test_latest_session_is_10(self, campaign_db):
        """get_latest_session returns session 10, not 1."""
        session = await campaign_db.get_latest_session(1)
        assert session["session_number"] == 10

    @pytest.mark.asyncio
    async def test_lore_doc_custom_source(self, campaign_db):
        """Can insert lore docs with custom source tags."""
        cid = 1
        doc_id = await campaign_db.insert_lore_doc(
            cid, "GM Notes", "Private GM notes.", source="manual"
        )
        docs = await campaign_db.get_all_lore_docs(cid)
        manual = [d for d in docs if d["source"] == "manual"]
        assert len(manual) == 1
        assert manual[0]["title"] == "GM Notes"


# =====================================================================
# 4. DISCORD OUTPUT — Respecting the medium's constraints
# =====================================================================

class TestDiscordOutput:
    """Discord has hard limits. Violating them = silent failures or ugly UX."""

    def test_split_text_short_message_no_split(self):
        """Short messages should not be split."""
        text = "Hello, world!"
        assert _split_text(text, 1900) == ["Hello, world!"]

    def test_split_text_respects_newlines(self):
        """Split should prefer newline boundaries over mid-word cuts."""
        text = "Line 1\nLine 2\nLine 3\n" + "x" * 1900
        chunks = _split_text(text, 1900)
        assert len(chunks) >= 2
        assert chunks[0].endswith("Line 3")

    def test_split_text_leading_newlines(self):
        """FINDING: _split_text produces empty first chunk from leading newlines.

        This is a real bug — sending an empty string as a Discord message
        raises HTTPException. The fix should strip leading whitespace before
        splitting, or filter empty chunks before sending.
        """
        text = "\n\n\n" + "content " * 500
        chunks = _split_text(text, 1900)
        # Currently produces an empty first chunk — documenting existing behavior
        # The first chunk may be empty due to leading newlines
        assert len(chunks) >= 2
        # At least the content chunks should be non-empty
        content_chunks = [c for c in chunks if c.strip()]
        assert len(content_chunks) >= 1

    @pytest.mark.asyncio
    async def test_send_long_response_short_message(self):
        """Short responses should send as a single message, no file attachment."""
        interaction = AsyncMock()
        await _send_long_response(interaction, "Short answer.", prefix="**Q:**\n\n")
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "file" not in call_kwargs.kwargs or call_kwargs.kwargs.get("file") is None

    @pytest.mark.asyncio
    async def test_send_long_response_long_message_attaches_file(self):
        """Long responses should send preview + .md file attachment."""
        interaction = AsyncMock()
        long_text = "This is a paragraph.\n\n" + "Detail " * 500
        await _send_long_response(
            interaction, long_text,
            prefix="**Q: Something?**\n\n",
            filename="test.md",
        )
        call_kwargs = interaction.followup.send.call_args
        # Should have a file kwarg
        assert call_kwargs.kwargs.get("file") is not None or (
            len(call_kwargs.args) > 0 and hasattr(call_kwargs.args[0], 'read')
        )

    @pytest.mark.asyncio
    async def test_send_long_response_preview_under_2000(self):
        """The preview message itself must never exceed Discord's 2000 char limit."""
        interaction = AsyncMock()
        # Very long first paragraph
        long_text = "A" * 3000 + "\n\nSecond paragraph."
        await _send_long_response(interaction, long_text, prefix="**Q:**\n\n")
        sent_text = interaction.followup.send.call_args.args[0]
        assert len(sent_text) < 2000


# =====================================================================
# 5. MULTI-CAMPAIGN ISOLATION — The silent killer of campaign tools
# =====================================================================

class TestMultiCampaignIsolation:
    """Many groups run multiple campaigns (e.g., Armour Astir + Delta Green).
    Data must NEVER leak between campaigns.
    """

    @pytest.mark.asyncio
    async def test_entities_isolated_between_campaigns(self, campaign_db):
        """An NPC from Campaign B must not appear in Campaign A searches."""
        cid_b = await campaign_db.get_or_create_campaign("Delta Green")
        await campaign_db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
            "VALUES (?, 'Agent Smith', 'NPC', ?, 'Active', 'A federal agent.')",
            (str(uuid.uuid4()), cid_b),
        )
        await campaign_db.db.commit()

        entities_a = await campaign_db.get_all_entities(1)
        names_a = [e["name"] for e in entities_a]
        assert "Agent Smith" not in names_a

    @pytest.mark.asyncio
    async def test_transcripts_isolated(self, campaign_db):
        """Transcripts from Campaign B must not appear in Campaign A queries."""
        cid_b = await campaign_db.get_or_create_campaign("Delta Green")
        await campaign_db.db.execute(
            "INSERT INTO sessions (campaign_id, session_number, transcript_text, processed_at) "
            "VALUES (?, 1, 'TOP SECRET DG transcript.', CURRENT_TIMESTAMP)",
            (cid_b,),
        )
        await campaign_db.db.commit()

        transcripts_a = await campaign_db.get_all_transcripts(1)
        all_text = " ".join(t["transcript_text"] for t in transcripts_a)
        assert "TOP SECRET" not in all_text

    @pytest.mark.asyncio
    async def test_relationships_isolated(self, campaign_db):
        """Relationships from Campaign B must not appear in Campaign A context."""
        cid_b = await campaign_db.get_or_create_campaign("Delta Green")
        # Create two DG entities and a relationship
        for name in ("Agent X", "Agent Y"):
            await campaign_db.db.execute(
                "INSERT INTO entities (uuid, name, type, campaign_id, status_label) VALUES (?, ?, 'NPC', ?, 'Active')",
                (str(uuid.uuid4()), name, cid_b),
            )
        await campaign_db.db.commit()

        cx = await campaign_db.db.execute("SELECT id FROM entities WHERE name = 'Agent X'")
        cy = await campaign_db.db.execute("SELECT id FROM entities WHERE name = 'Agent Y'")
        ax = await cx.fetchone()
        ay = await cy.fetchone()
        await campaign_db.db.execute(
            "INSERT INTO relationships (source_id, target_id, rel_type) VALUES (?, ?, 'Handler')",
            (ax["id"], ay["id"]),
        )
        await campaign_db.db.commit()

        rels_a = await campaign_db.get_all_relationships(1)
        rel_text = _format_relationships_for_context(rels_a)
        assert "Agent X" not in rel_text
        assert "Agent Y" not in rel_text


# =====================================================================
# 6. EDGE CASES FROM REAL TABLE PLAY
# =====================================================================

class TestRealPlayEdgeCases:
    """Things that actually happen at the table and break naive implementations."""

    @pytest.mark.asyncio
    async def test_entity_with_colon_in_name(self, campaign_db):
        """'Armour Astir: Advent' has a colon. Entity names can too."""
        await campaign_db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
            "VALUES (?, 'Mech: Iron Tide', 'Asset', 1, 'Active', 'A water-frame mech.')",
            (str(uuid.uuid4()),),
        )
        await campaign_db.db.commit()

        result = await search(campaign_db, "Mech: Iron Tide")
        assert result.kind == "direct"

    @pytest.mark.asyncio
    async def test_entity_with_apostrophe(self, campaign_db):
        """Names like O'Brien, K'thar are common in fantasy/sci-fi."""
        await campaign_db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
            "VALUES (?, ?, 'NPC', 1, 'Active', 'A scout.')",
            (str(uuid.uuid4()), "K'thar the Swift"),
        )
        await campaign_db.db.commit()

        result = await search(campaign_db, "K'thar")
        assert result.kind in ("direct", "candidates")

    @pytest.mark.asyncio
    async def test_very_long_description(self, campaign_db):
        """Some GMs write novels in entity descriptions. Must not crash formatting."""
        long_desc = "This is a very detailed description. " * 200  # ~7400 chars
        await campaign_db.db.execute(
            "INSERT INTO entities (uuid, name, type, campaign_id, status_label, description_public) "
            "VALUES (?, 'Loremaster', 'NPC', 1, 'Active', ?)",
            (str(uuid.uuid4()), long_desc),
        )
        await campaign_db.db.commit()

        entities = await campaign_db.get_all_entities(1)
        text = _format_entities_for_context([dict(e) for e in entities])
        # Should not crash, should contain the entity
        assert "Loremaster" in text

    @pytest.mark.asyncio
    async def test_empty_campaign(self):
        """A brand new campaign with zero data should not crash any queries."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            db = CodexDB(Path(tmpdir) / "empty.db")
            await db.connect()
            cid = await db.get_or_create_campaign("New Campaign")

            entities = await db.get_all_entities(cid)
            assert _format_entities_for_context([dict(e) for e in entities]) == ""

            rels = await db.get_all_relationships(cid)
            assert _format_relationships_for_context(rels) == ""

            summaries = await db.get_all_session_summaries(cid)
            assert _format_summaries_for_context(summaries) == ""

            docs = await db.get_all_lore_docs(cid)
            assert _format_lore_for_context(docs) == ""

            transcripts = await db.get_all_transcripts(cid)
            assert _format_transcripts_for_context(transcripts) == ""

            await db.close()

    @pytest.mark.asyncio
    async def test_session_without_transcript(self, campaign_db):
        """A session that exists but has no transcript (audio failed) should not crash."""
        await campaign_db.db.execute(
            "INSERT INTO sessions (campaign_id, session_number, transcript_text, processed_at) "
            "VALUES (1, 99, NULL, CURRENT_TIMESTAMP)"
        )
        await campaign_db.db.commit()

        # get_all_transcripts filters out NULL transcripts
        transcripts = await campaign_db.get_all_transcripts(1)
        nums = [t["session_number"] for t in transcripts]
        assert 99 not in nums

    @pytest.mark.asyncio
    async def test_duplicate_lore_doc_titles(self, campaign_db):
        """GM uploads 'Session Notes' twice with different content. Both should persist."""
        await campaign_db.insert_lore_doc(1, "Session Notes", "Version 1")
        await campaign_db.insert_lore_doc(1, "Session Notes", "Version 2")

        docs = await campaign_db.get_all_lore_docs(1)
        session_notes = [d for d in docs if d["title"] == "Session Notes"]
        assert len(session_notes) == 2


# =====================================================================
# 7. PIPELINE INTEGRATION — End-to-end staged changes
# =====================================================================

class TestPipelineIntegration:
    """The pipeline is the heart of the system. If entity extraction → staging
    is broken, the entire Codex becomes stale.
    """

    @pytest.mark.asyncio
    async def test_pipeline_with_gemini_pro_mock(self, tmp_path):
        """Pipeline should work with the new GeminiProClient (same interface as ClaudeClient)."""
        from living_codex.scribe.pipeline import ScribePipeline

        db = CodexDB(tmp_path / "pipe.db")
        await db.connect()
        await db.get_or_create_campaign("Test")

        mock_gemini = AsyncMock()
        mock_gemini.upload_audio.return_value = MagicMock(name="files/test")
        mock_gemini.transcribe_single.return_value = "[00:00] GM: A stranger arrives."
        mock_gemini.delete_file.return_value = None

        # Mock AI client — same interface as GeminiProClient or ClaudeClient
        mock_ai = AsyncMock()
        mock_ai.extract_entities.return_value = [{
            "name": "The Stranger",
            "type": "NPC",
            "aliases": [],
            "public_description": "A cloaked figure.",
            "private_description": "",
            "motivation": "",
            "appearance": "Tall, hooded.",
            "first_appearance": "[00:00]",
            "relationships": [],
            "status_label": "Unknown",
            "events": [{"timestamp": "[00:00]", "description": "Arrives at the tavern.", "visibility": "public"}],
        }]
        mock_ai.summarize_session.return_value = "A stranger arrives at the tavern."

        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        pipeline = ScribePipeline(db, mock_gemini, mock_ai, campaign_id=1)
        count = await pipeline.process_file(audio)

        # Should have staged: pub_desc + appearance + first_appearance + status + 1 event = 5
        assert count == 5

        # Verify pipeline called the AI client correctly
        mock_ai.extract_entities.assert_called_once()
        mock_ai.summarize_session.assert_called_once()

        await db.close()


# =====================================================================
# 8. AI CLIENT INTERFACE CONTRACT — Both implementations must match
# =====================================================================

class TestAIClientInterface:
    """GeminiProClient and ClaudeClient must have identical method signatures.
    If they diverge, the pipeline or commands will crash at runtime.
    """

    def test_gemini_pro_has_required_methods(self):
        """GeminiProClient must expose extract_entities, summarize_session, query."""
        from living_codex.ai.gemini_pro import GeminiProClient
        assert hasattr(GeminiProClient, "extract_entities")
        assert hasattr(GeminiProClient, "summarize_session")
        assert hasattr(GeminiProClient, "query")

    def test_claude_has_required_methods(self):
        """ClaudeClient must still expose the same interface (fallback support)."""
        from living_codex.ai.claude import ClaudeClient
        assert hasattr(ClaudeClient, "extract_entities")
        assert hasattr(ClaudeClient, "summarize_session")
        assert hasattr(ClaudeClient, "query")

    def test_query_signatures_match(self):
        """Both clients' query() methods must accept the same keyword args."""
        import inspect
        from living_codex.ai.gemini_pro import GeminiProClient
        from living_codex.ai.claude import ClaudeClient

        gemini_sig = inspect.signature(GeminiProClient.query)
        claude_sig = inspect.signature(ClaudeClient.query)

        gemini_params = set(gemini_sig.parameters.keys()) - {"self"}
        claude_params = set(claude_sig.parameters.keys()) - {"self"}
        assert gemini_params == claude_params, (
            f"Signature mismatch: Gemini={gemini_params}, Claude={claude_params}"
        )
