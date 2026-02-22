"""Tests for living_codex.search — all against the seeded_db fixture."""

import pytest

from living_codex.search import search


async def test_exact_alias_match(seeded_db):
    """'Sky Pirates' is an alias for The 4th Fleet — should resolve directly."""
    result = await search(seeded_db, "Sky Pirates")
    assert result.kind == "direct"
    assert result.entity is not None
    assert result.entity["name"] == "The 4th Fleet"


async def test_fuzzy_name_match(seeded_db):
    """'Vrex' scores 75 against alias 'Vrax' (WRatio) — resolves to Baron Vrax.
    Note: WRatio uses 0-100 scale; plan thresholds are 70 (direct) / 40 (candidate).
    """
    result = await search(seeded_db, "Vrex")
    assert result.kind == "direct"
    assert result.entity is not None
    assert result.entity["name"] == "Baron Vrax"


async def test_no_match(seeded_db):
    """'Frobozz' has zero letter overlap with any seeded entity or alias."""
    result = await search(seeded_db, "Frobozz")
    assert result.kind == "none"
    assert result.entity is None
    assert result.candidates == []


async def test_ambiguous_name_prefix(seeded_db):
    """'Baron' scores ≥70 against both Baron Vrax and Baroness Kora
    — multiple direct hits triggers downgrade to candidates."""
    result = await search(seeded_db, "Baron")
    assert result.kind == "candidates"
    names = [c["name"] for c in result.candidates]
    assert "Baron Vrax" in names
    assert "Baroness Kora" in names


async def test_case_insensitive(seeded_db):
    """Folded query 'baron vrax' should match 'Baron Vrax' directly."""
    result = await search(seeded_db, "baron vrax")
    assert result.kind == "direct"
    assert result.entity is not None
    assert result.entity["name"] == "Baron Vrax"


async def test_empty_string(seeded_db):
    """Empty query should short-circuit to none without hitting the DB."""
    result = await search(seeded_db, "")
    assert result.kind == "none"


async def test_deduplicated_entity(seeded_db):
    """'Vrax' matches Baron Vrax via both name (fuzzy) and alias (exact).
    After merging best-score-per-entity, it should appear only once as direct."""
    result = await search(seeded_db, "Vrax")
    assert result.kind == "direct"
    assert result.entity is not None
    assert result.entity["name"] == "Baron Vrax"
    # Confirm the entity does not also appear in candidates (dedup guard)
    assert result.candidates == []
