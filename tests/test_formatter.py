"""Tests for living_codex.formatter — pure unit tests, no DB or Discord client.

formatter.py functions are synchronous, so these are plain def tests
(no @pytest.mark.asyncio needed).
"""

import discord

from living_codex.formatter import build_entity_embed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(**overrides) -> dict:
    """Return a minimal entity dict with sane defaults, overridable per test."""
    base = {
        "id": 1,
        "name": "Baron Vrax",
        "type": "NPC",
        "status_label": "Active",
        "description_public": "A ruthless baron.",
        "campaign_name": "Armour Astir",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 3-bullet structure
# ---------------------------------------------------------------------------


def test_three_bullet_structure():
    """Description must contain exactly 3 lines beginning with '•'."""
    embed = build_entity_embed(_make_entity())
    bullets = [line for line in embed.description.splitlines() if line.startswith("•")]
    assert len(bullets) == 3


# ---------------------------------------------------------------------------
# Status emojis
# ---------------------------------------------------------------------------


def test_status_emoji_active():
    embed = build_entity_embed(_make_entity(status_label="Active"))
    assert "🟢" in embed.description


def test_status_emoji_grounded():
    embed = build_entity_embed(_make_entity(status_label="Grounded"))
    assert "🔴" in embed.description


def test_status_emoji_dead():
    embed = build_entity_embed(_make_entity(status_label="Dead"))
    assert "💀" in embed.description


# ---------------------------------------------------------------------------
# 500-char hard cap
# ---------------------------------------------------------------------------


def test_500_char_hard_cap():
    """A very long public description must not push description past 500 chars."""
    long_desc = "A" * 1000
    embed = build_entity_embed(_make_entity(description_public=long_desc))
    assert len(embed.description) <= 500


# ---------------------------------------------------------------------------
# Truncation marker
# ---------------------------------------------------------------------------


def test_truncation_marker():
    """When truncated, description ends with '… (truncated)'."""
    long_desc = "A" * 1000
    embed = build_entity_embed(_make_entity(description_public=long_desc))
    assert embed.description.endswith("… (truncated)")


# ---------------------------------------------------------------------------
# Embed title
# ---------------------------------------------------------------------------


def test_embed_title():
    """Embed title must equal the entity name."""
    embed = build_entity_embed(_make_entity(name="Baron Vrax"))
    assert embed.title == "Baron Vrax"
