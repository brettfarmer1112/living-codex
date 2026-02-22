"""Tests for context formatting helpers used by /codex query."""

from living_codex.commands.codex import (
    _format_entities_for_context,
    _format_relationships_for_context,
    _format_summaries_for_context,
    _format_lore_for_context,
    _format_transcripts_for_context,
    _TRANSCRIPT_CHAR_BUDGET,
)


def test_format_entities_empty():
    assert _format_entities_for_context([]) == ""


def test_format_entities_basic():
    entities = [
        {"name": "Baron Vrax", "type": "NPC", "status_label": "Active",
         "description_public": "A ruthless baron."},
        {"name": "The Green Box", "type": "Location", "status_label": None,
         "description_public": ""},
    ]
    result = _format_entities_for_context(entities)
    assert "Baron Vrax | NPC | Active" in result
    assert "A ruthless baron." in result
    assert "The Green Box | Location | Unknown" in result


def test_format_relationships_empty():
    assert _format_relationships_for_context([]) == ""


def test_format_relationships():
    rels = [
        {"source_name": "Baron Vrax", "target_name": "Baroness Kora",
         "rel_type": "Rival", "citation": "Session 4"},
    ]
    result = _format_relationships_for_context(rels)
    assert "Baron Vrax \u2192 Rival \u2192 Baroness Kora [S4]" in result


def test_transcripts_truncation():
    """Transcripts exceeding the char budget should be truncated."""
    # Create sessions that exceed the budget
    sessions = []
    for i in range(1, 20):
        sessions.append({
            "session_number": i,
            "transcript_text": "x" * 10_000,
        })

    result = _format_transcripts_for_context(sessions)
    assert len(result) <= _TRANSCRIPT_CHAR_BUDGET + 500  # allow for headers/truncation marker
    assert "truncated" in result.lower()
