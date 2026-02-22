# The Living Codex — Implementation Plan (Respec)

## Context

The Living Codex is a Discord bot that acts as a "search engine for the fiction" across TTRPG campaigns (Armour Astir, Delta Green, Monster of the Week). GMs upload session audio; AI extracts entities; players search lore mid-game without breaking flow.

**The repo is empty. This plan builds the MVP.**

Two features are P0 (zero tolerance for failure):
1. **Spoiler Shield** — players never see GM secrets
2. **Conflict Guard** — AI never overwrites GM's manual Foundry VTT edits

Host constraint: runs on a live AVAX validator node. The Codex must be invisible to the validator — hard-capped at 0.5 vCPU / 512MB RAM via Docker, all AI offloaded to Gemini API.

---

## Architecture Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Language | Python 3.10+ (asyncio) | Matches all existing MCPs |
| Bot framework | discord.py | Spec requirement, mature |
| Database | SQLite + aiosqlite (WAL mode) | Proven pattern from garmin/hevy/monarch MCPs |
| AI provider | Gemini directly (no abstraction) | Add abstraction only if switching providers |
| Search | rapidfuzz (Levenshtein) | Lightweight, no ML, spec thresholds 0.7/0.4 |
| Config | pydantic-settings | Matches monarch pattern |
| Audio capture | Craig bot → Google Drive → rclone pull | Per-speaker FLAC tracks, auto-uploaded, no manual handling |
| Foundry sync | REST API via httpx | Simpler than MCP Bridge for v1 |
| Deployment | Docker Compose with resource caps | `nice -n 19`, `ionice -c 3` in CMD |
| Spoiler control | Channel-based (GM channel vs player channel) + role check | Native Discord permissions, not custom ID tracking |
| GM identity | Discord role (`GM_ROLE_ID`), not single user ID | Supports multiple GMs |

---

## Project Structure

