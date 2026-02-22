"""Discord embed and UI builders for entity display.

All functions are pure / synchronous — no DB calls, no Discord client state
required beyond constructing Embed/View objects (discord.py allows this without
an active event loop).
"""

from __future__ import annotations

import discord

# ---------------------------------------------------------------------------
# Status emoji map (keys are lower-cased for comparison)
# ---------------------------------------------------------------------------
_STATUS_EMOJI: dict[str, str] = {
    "active": "🟢",
    "grounded": "🔴",
    "inactive": "🔴",
    "dead": "💀",
    "destroyed": "💀",
}
_DEFAULT_EMOJI = "⬜"

# Hard cap on the embed description field.
# (Discord's hard limit is 4096 chars; we cap at 500 for readability.)
_DESC_HARD_CAP = 500
_FIELD_CAP = 1024  # Discord field value limit
_TRUNCATION_SUFFIX = "… (truncated)"


def _status_emoji(status: str | None) -> str:
    if not status:
        return _DEFAULT_EMOJI
    return _STATUS_EMOJI.get(status.casefold(), _DEFAULT_EMOJI)


def _truncate(text: str, cap: int) -> str:
    """Truncate *text* to at most *cap* chars, appending the truncation marker."""
    if len(text) <= cap:
        return text
    cut = cap - len(_TRUNCATION_SUFFIX)
    return text[:cut] + _TRUNCATION_SUFFIX


def build_entity_embed(entity: dict, *, is_gm: bool = False) -> discord.Embed:
    """Build the standard 3-bullet embed for a single entity.

    Layout (each line starts with •):
        • {emoji} {status}  |  {type}
        • {public description}
        • Last seen: Session N

    The description is hard-capped at 500 chars with "… (truncated)" if needed.
    last_seen_session resolves from entity dict key 'last_seen_session_number'.
    """
    emoji = _status_emoji(entity.get("status_label"))
    status = entity.get("status_label") or "Unknown"
    entity_type = entity.get("type") or "Unknown"
    pub_desc = entity.get("description_public") or "No description available."

    # Prefer a resolved session number; fall back to campaign name for legacy data
    last_seen_session = entity.get("last_seen_session_number")
    if last_seen_session is not None:
        seen_text = f"Last seen: Session {last_seen_session}"
    else:
        campaign = entity.get("campaign_name") or "Unknown"
        seen_text = f"Seen in: {campaign}"

    bullet1 = f"• {emoji} {status}  |  {entity_type}"
    bullet2 = f"• {pub_desc}"
    bullet3 = f"• {seen_text}"

    raw_description = "\n".join([bullet1, bullet2, bullet3])
    description = _truncate(raw_description, _DESC_HARD_CAP)

    embed = discord.Embed(
        title=entity.get("name", "Unknown"),
        description=description,
        colour=discord.Colour.blurple(),
    )

    foundry_id = entity.get("foundry_id")
    foundry_url = entity.get("foundry_url") or ""
    if foundry_id and foundry_url:
        entry_url = f"{foundry_url.rstrip('/')}/journal/{foundry_id}"
        embed.add_field(name="Foundry", value=f"[View in Foundry]({entry_url})", inline=False)

    return embed


def build_full_detail_embed(
    entity: dict,
    events: list[dict],
    relationships: list[dict],
) -> discord.Embed:
    """Build an expanded embed with full profile, events timeline, and relationships.

    entity keys expected:
        name, status_label, type, description_public, first_seen_session_number,
        last_seen_session_number, campaign_name (fallback)

    events: list of dicts with keys: event_text, session_number, event_timestamp, visibility
    relationships: list of dicts with keys: rel_type, target_name, citation, session_number
    """
    emoji = _status_emoji(entity.get("status_label"))
    name = entity.get("name", "Unknown")

    embed = discord.Embed(
        title=f"{name}  {emoji}",
        colour=discord.Colour.blurple(),
    )

    # Full description — no truncation cap in expanded view
    pub_desc = entity.get("description_public") or "No description available."
    embed.description = pub_desc[:4096]  # Discord embed description hard limit

    # First / last seen
    first_num = entity.get("first_seen_session_number")
    last_num = entity.get("last_seen_session_number")
    if first_num is not None:
        embed.add_field(name="First seen", value=f"Session {first_num}", inline=True)
    if last_num is not None:
        embed.add_field(name="Last seen", value=f"Session {last_num}", inline=True)

    # Type + status
    status = entity.get("status_label") or "Unknown"
    entity_type = entity.get("type") or "Unknown"
    embed.add_field(name="Type", value=entity_type, inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    # Events timeline
    if events:
        lines = []
        for ev in events:
            ts = ev.get("event_timestamp") or ""
            sn = ev.get("session_number", "?")
            text = ev.get("event_text", "")
            ts_part = f" {ts}" if ts else ""
            lines.append(f"• **S{sn}{ts_part}** {text}")
        events_text = _truncate("\n".join(lines), _FIELD_CAP)
        embed.add_field(name="Events", value=events_text, inline=False)

    # Relationships
    if relationships:
        lines = []
        for rel in relationships:
            rel_type = rel.get("rel_type", "")
            target = rel.get("target_name", "")
            citation = rel.get("citation", "")
            cite_part = f" ({citation})" if citation else ""
            lines.append(f"• {rel_type} → {target}{cite_part}")
        rels_text = _truncate("\n".join(lines), _FIELD_CAP)
        embed.add_field(name="Relationships", value=rels_text, inline=False)

    return embed


def build_candidates_select(candidates: list[dict]) -> discord.ui.Select:
    """Build a Select menu from a list of candidate entity dicts.

    Values are str(entity_id) — not entity names — so the select survives
    entity renames without stale references (plan landmine #3).
    """
    options = [
        discord.SelectOption(
            label=c["name"],
            value=str(c["id"]),
            description=(c.get("description_public") or "")[:100],
        )
        for c in candidates
    ]
    return discord.ui.Select(
        placeholder="Pick an entity…",
        options=options,
    )


def build_full_detail_view(entity: dict) -> discord.ui.View:
    """Build a View with a 'View Full' button. The callback is wired in commands/codex.py
    after async data (events, relationships) has been fetched.

    Timeout is 300 s.
    """
    view = discord.ui.View(timeout=300)
    button = discord.ui.Button(
        label="View Full",
        style=discord.ButtonStyle.secondary,
        custom_id="view_full",
    )
    view.add_item(button)
    return view
