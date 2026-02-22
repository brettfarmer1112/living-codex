# Changelog

All notable changes to living-codex are documented here.

---

## [Unreleased]

- Discord-based staged-change approval UI (currently CLI only via `approve.py`)
- Spoiler Shield — per-entity visibility controls for player vs. GM channels
- Audit log table for tracking all database mutations

---

## [0.1.0] — 2026-02-22

Initial release. All four core phases complete.

### Phase 4 — Foundry VTT Sync

- Foundry REST client (`sync/foundry.py`) — push entities and lore docs as journal entries
- Conflict Guard (`sync/guard.py`) — detects when a Foundry journal was manually edited since last sync; blocks overwrite until `force:True` is passed
- Push queue (`sync/push.py`) — offline retry queue with 5-minute polling
- `/codex sync` and `/codex syncstatus` commands
- `lore_docs` table — stores GM-uploaded markdown/text documents
- Context assembly for `/codex query` now includes lore docs and session summaries
- Parallel DB reads in `/codex query` for faster response times

### Phase 3 — Scribe Pipeline

- `AudioWatcher` — watchfiles-based directory monitor with 10-second debounce
- Scribe pipeline — transcription → entity extraction → session summary → staged changes
- Gemini 2.0 Flash transcription via Files API (audio never loaded into memory)
- Single-file and Craig multi-speaker input modes
- `staged_changes` table — AI extractions held for human review before touching entities
- `approve.py` — CLI script to review and commit staged changes
- `inspect_staged.py` — preview pending changes before approving
- `setup_rclone.sh` — Google Drive audio ingestion via rclone

### Phase 2 — Public Search

- `/codex check` with rapidfuzz fuzzy matching (threshold 70/100)
- `SearchResult` with three outcome types: direct match, candidate list, no match
- Discord select menu for ambiguous matches
- 3-Bullet Rule embeds: status badge, public description, first/last session seen
- "View Full" button to expand entity with events and relationships
- `seed.py` — demo entities for testing and development

### Phase 1 — Foundation

- `CodexConfig` — pydantic-settings with `CODEX_` prefix, `.env` file support
- `CodexDB` — SQLite database with WAL mode, FK constraints, 8 tables, 8 indexes
- Discord bot skeleton — `LivingCodex(commands.Bot)` with `setup_hook`
- AI router — selects Anthropic or Google GenAI SDK from model name prefix
- `ClaudeClient` and `GeminiClient` — unified interface for entity extraction, summarization, and queries
- `/codex ping`, `/codex lastsession`, `/codex query`, `/codex upload`
- Docker container with 0.5 CPU / 512 MB memory limits, log rotation
- Full pytest suite — 13 test files, ~400 test cases
