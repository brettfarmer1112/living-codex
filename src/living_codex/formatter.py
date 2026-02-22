"""Discord embed and UI builders for entity display.

All functions are pure / synchronous — no DB calls, no Discord client state
required beyond constructing Embed/View objects (discord.py allows this without
an active event loop).

The is_gm parameter is a Phase 4 hook: accepted now but has no effect in Phase 2
(private fields are never passed to the formatter at this phase).
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
        • Seen in: {campaign_name}

    The description is hard-capped at 500 chars with "… (truncated)" if needed.
    campaign_name is optional in the entity dict; defaults to "Unknown".
    """
    emoji = _status_emoji(entity.get("status_label"))
    status = entity.get("status_label") or "Unknown"
    entity_type = entity.get("type") or "Unknown"
    pub_desc = entity.get("description_public") or "No description available."
    campaign = entity.get("campaign_name") or "Unknown"

    bullet1 = f"• {emoji} {status}  |  {entity_type}"
    bullet2 = f"• {pub_desc}"
    bullet3 = f"• Seen in: {campaign}"

    raw_description = "\n".join([bullet1, bullet2, bullet3])
    description = _truncate(raw_description, _DESC_HARD_CAP)

    return discord.Embed(
        title=entity.get("name", "Unknown"),
        description=description,
        colour=discord.Colour.blurple(),
    )


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
    """Build a View with a 'View Full' button for expanded detail.

    Timeout is 300 s. timeout=None is reserved for Phase 4 items (Mission
    Reports) that need to survive bot restarts.
    """
    view = discord.ui.View(timeout=300)
    button = discord.ui.Button(
        label="View Full",
        style=discord.ButtonStyle.secondary,
    )

    async def _on_click(interaction: discord.Interaction) -> None:
        full_embed = build_entity_embed(entity)
        await interaction.response.send_message(embed=full_embed, ephemeral=True)

    button.callback = _on_click
    view.add_item(button)
    return view
