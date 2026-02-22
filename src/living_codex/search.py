"""Entity search using rapidfuzz fuzzy matching.

Score thresholds use the 0-100 scale that rapidfuzz WRatio returns.
(The plan spec used 0.7/0.4 notation — those are equivalent to 70/40 here.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from rapidfuzz import fuzz

from living_codex.database import CodexDB

# ---------------------------------------------------------------------------
# Score thresholds (rapidfuzz WRatio returns 0-100, NOT 0-1)
# ---------------------------------------------------------------------------
_DIRECT_THRESHOLD = 70
_CANDIDATE_THRESHOLD = 40
_MAX_CANDIDATES = 5


# ---------------------------------------------------------------------------
# Row type aliases — thin wrappers so callers never receive raw aiosqlite Row
# objects (which aren't picklable and are harder to test).
# ---------------------------------------------------------------------------


class EntityRow(TypedDict):
    id: int
    uuid: str
    name: str
    type: str
    campaign_id: int
    status_label: str | None
    description_public: str | None
    description_private: str | None


class CandidateRow(TypedDict):
    id: int
    uuid: str
    name: str
    type: str
    campaign_id: int
    status_label: str | None
    description_public: str | None
    description_private: str | None
    score: float


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    kind: Literal["direct", "candidates", "none"]
    entity: EntityRow | None = None
    candidates: list[CandidateRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core search function
# ---------------------------------------------------------------------------


async def search(db: CodexDB, query: str) -> SearchResult:
    """Search all entities and aliases for *query* using fuzzy matching.

    Returns a SearchResult whose kind is:
      - "direct"     — exactly one entity scored ≥ 70
      - "candidates" — multiple entities scored ≥ 70, OR at least one 40-69
      - "none"       — nothing scored ≥ 40 (or query is empty)
    """
    if not query.strip():
        return SearchResult(kind="none")

    query_folded = query.casefold()

    # --- Pass 1: score against entity names ---
    entity_rows = await db.get_all_entities()
    # best maps entity_id → (best_score, EntityRow)
    best: dict[int, tuple[float, EntityRow]] = {}

    for row in entity_rows:
        entity_id = row["id"]
        score = fuzz.WRatio(query_folded, row["name"].casefold())
        entity: EntityRow = {
            "id": row["id"],
            "uuid": row["uuid"],
            "name": row["name"],
            "type": row["type"],
            "campaign_id": row["campaign_id"],
            "status_label": row["status_label"],
            "description_public": row["description_public"],
            "description_private": row["description_private"],
        }
        if entity_id not in best or score > best[entity_id][0]:
            best[entity_id] = (score, entity)

    # --- Pass 2: score against aliases ---
    # An entity matched by both name and alias counts once (best score wins).
    alias_rows = await db.get_all_aliases()
    for row in alias_rows:
        entity_id = row["entity_id"]
        score = fuzz.WRatio(query_folded, row["alias"].casefold())
        if entity_id in best and score > best[entity_id][0]:
            # Alias gave a better score — update score, keep entity data
            best[entity_id] = (score, best[entity_id][1])

    # --- Classify ---
    # Collect everything at or above the candidate floor, sorted by score desc
    qualified = sorted(
        [
            (score, entity)
            for score, entity in best.values()
            if score >= _CANDIDATE_THRESHOLD
        ],
        key=lambda x: x[0],
        reverse=True,
    )

    if not qualified:
        return SearchResult(kind="none")

    direct_hits = [(s, e) for s, e in qualified if s >= _DIRECT_THRESHOLD]

    if len(direct_hits) == 1:
        # Exactly one entity above the direct threshold → unambiguous match
        _, entity = direct_hits[0]
        return SearchResult(kind="direct", entity=entity)

    # Multiple ≥70 (ambiguous) OR nothing ≥70 but some 40-69 → candidates
    top = qualified[:_MAX_CANDIDATES]
    candidates: list[CandidateRow] = [
        {**entity, "score": score}  # type: ignore[misc]
        for score, entity in top
    ]
    return SearchResult(kind="candidates", candidates=candidates)