```
living-codex/
├── src/living_codex/
│   ├── main.py              # Entry point
│   ├── bot.py               # Discord bot setup, command registration
│   ├── config.py            # pydantic-settings
│   ├── database.py          # Schema, migrations, queries
│   ├── models.py            # Pydantic models
│   ├── search.py            # Fuzzy search (rapidfuzz)
│   ├── formatter.py         # 3-Bullet Rule embed builder
│   ├── permissions.py       # Two-layer access control (P0)
│   ├── commands/
│   │   ├── codex.py         # /codex check, /codex add
│   │   └── admin.py         # /codex sync, /codex status, /codex resolve
│   ├── ai/
│   │   ├── gemini.py        # Gemini Flash/Pro (direct, no abstraction)
│   │   └── prompts.py       # Transcription & extraction prompts
│   ├── scribe/
│   │   ├── pipeline.py      # Audio → transcript → extract → staged changes
│   │   ├── watcher.py       # Craig folder detector + file watcher
│   │   └── report.py        # Mission Report + Discord approval buttons
│   └── sync/
│       ├── foundry.py       # Foundry VTT REST client
│       └── guard.py         # Conflict Guard (hash comparison) (P0)
├── scripts/
│   ├── seed.py              # Test data loader
│   └── setup_rclone.sh      # rclone + cron for Craig → /inputs
├── tests/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Phase 1: Foundation (Week 1)

**Ship:** Docker container running, bot responds to `/ping`, DB schema created.

**Build:**
- `pyproject.toml` — deps: discord.py, aiosqlite, pydantic-settings, rapidfuzz, httpx, google-generativeai, watchfiles
- `config.py` — `CodexConfig(BaseSettings)` loading from `.env`: tokens, channel IDs, role IDs, API keys, paths
- `database.py` — tables: `entities` (with `campaign_id`, public/private descriptions, `foundry_hash`), `aliases` (with PK), `relationships` (with PK + timestamps), `campaigns`, `sessions`, `staged_changes`, `meta`. WAL mode, FK on, indexes on name/campaign_id/alias/type
- `bot.py` + `main.py` — basic bot with `/ping`, guild-specific commands for dev
- `Dockerfile` — Python 3.10-slim, CMD with `nice -n 19 ionice -c 3`
- `docker-compose.yml` — 0.5 vCPU, 512M hard cap, volume mounts for DB (NVMe) and audio (HDD)

**Verify:** `docker compose up` → `/ping` works → schema correct → `docker stats` under limits.

**Patterns to reuse:**
- Async DB class pattern from `garmin-health-mcp/src/garmin_health_mcp/database.py`
- pydantic-settings pattern from `monarch-mcp-enhanced/src/monarch_enhanced/`
- hatchling build backend + ruff config from existing MCPs

---

## Phase 2: Public Search (Week 2)

**Ship:** Players can `/codex check` entities with fuzzy matching. Public data only.

**Build:**
- `search.py` — rapidfuzz against `entities.name` + `aliases.alias`. Thresholds: ≥0.7 direct, 0.4–0.7 "Did you mean?" select menu (max 5), <0.4 no results
- `formatter.py` — 3-Bullet Rule embeds: status emoji (🟢/🔴/💀) + context + source. Hard cap 500 chars. "View Full" button for expanded detail (ephemeral follow-up)
- `commands/codex.py` — `/codex check [query]` → fuzzy search → format → ephemeral embed
- `scripts/seed.py` — test entities: Baron Vrax, The 4th Fleet (alias: "Sky Pirates"), The Green Box, Baroness Kora. Includes public/private descriptions, aliases, relationships

**Verify:**
- "Sky Pirates" → The 4th Fleet
- "Vrecks" → Baron Vrax (fuzzy)
- "Banana" → no results
- "Baron" → select menu (Vrax vs Kora)
- All embeds follow 3-Bullet Rule, under 500 chars

---

## Phase 3: The Scribe Pipeline (Week 3)

**Ship:** Audio in → entities extracted → staged changes in DB. No approval UI yet.

**Build:**
- `ai/gemini.py` — Gemini Flash for transcription, Pro for extraction. **Critical:** use Gemini Files API (`upload_file`) for audio — FLAC files are 300-500MB each, never load into Python memory
- `ai/prompts.py` — per-speaker transcription prompt (preferred, uses Craig filenames as speaker names), mixed-track fallback prompt, entity extraction prompt with public/private classification + PII redaction directive
- `scribe/watcher.py` — watches `/inputs` for Craig recording folders. Multiple FLAC files = per-speaker mode; single file = mixed mode. Also accepts mp3/wav for manual uploads
- `scribe/pipeline.py` — per-speaker mode: upload each FLAC individually to Gemini Flash with speaker name → merge transcripts → extract entities via Gemini Pro → write `staged_changes` table. Delete all audio after extraction (privacy requirement). Session-level attribution only
- `scripts/setup_rclone.sh` — cron job: `rclone move gdrive:/Craig /app/inputs/ --bwlimit 5M` every 10 min

**Verify:** Craig FLAC folder → pipeline detects multi-track → staged_changes populated → audio deleted → RAM stays under cap.

---

## Phase 4: GM Tools + Spoiler Shield (Week 4)

**Ship:** Two-layer permissions. Mission Report with approve/reject. GM secrets visible only in GM channel.

**Build:**
- `permissions.py` — **Two layers (defense in depth):**
  - Layer 1 (query): DB queries omit `description_private` unless caller has GM role
  - Layer 2 (formatter): strips any private content that leaked through Layer 1
  - Channel routing: `#codex-intel` = public only, `#codex-gm` = public + private (requires GM role)
  - Default: unknown source = private (never surface to players)
- Update `/codex check` with permissions filter
- `/codex add [name] [type] [description]` — GM channel only, creates Draft entity
- `scribe/report.py` — Mission Report in GM channel (non-ephemeral). Shows new/updated/conflict counts. Discord buttons with `custom_id` + `View(timeout=None)` (survives restarts). Pending report IDs stored in `staged_changes`. "Approve All" / "Review Details" / "Reject"

**Verify (P0 — zero tolerance):**
- Same entity queried in player channel → public only; in GM channel with GM role → public + private with 🔒 SECRET
- Player in GM channel (no GM role) → still no private data
- GM approves report → entities written to DB
- GM rejects → nothing written
- **Screenshot comparison: player vs GM view of The Green Box**

---

## Phase 5: Foundry Sync + Conflict Guard (Week 5)

**Ship:** Foundry VTT sync with conflict detection. Alpha release.

**Build:**
- `sync/foundry.py` — httpx async client. Retry with backoff on 5xx (3 attempts). Offline detection → queue changes in `sync_queue` table → process queue when Foundry returns
- `sync/guard.py` — **The P0 algorithm:**
  1. Fetch journal from Foundry
  2. SHA-256 hash current content
  3. Compare with `entities.foundry_hash`
  4. Match → update Foundry + update stored hash
  5. Mismatch → **ABORT** + notify GM ("Conflict detected for [entity]. Manual edit found. Sync skipped.")
  6. New entity → create journal entry
- `commands/admin.py` — `/codex sync`, `/codex status`, `/codex resolve [entity]`
- Formatter update: add `[View in Foundry]` link when `foundry_id` exists
- Daily SQLite backup to Google Drive via rclone

**Verify (P0 — zero tolerance):**
- Sync entity → journal created in Foundry
- Manually edit journal → re-sync → "Conflict detected" message, Foundry content preserved
- Foundry offline → `/codex check` still works from SQLite
- Full end-to-end: audio → process → approve → sync → Foundry journal created
- Stress test: 50 queries/sec + audio pipeline → CPU ≤ 50%, RAM ≤ 512MB

---

## Deployment

**Pre-deploy gate (run before first `docker compose up`):**
```bash
# Validator healthy?
curl -s -X POST --data '{"jsonrpc":"2.0","id":1,"method":"health.health"}' \
  -H 'content-type:application/json' http://127.0.0.1:9650/ext/health | jq .result.healthy

# Rewarding stake nominal?
curl -s 'http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake'

# Resources have headroom?
free -h && df -h /
```

**Deploy:** Build locally on Windows first (non-Docker) → SCP to validator at `~/living-codex/` → `docker compose up -d` → verify `/ping` + `docker stats` → monitor 1 hour.

**Kill switch:** `docker compose down` — instant, zero residue. The Codex opens no inbound ports, adds no UFW rules, touches no validator files or services.

---

## Test Plan (Summary)

~61 automated tests. One-third concentrated on the two P0 features.

| File | Phase | Priority | Key Assertions |
|------|-------|----------|----------------|
| `test_database.py` | 1 | Medium | Schema, WAL, FK, indexes, constraints |
| `test_search.py` | 2 | High | Exact/fuzzy/alias/ambiguous/noise thresholds |
| `test_formatter.py` | 2 | Medium | 3-Bullet structure, <500 chars, clean truncation |
| **`test_permissions.py`** | **4** | **P0** | **Player never sees private. GM sees private. Channel routing. Unknown defaults to private. Two-layer defense.** |
| `test_pipeline.py` | 3 | Medium | Staged changes written, per-speaker mode, mixed fallback |
| `test_gemini.py` | 3 | Medium | Files API used (not inline bytes), correct model selection |
| **`test_guard.py`** | **5** | **P0** | **Hash match → sync. Hash mismatch → ABORT. Never overwrites. Offline → queued.** |
| `test_report.py` | 4 | High | Approve writes, reject discards, survives restart |

**Alpha exit criteria:** 100% pass on test_permissions + test_guard. Resource compliance under load. Full end-to-end path works.

---

## Deferred (Not MVP)

- Audit log table
- Exception hierarchy (`errors.py`)
- Network bandwidth limiting (infra, not app code)
- Model splitting (`models.py` submodules)
- Lore builder, character builder, GM asset assistant (future ideas from design doc)
