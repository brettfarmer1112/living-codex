# The Living Codex — Implementation Plan

## Context

The Living Codex is a headless, AI-powered GM assistant for TTRPG campaigns (Armour Astir, Delta Green, Monster of the Week). It operates as a Discord bot backed by SQLite and Google's Gemini API, with sync to Foundry VTT. It runs on a live AVAX validator node under strict resource constraints (0.5 vCPU, 512MB RAM).

The design doc is finalized. The repo is empty. This plan turns the spec into shippable code across 5 phases, each delivering testable value. GM tools and Foundry sync are deferred to later phases so the player-facing search experience ships first.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Language** | Python 3.10+ | Matches all existing MCPs; asyncio native |
| **Bot Framework** | discord.py | Spec requirement; mature async library |
| **Channel Strategy** | Dedicated channels | GM-only channel for secrets; player channel for public intel. Discord permissions handle access control natively. |
| **Database** | SQLite + aiosqlite | Proven pattern across garmin/hevy/monarch MCPs |
| **AI Provider** | Gemini direct (no abstraction) | Write Gemini impl directly; refactor to abstraction only if switching providers |
| **Config** | pydantic-settings | Matches monarch pattern; type-safe .env loading |
| **Search** | rapidfuzz (Levenshtein) | Spec calls for 0.7/0.4 thresholds; lightweight, no ML |
| **Foundry Sync** | REST API via httpx | Simpler than MCP Bridge for v1; can add bridge later |
| **Audio Capture** | Craig bot (paid tier) → Google Drive auto-sync → rclone pull | Per-speaker FLAC tracks, auto-uploaded; no manual file handling |
| **Deployment** | Docker Compose | Spec requirement with hard resource caps |
| **Dep Management** | pyproject.toml + hatchling | Matches garmin/monarch convention |

---

## Audio Capture: Craig Bot Integration

[Craig](https://craig.chat/) is a Discord bot that records voice channels with **multi-track, per-speaker audio**. Each participant gets a separate FLAC file, perfectly synced. The paid tier ($4/mo) enables **automatic Google Drive sync** — recordings upload to a `Craig/` folder on the GM's Drive immediately after the session ends.

This eliminates the entire "drag audio into a folder" workflow from the design doc. The pipeline becomes fully automated:

```
Discord voice channel (game session)
  → Craig bot records per-speaker FLAC tracks
  → Craig auto-uploads to GM's Google Drive (paid tier)
  → rclone polls Drive every 10 min, pulls to /inputs on server
  → Scribe pipeline processes FLAC → Gemini API
  → Staged changes written to DB
  → Mission Report sent to GM in Discord
```

### What Craig Gives Us

| Feature | Detail |
|---------|--------|
| **Format** | FLAC (lossless) per speaker, or single mixed track |
| **Per-speaker tracks** | Each player gets their own audio file — dramatically improves transcription accuracy (no speaker diarization needed) |
| **Auto-sync** | Patron tier auto-uploads to Google Drive, OneDrive, or Dropbox |
| **Max duration** | 6 hours continuous |
| **Retention** | 7 days on Craig's servers (but auto-synced to Drive immediately) |
| **Auto-record** | $4 tier can auto-start recording when GM joins voice channel |
| **Session commands** | `/join` to start, `/stop` to end, `/note` for timestamped bookmarks |

### Impact on the Scribe Pipeline (Phase 3)

**Before Craig:** GM manually uploads a single mixed MP3. Gemini must figure out who's speaking.

**With Craig:** Per-speaker FLAC files arrive automatically. Two options for transcription:

1. **Per-speaker transcription (recommended):** Send each speaker's FLAC to Gemini Flash individually with the speaker's name. The transcript is pre-attributed — no speaker diarization needed. This is simpler, more accurate, and costs the same.
2. **Mixed track:** Craig can also produce a single mixed file. Falls back to the original pipeline design.

The Scribe pipeline should prefer per-speaker tracks when available (check for multiple FLAC files in the same Craig recording folder) and fall back to mixed track otherwise.

### File Size Estimates

FLAC is lossless and larger than MP3:
- 3-hour session, single speaker track: ~300-500MB FLAC
- 3-hour session, 5 speakers: ~1.5-2.5GB total across all tracks
- These go to HDD (`/mnt/mediadrive`), not NVMe. Plenty of room (19TB free).
- Files are deleted after Gemini processes them (per design doc privacy requirement).

### Config Additions

```
CODEX_GDRIVE_CRAIG_PATH=gdrive:/Craig    # rclone remote path to Craig's upload folder
CODEX_INPUT_DIR=/app/inputs               # local dir where rclone deposits files
```

---

## Validator Safety & Deployment Strategy

The Living Codex runs on a live AVAX validator node earning ~167 AVAX in potential rewards with 44 delegators and ~10,189 AVAX in delegated stake. Dropping below 80% uptime forfeits all staking rewards. **The validator's survival is the single non-negotiable constraint.** Every design decision below exists to guarantee the Codex cannot harm it.

### Host Environment

| Resource | Spec | Current Usage | Codex Budget |
|----------|------|---------------|--------------|
| CPU | AMD Ryzen 5 5500 (6C/12T) | avalanchego + monitoring + media stack | **0.5 vCPU hard cap** (4% of total) |
| RAM | 32GB DDR4 | avalanchego (~4-8GB), Plex, Docker media stack | **512MB hard cap** (~1.5% of total) |
| NVMe (root) | 2TB, 41% used (720GB free) | avalanchego data, OS, Docker volumes | **<100MB** (SQLite DB, est. year 1) |
| HDD | 21TB at `/mnt/mediadrive`, 9% used | Media files, Plex library | Raw audio staging (transient, ~7GB/year) |
| Network | Residential, Chicago | P2P gossip on 9651, Plex on 32400 | **Egress limited to 5Mbps** for API calls |

### Resource Isolation (Defense in Depth)

The Codex uses **four layers** of isolation to ensure it cannot starve the validator:

**Layer 1: Docker resource limits** — hard caps that the kernel enforces.
```yaml
# docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '0.50'
      memory: 512M
    reservations:
      memory: 128M
```
If the container tries to exceed 512MB, the OOM killer terminates it. The validator is unaffected.

**Layer 2: Process priority** — even within its CPU budget, the Codex yields.
```dockerfile
# Dockerfile CMD
CMD ["nice", "-n", "19", "ionice", "-c", "3", "python", "-m", "living_codex.main"]
```
- `nice -n 19`: Lowest CPU scheduling priority. If avalanchego needs cycles, the Codex waits.
- `ionice -c 3`: Idle I/O class. Disk I/O only happens when no other process is reading/writing. This protects the NVMe from contention during avalanchego block verification.

**Layer 3: No local compute for AI** — all heavy processing is offloaded.
- Transcription → Gemini Flash API (Google's servers)
- Entity extraction → Gemini Pro API (Google's servers)
- Audio files uploaded via Gemini Files API — **never loaded into Python memory**
- The Codex container does: SQLite queries (<1ms), fuzzy string matching (rapidfuzz C extension, microseconds), Discord websocket (idle most of the time), HTTP calls to external APIs

The CPU/RAM caps exist as insurance. In practice, the Codex should idle at **~40-60MB RSS** and **<1% CPU** during normal operation (search queries). The only CPU spike is JSON parsing of Gemini API responses during Scribe pipeline runs, which is brief and capped by Layer 1+2.

**Layer 4: Network isolation** — no new inbound ports.
- The Codex opens **zero** inbound ports. It connects outward to Discord (websocket) and Gemini API (HTTPS).
- Audio ingestion uses **pull-based polling** (rclone from Google Drive), not inbound uploads.
- Foundry sync is outbound HTTPS only.
- UFW rules remain unchanged: SSH (22), Avalanche P2P (9651), Plex (32400) stay open. Nothing added.

### Storage Strategy

| Data | Location | Rationale |
|------|----------|-----------|
| `codex.db` (live database) | NVMe at `./data/codex.db` | Sub-millisecond query latency for Discord responses |
| Craig FLAC audio (transient) | HDD at `/mnt/mediadrive/codex_raw/` | Per-speaker FLAC tracks are 300-500MB each; keeps them off NVMe. Deleted after Gemini processes them. |
| Processed transcripts | Deleted after entity extraction | Per design doc: "post session transcription mp3 is destroyed" |
| Backups | Google Drive via rclone | Off-site disaster recovery, daily cron |

The SQLite database will be <100MB after a year of use. It is negligible compared to the 720GB free on the NVMe. WAL mode means reads never block writes and vice versa — no contention with avalanchego's own disk I/O.

### What Happens If Things Go Wrong

| Failure Mode | Impact on Validator | Automatic Response |
|-------------|--------------------|--------------------|
| Codex OOM (exceeds 512MB) | **None.** OOM killer targets the Codex container. | Container dies, `restart: unless-stopped` brings it back. |
| Codex CPU spike | **None.** `nice -n 19` means avalanchego preempts it. Docker cpus cap at 0.50. | Spike is capped; validator gets priority. |
| Codex crashes/hangs | **None.** It's an isolated Docker container. | `restart: unless-stopped` auto-recovers. |
| Gemini API timeout (30s+) | **None.** It's an outbound HTTPS call, not local compute. | Pipeline logs error, retries once, then stops. |
| Disk full on NVMe | **Potential risk.** Could affect avalanchego if root fills up. | Codex DB is <100MB. Audio goes to HDD. Log rotation at 10MB x 3 files. Monitor via existing `disk >80%` alert. |
| Network saturation | **Low risk.** Gossip on 9651 is lightweight. | Egress capped at 5Mbps. Gemini API calls are small payloads. Audio uploads are bandwidth-limited by rclone `--bwlimit 5M`. |

### Monitoring Integration

The validator already has a monitoring stack (Prometheus + Grafana + validator-triage bot with Discord alerts). The Codex integrates with this, not replaces it.

**Existing alerts that protect the validator from the Codex:**
- **Memory >85%**: Validator triage bot fires warning. If the Codex were somehow leaking despite the 512MB cap, system-level memory pressure would trigger this alert.
- **Disk >80%**: Triage bot fires warning. The Codex's log rotation (10MB x 3) and audio-on-HDD strategy keeps NVMe usage negligible.
- **Rewarding stake <95%**: Triage bot fires warning. If the Codex were somehow causing network disruption, this metric would drop and alert.

**New monitoring added by the Codex (Phase 1):**
- `docker stats` output for the Codex container should be checked after deployment and during Scribe pipeline runs
- Add a `CODEX_HEALTH` metric endpoint (simple HTTP on localhost, no UFW change needed) that Prometheus can scrape — reports: uptime, last query time, DB size, container RSS

**Pre-deployment health gate:**
Before starting the Codex container for the first time, verify:
```bash
# 1. Validator is healthy
curl -s -X POST --data '{"jsonrpc":"2.0","id":1,"method":"health.health"}' \
  -H 'content-type:application/json' http://127.0.0.1:9650/ext/health | jq .result.healthy

# 2. Rewarding stake is nominal
curl -s 'http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake' | jq .data.result[0].value[1]

# 3. Resources have headroom
free -h  # Confirm >4GB available
df -h /  # Confirm <70% used
```

### Deployment Procedure

**First deployment (Phase 1):**
1. Build and test locally on Windows first (non-Docker, just `python -m living_codex.main` with a test Discord server)
2. SCP the project to the validator: `scp -r` to `/home/brettfarmer/living-codex/`
3. Run the pre-deployment health gate (above)
4. `docker compose up -d` — starts in background
5. Verify: `/ping` in Discord, `docker stats` shows resource limits applied
6. Monitor for 1 hour: check validator health, rewarding stake, `docker stats`
7. If anything looks wrong: `docker compose down` — instant rollback, zero residue

**Subsequent deployments (Phase 2+):**
1. `docker compose down` — stop existing container
2. `git pull` or SCP updated files
3. `docker compose build && docker compose up -d`
4. Verify `/ping`, check `docker stats`
5. Run phase-specific verification steps

**Emergency kill switch:**
```bash
docker compose down    # Graceful stop
# OR
docker kill living-codex  # Immediate kill if unresponsive
```
The Codex leaves no residual processes, no cron jobs (except the rclone poller added in Phase 5), and no system-level changes. Removing it is: stop container, delete directory.

### What the Codex Does NOT Touch

- `avalanchego.service` — never modified, never restarted
- `~/.avalanchego/staking/` — never accessed
- Ports 9650, 9651 — never touched
- UFW rules — no changes
- `unattended-upgrades` — no changes
- Prometheus/Grafana config — no changes (optional scrape target only)
- Any systemd service — the Codex is Docker-only, not a systemd service

### Co-tenancy with Existing Docker Stack

The validator already runs a Docker media stack (gluetun, qbittorrent, media-processor) at `~/docker/`. The Codex lives in its own `docker-compose.yml` at `~/living-codex/`, on the **default bridge network** — completely separate from the media stack's network.

The two Docker stacks share the Docker daemon but have independent resource limits. The media stack has its own CPU/RAM constraints. There is no port conflict (the Codex opens no ports). The only shared resource is the Docker daemon itself, which is lightweight.

---

## Project Structure

```
living-codex/
├── src/living_codex/
│   ├── __init__.py
│   ├── main.py              # Entry point, bot startup
│   ├── bot.py               # Discord bot setup, command registration
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── codex.py          # /codex check, /codex add, /codex history
│   │   └── admin.py          # /codex sync, /codex status, /codex resolve
│   ├── database.py           # Schema, migrations, query helpers
│   ├── models.py             # Pydantic models (Entity, StagedChange, etc.)
│   ├── config.py             # pydantic-settings config
│   ├── search.py             # Fuzzy search with rapidfuzz
│   ├── formatter.py          # 3-Bullet Rule Discord embed builder
│   ├── permissions.py        # Two-layer access control (query + formatter)
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── gemini.py         # Gemini Flash/Pro implementation (no abstraction)
│   │   └── prompts.py        # Extraction & summarization prompts
│   ├── scribe/
│   │   ├── __init__.py
│   │   ├── pipeline.py       # Orchestrates transcribe → extract → diff
│   │   ├── watcher.py        # Craig session detector + file watcher
│   │   └── report.py         # Mission Report generation + Discord buttons
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── foundry.py        # Foundry VTT API client
│   │   └── guard.py          # Conflict Guard (hash comparison logic)
│   └── utils.py              # Shared helpers (hashing, truncation)
├── scripts/
│   ├── seed.py               # Load test entities, aliases, relationships
│   └── setup_rclone.sh       # Configure rclone + cron for Craig → /inputs
├── tests/
│   ├── test_search.py
│   ├── test_permissions.py
│   ├── test_guard.py
│   ├── test_formatter.py
│   └── conftest.py
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
└── CLAUDE.md
```

---

## Phase 1: The Foundation (Week 1)

**Goal:** Running Discord bot with database, responds to `/ping`.

### Tasks

1. **Project scaffolding**
   - `pyproject.toml` with deps: `discord.py`, `aiosqlite`, `pydantic-settings`, `rapidfuzz`, `httpx`, `python-dotenv`
   - `.gitignore` (Python + .env + data/)
   - `.env.example` with `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, `GM_ROLE_ID`, `GM_CHANNEL_ID`, `PLAYER_CHANNEL_ID`, `GEMINI_API_KEY`
   - `CLAUDE.md` with project-specific skills

2. **Config module** — `src/living_codex/config.py`
   - `CodexConfig(BaseSettings)` with env_prefix `CODEX_`
   - Fields: `discord_token`, `discord_guild_id`, `gm_role_id`, `gm_channel_id`, `player_channel_id`, `gemini_api_key`, `db_path`, `input_dir`, `foundry_url`, `foundry_api_key`

3. **Database** — `src/living_codex/database.py`
   - Schema from spec: `entities` (with `campaign_id`, without `status_color`), `aliases` (with PK), `relationships` (with PK + timestamps)
   - Add `campaigns` table for multi-campaign support
   - Add `sessions` table for session-level attribution
   - Add `meta` table for sync state / key-value settings
   - Indexes on: `entities(name)`, `entities(campaign_id)`, `aliases(alias)`, `aliases(entity_id)`, `entities(type)`
   - `CodexDB` class following garmin/hevy async pattern
   - WAL mode, foreign keys ON

4. **Discord bot skeleton** — `src/living_codex/bot.py` + `main.py`
   - Basic bot with `/ping` command
   - Slash command group: `/codex`
   - Graceful shutdown, logging setup

5. **Docker setup**
   - `Dockerfile`: Python 3.10-slim, pip install
   - `docker-compose.yml`: resource limits (0.5 vCPU, 512MB), volume mounts
   - Dockerfile CMD uses `nice -n 19 ionice -c 3` prefix to yield to AVAX validator

### Verification
- `docker compose up` → bot comes online in Discord
- `/ping` returns "Pong" with latency
- SQLite DB created with correct schema
- Container stays under resource caps (`docker stats`)

---

## Phase 2: Public Search (Week 2)

**Goal:** Anyone can search entities with fuzzy matching. Public data only — no permissions layer yet.

### Tasks

1. **Fuzzy search** — `src/living_codex/search.py`
   - rapidfuzz against `entities.name` + `aliases.alias`
   - Thresholds: ≥0.7 → direct match, 0.4–0.7 → "Did you mean?" select menu, <0.4 → no results
   - Return ranked results, cap select menu at 5 options

2. **Formatter** — `src/living_codex/formatter.py`
   - Discord embed builder following the 3-Bullet Rule:
     1. Status line with emoji (🟢/🔴/💀)
     2. Context line (most relevant fact)
     3. Source line (session citation)
   - Hard cap at 2000 chars; truncate with "…"
   - "View Full" button for expanded detail (ephemeral follow-up)

3. **Commands** — `src/living_codex/commands/codex.py`
   - `/codex check [query]` — fuzzy search → format → ephemeral embed (public fields only)
   - Ambiguous results → Discord select menu dropdown
   - Use guild-specific commands during dev (instant sync vs 1-hour global)

4. **Seed data** — `scripts/seed.py`
   - Populates entities with public descriptions, aliases, relationships, campaign IDs
   - Sample data: Baron Vrax, The 4th Fleet, The Green Box, Baroness Kora
   - Include aliases ("Sky Pirates" → "The 4th Fleet")

5. **Tests** — `tests/test_search.py`, `tests/test_formatter.py`
   - Fuzzy match thresholds, edge cases, embed formatting

### Verification
- `/codex check "Sky Pirates"` returns The 4th Fleet embed
- `/codex check "Vrecks"` fuzzy-matches to Baron Vrax
- `/codex check "Banana"` returns "No results found"
- `/codex check "Baron"` shows select menu (Vrax vs Baroness Kora)
- All embeds under 500 chars, follow 3-Bullet Rule
- Test suite passes

---

## Phase 3: The Scribe (Week 3)

**Goal:** Craig audio → transcript → entity extraction → staged changes in DB. No approval UI yet — that's Phase 4.

### Tasks

1. **Gemini integration** — `src/living_codex/ai/gemini.py`
   - Direct implementation (no abstraction layer) using `google-generativeai` SDK
   - Flash for transcription, Pro for entity extraction
   - **Critical:** Use Gemini Files API (`upload_file`) for audio — never load FLAC into Python memory (500MB+ per-speaker files vs 512MB container)

2. **Prompt engineering** — `src/living_codex/ai/prompts.py`
   - **Per-speaker transcription prompt** (preferred): "Transcribe this audio from [Speaker Name]. Output JSON with timestamps."
   - **Mixed track transcription prompt** (fallback): "Transcribe. Identify speakers. Output JSON."
   - Extraction prompt: "Given transcript and current entity list, identify new/updated Assets, Factions, NPCs, Locations, Clues. Classify public vs private. Output structured JSON."
   - Include PII redaction directive

3. **Craig session detector** — `src/living_codex/scribe/watcher.py`
   - Watch `/inputs` directory for new Craig recording folders (Craig creates a folder per recording with per-speaker FLAC files)
   - Detect per-speaker tracks: multiple `.flac` files in same folder → per-speaker mode
   - Detect single mixed track: one `.flac` file → mixed mode (fallback)
   - Also accept `.mp3`/`.wav` for manual uploads
   - On new session detected: trigger pipeline, move to `/processed` after completion
   - Alternative: manual `/codex ingest` command for testing

4. **Scribe pipeline** — `src/living_codex/scribe/pipeline.py`
   - **Per-speaker mode:** Upload each speaker's FLAC to Gemini Flash individually with speaker name → merge transcripts chronologically → extract entities
   - **Mixed mode:** Upload single file → transcribe with speaker diarization → extract entities
   - `process_session(session_path)` → transcribe → extract → write `staged_changes` table in SQLite
   - Session-level attribution (not per-sentence)
   - Pipeline stops after writing staged changes; no approval flow yet
   - Delete all audio files after successful extraction (privacy requirement)

5. **rclone pull setup** — `scripts/setup_rclone.sh`
   - Configure rclone remote for Google Drive
   - Cron job: `rclone move gdrive:/Craig /app/inputs/ --bwlimit 5M` every 10 minutes
   - Bandwidth-limited to protect validator network

### Verification
- Craig recording folder with per-speaker FLAC files in `/inputs` → pipeline detects multi-track, processes each speaker
- Single mixed FLAC → pipeline falls back to mixed mode
- `staged_changes` table has correct entity data with public/private classification
- Transcripts have correct speaker attribution (from Craig filenames, not AI guessing)
- Session recorded in `sessions` table
- Resource usage stays under caps during processing
- No raw audio persists after processing

---

## Phase 4: GM Tools (Week 4)

**Goal:** GM can review, approve/reject staged changes. Role-based permissions. Spoiler safety.

### Tasks

1. **Permissions (two-layer)** — `src/living_codex/permissions.py`
   - **Layer 1 (query-level):** DB queries never return `description_private` unless caller is verified GM (role check)
   - **Layer 2 (formatter-level):** Formatter strips any private content as a second pass (defense in depth)
   - Channel-based routing: `#codex-intel` → public only, `#codex-gm` → public + private
   - GM verification via Discord role (`GM_ROLE_ID`), not single user ID
   - Default: if source unknown, treat as private (never surfaces in player channel)

2. **Update `/codex check`** — add permissions filter to existing command
   - Same command, now channel/role-aware
   - Player channel → public only; GM channel → public + private with 🔒 SECRET field

3. **`/codex add [name] [type] [description]`** — GM channel only, creates entity with status='Draft'

4. **Mission Report** — `src/living_codex/scribe/report.py`
   - Non-ephemeral Discord message in GM channel (GM needs to reference later)
   - Content: new entities count, updates count, conflicts count
   - Discord buttons using `discord.ui.View(timeout=None)` + `custom_id` (survives bot restarts)
   - Store pending report IDs in `staged_changes` table; re-register handlers on startup
   - "Approve All", "Review Details", "Reject" buttons
   - On approve: write to DB, delete processed audio
   - On review: show individual changes with approve/reject per item

5. **Tests** — `tests/test_permissions.py`
   - Player cannot see private fields (P0)
   - GM sees private fields
   - Unknown source defaults to private

### Verification
- Same query in `#codex-intel` shows public only; in `#codex-gm` shows public + private
- `/codex add` works in GM channel, rejected in player channel
- Mission Report appears with correct counts after Scribe pipeline runs
- GM approves → entities written to DB, audio deleted
- GM rejects → nothing written
- **P0:** No private data leaks to player channel under any code path

---

## Phase 5: Foundry Sync (Week 5)

**Goal:** Foundry VTT sync with conflict detection. Alpha release.

### Tasks

1. **Foundry client** — `src/living_codex/sync/foundry.py`
   - httpx async client for Foundry VTT REST API with retry/exponential backoff
   - CRUD operations on Journal Entries
   - Offline detection: if Foundry unreachable, write to `sync_queue` table, serve reads from SQLite cache
   - Process queue when Foundry comes back online

2. **Conflict Guard** — `src/living_codex/sync/guard.py`
   - On sync attempt:
     1. Fetch journal from Foundry
     2. SHA-256 hash current content
     3. Compare with `entities.foundry_hash`
     4. Match → safe to update, write + update hash
     5. Mismatch → abort, log conflict, notify GM
     6. New entity → create journal entry
   - Never overwrite. Ever.

3. **Sync commands** — `src/living_codex/commands/admin.py`
   - `/codex sync` — trigger manual sync for approved changes
   - `/codex status` — show pending changes, conflicts, last sync time
   - `/codex resolve [entity]` — mark conflict as resolved (GM chooses which version)

4. **Formatter update** — add optional `[View in Foundry]` link if `foundry_id` exists

5. **Backup automation**
   - Daily SQLite backup to Google Drive
   - `.backup` command for DB snapshot

### Verification
- Sync entity to Foundry → journal entry created
- Manually edit journal in Foundry → re-sync → "Conflict detected" message
- Foundry offline → `/codex check` still works from SQLite cache
- Full end-to-end: upload audio → process → approve → sync to Foundry
- Test suite: `test_guard.py` covers all conflict scenarios
- Docker stats confirm resource compliance under load
- Backup script runs and uploads DB snapshot to Drive

---

## Critical Files to Create

| File | Purpose | Phase |
|------|---------|-------|
| `pyproject.toml` | Dependencies + project metadata | 1 |
| `src/living_codex/config.py` | pydantic-settings config | 1 |
| `src/living_codex/database.py` | SQLite schema + async DB class | 1 |
| `src/living_codex/bot.py` | Discord bot setup | 1 |
| `src/living_codex/main.py` | Entry point | 1 |
| `docker-compose.yml` | Deployment config | 1 |
| `Dockerfile` | Container build | 1 |
| `src/living_codex/search.py` | Fuzzy search with rapidfuzz | 2 |
| `src/living_codex/formatter.py` | 3-Bullet embed builder | 2 |
| `src/living_codex/commands/codex.py` | /codex check (public only) | 2 |
| `scripts/seed.py` | Test data population | 2 |
| `src/living_codex/ai/gemini.py` | Gemini API (direct, no abstraction) | 3 |
| `src/living_codex/ai/prompts.py` | Extraction & transcription prompts | 3 |
| `src/living_codex/scribe/pipeline.py` | Audio → staged changes pipeline | 3 |
| `src/living_codex/scribe/watcher.py` | Craig session detector + file watcher | 3 |
| `scripts/setup_rclone.sh` | rclone + cron for Craig → /inputs | 3 |
| `src/living_codex/permissions.py` | Two-layer access control | 4 |
| `src/living_codex/scribe/report.py` | Mission Report + Discord buttons | 4 |
| `src/living_codex/sync/foundry.py` | Foundry VTT client | 5 |
| `src/living_codex/sync/guard.py` | Conflict Guard logic | 5 |
| `src/living_codex/commands/admin.py` | /codex sync, status, resolve | 5 |

## Dependencies

```toml
dependencies = [
    "discord.py>=2.3.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "rapidfuzz>=3.0.0",
    "httpx>=0.27.0",
    "google-generativeai>=0.5.0",
    "python-dotenv>=1.0.0",
    "watchfiles>=0.20.0",
]
```

## Reusable Patterns from Existing MCPs

- **Async DB class**: Follow `garmin-health-mcp/src/garmin_health_mcp/database.py` pattern (WAL, row_factory, init_schema)
- **Config**: Follow `monarch-mcp-enhanced/src/monarch_enhanced/` pydantic-settings pattern
- **HTTP retries**: 3 attempts with exponential backoff (hevy pattern)
- **pyproject.toml**: hatchling build backend, same [tool.black] and [tool.ruff] config

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Gemini transcription quality | Write Gemini directly; refactor to abstraction only if switching providers |
| Foundry VTT API instability | SQLite cache serves all reads; sync is best-effort; `sync_queue` table for offline |
| Resource caps too tight | All AI processing is API calls (no local inference); monitor with `docker stats` |
| Discord 2000 char limit | Formatter hard-caps at 500 chars; tested in Phase 2 |
| Fuzzy search false positives | Configurable thresholds; "Did you mean?" for ambiguous matches |
| Audio file size vs 512MB RAM | Craig FLAC tracks are 300-500MB each; use Gemini Files API (upload_file), never load into Python memory |
| Discord interaction timeouts | Acknowledge slash commands within 3s; use persistent views with custom_id for buttons |
| Button persistence across restarts | Store pending report IDs in SQLite; re-register handlers on bot startup |

---

## Senior Architect Review — Accepted Changes

The following changes have been incorporated into the plan based on architecture review.

### 1. Drop AI provider abstraction (Phase 3)
**Was:** `ai/base.py` abstract class + `ai/gemini.py` implementation.
**Now:** Write `ai/gemini.py` directly with clean function signatures. Refactor to abstraction only if/when a second provider is needed. The abstraction adds no value at MVP.

### 2. Add `campaigns` table + `campaign_id` to entities (Phase 1)
The design doc runs 3 campaigns (Armour Astir, Delta Green, MotW). Without campaign isolation, you cannot prioritize "current campaign results first." This is basic data partitioning, not multiverse over-engineering.

### 3. Move staged changes from JSON file to SQLite table (Phase 3)
**Was:** `staged_changes.json` file on disk.
**Now:** `staged_changes` table in SQLite. Survives container restarts, supports querying, makes Mission Report buttons reliable (reference changes by ID).

### 4. Rename `spoiler.py` → `permissions.py` + two-layer filtering (Phase 4)
The Spoiler Shield is the P0 security feature. Name it what it is: access control. Implement defense in depth — query-level filter (never return private fields unless verified GM) AND formatter-level strip (second pass removes any private content that leaked through).

### 5. Remove `status_color` from schema
`status_color` is a presentation concern. Map status labels → emoji/colors in the formatter, not in the database.

### 6. Use Discord role for GM identification, not single user ID
**Was:** `GM_USER_ID` single hardcoded value.
**Now:** `GM_ROLE_ID` in config. Check `interaction.user.roles` in command handlers. Supports multiple GMs, which is how Discord permissions actually work. Keep channel-based routing as the UX layer on top.

### 7. Add `scripts/seed.py` for test data (Phase 2)
`/codex add` only creates Draft entities — too limited for testing. Need a seed script that populates entities with public/private descriptions, aliases, relationships, and campaign IDs.

### 8. Add primary keys to `aliases` and `relationships` tables
Design doc schema is missing PKs. Add `id INTEGER PRIMARY KEY` to both.

### 9. Add indexes (Phase 1)
At minimum: `entities(name)`, `entities(campaign_id)`, `aliases(alias)`, `aliases(entity_id)`, `entities(type)`.

### 10. Add `sync_queue` table for Foundry offline handling (Phase 5)
When Foundry is unreachable, queue changes in SQLite. Process queue when Foundry comes back online.

### 11. Explicit audio file handling constraint (Phase 3)
Craig outputs FLAC (lossless): a 3-hour per-speaker track ≈ 300-500MB. With 5 speakers, a session could be 1.5-2.5GB total. Use Gemini Files API (`upload_file`) to upload to Google's servers, then reference by URI. Never load FLAC into Python memory.

### 12. Discord-specific fixes
- Use **guild-specific commands** during development (instant sync vs 1-hour for global)
- Use **ephemeral** for `/codex check` (keeps channel clean), **non-ephemeral** for Mission Reports (GM needs to reference later)
- Embed total limit is **6000 chars** (not 2000) — 2000 is for regular messages. Still enforce 3-Bullet brevity.
- Cap fuzzy "Did you mean?" select menu at **5 options** max
- Use `discord.ui.View(timeout=None)` + `custom_id` for Mission Report buttons that survive restarts

### 13. File cleanup after processing (Phase 3)
Design doc comment says "post session transcription mp3 is destroyed." Add explicit deletion of raw audio + intermediate transcript files after pipeline completes and GM approves/rejects.

### 14. nice/ionice in Docker setup (Phase 1)
The systems architecture doc specifies `nice -n 19` and `ionice -c 3`. These must be set in the Dockerfile CMD or docker-compose config, not left out.

### 15. Add "View Full" button on embeds (Phase 2)
The 3-Bullet Rule intentionally truncates. Users need a way to get more detail. Add a "View Full" button on embeds that sends an ephemeral follow-up with expanded info.

---

## QA Test Plan

Testing effort is weighted toward the two P0 features: **Spoiler Shield** (T3) and **Conflict Guard** (T4). A single failure in either kills the product.

### Shared Test Infrastructure — `tests/conftest.py`

```python
@pytest.fixture async def db(tmp_path)        # Fresh SQLite with schema applied
@pytest.fixture async def seeded_db(db)       # Pre-loaded: Vrax, 4th Fleet, Green Box, Kora
@pytest.fixture def gm_interaction()           # Mock interaction WITH GM role
@pytest.fixture def player_interaction()       # Mock interaction WITHOUT GM role
```

Dev dependencies: `pytest`, `pytest-asyncio`, `pytest-mock`

---

### Phase 1 Tests — `tests/test_database.py`

| Test | Assertion |
|------|-----------|
| `test_schema_creates_all_tables` | entities, aliases, relationships, campaigns, sessions, meta tables exist |
| `test_wal_mode_enabled` | `PRAGMA journal_mode` → `wal` |
| `test_foreign_keys_enabled` | `PRAGMA foreign_keys` → `1` |
| `test_indexes_exist` | Indexes on entities(name), entities(campaign_id), aliases(alias), etc. |
| `test_entity_type_check_constraint` | `type='Garbage'` raises IntegrityError |
| `test_config_loads_from_env` | All CodexConfig fields populate from env vars |
| `test_config_missing_required_field` | Missing CODEX_DISCORD_TOKEN raises ValidationError |

**Gate:** All green + Docker builds + `/ping` responds + `docker stats` under limits.

---

### Phase 2 Tests — Fuzzy Search (T2) & Formatter (T1)

**`tests/test_search.py`** — Design doc T2 boundary tests:

| Test | Input | Expected |
|------|-------|----------|
| `test_exact_match_returns_direct` | "Baron Vrax" | direct match |
| `test_close_spelling_returns_direct` | "Baron Vrecks" | direct match (≥0.7) |
| `test_alias_match` | "Sky Pirates" | The 4th Fleet |
| `test_ambiguous_returns_select_menu` | "Baron" | ambiguous, shows Vrax + Kora |
| `test_noise_returns_nothing` | "Banana" | no results (<0.4) |
| `test_empty_query_returns_none` | "" | no results |
| `test_threshold_boundary_at_070` | crafted input | direct, not ambiguous |
| `test_threshold_boundary_at_040` | crafted input | ambiguous, not none |
| `test_select_menu_capped_at_five` | 10+ partial matches | max 5 returned |

**`tests/test_formatter.py`** — Design doc T1:

| Test | Assertion |
|------|-----------|
| `test_embed_has_status_line` | Contains 🟢, 🔴, or 💀 |
| `test_embed_has_context_line` | Contains entity's key fact |
| `test_embed_has_source_line` | Contains "Session" reference |
| `test_embed_under_500_chars` | 5000-char entity → embed < 500 chars |
| `test_truncation_clean` | Ends with "…" not broken mid-word |
| `test_embed_total_under_6000` | Discord hard limit respected |

**Gate:** All T1 + T2 tests green. Manual: alias resolves, "Banana" returns nothing.

---

### Phase 3 Tests — Scribe Pipeline (Craig Integration)

**`tests/test_pipeline.py`** (all Gemini calls mocked):

| Test | Assertion |
|------|-----------|
| `test_pipeline_writes_staged_changes` | staged_changes rows created in DB |
| `test_pipeline_creates_session_record` | sessions table has entry |
| `test_pipeline_classifies_public_vs_private` | Output has both visibility types |
| `test_pipeline_handles_gemini_error` | GeminiAPIError raised, no partial writes |
| `test_per_speaker_mode_sends_individual_tracks` | Multiple FLAC files → each uploaded separately with speaker name |
| `test_mixed_mode_fallback` | Single FLAC → falls back to mixed transcription prompt |
| `test_speaker_attribution_from_filenames` | Craig filenames (e.g., `username.flac`) used as speaker names |

**`tests/test_gemini.py`**:

| Test | Assertion |
|------|-----------|
| `test_uses_files_api_not_inline_bytes` | **CRITICAL:** `upload_file()` called, not raw bytes (FLAC files are 300-500MB) |
| `test_transcribe_uses_flash_model` | Model is `gemini-1.5-flash` |
| `test_extract_uses_pro_model` | Model is `gemini-1.5-pro` |

**`tests/test_watcher.py`**: detects Craig folders with FLAC files, ignores non-audio, moves to /processed

**Gate:** Pipeline writes staged changes. Per-speaker mode works. Files API confirmed. RAM under 512MB during manual FLAC test.

---

### Phase 4 Tests — Spoiler Shield (T3) — **P0 CRITICAL**

**`tests/test_permissions.py`** — The most important test file in the project.

#### Layer 1: Query-Level Filtering
| Test | Assertion |
|------|-----------|
| `test_player_cannot_see_secrets` | Green Box query: "Shoggoth" NOT in result, description_private is None |
| `test_gm_sees_secrets` | Green Box query: both public and "Shoggoth" present |
| `test_player_query_never_returns_private_field` | ALL entities: description_private is None for players |

#### Layer 2: Formatter-Level Stripping (Defense in Depth)
| Test | Assertion |
|------|-----------|
| `test_formatter_strips_private_for_player` | Even if Layer 1 leaks, formatter removes private content |
| `test_formatter_includes_private_for_gm` | GM embed includes 🔒 SECRET field |

#### Channel Routing
| Test | Assertion |
|------|-----------|
| `test_player_channel_never_shows_private` | GM user in #codex-intel → no private data (channel overrides role) |
| `test_gm_channel_shows_private_for_gm` | GM in #codex-gm → private data shown |
| `test_player_in_gm_channel_cannot_see_private` | Player who gets into #codex-gm → still no private data |
| `test_unknown_source_defaults_to_private` | Entity with no visibility → private by default |

#### Mission Report
| Test | Assertion |
|------|-----------|
| `test_approve_all_writes_to_db` | Staged changes → entities table |
| `test_reject_writes_nothing` | Entity count unchanged |
| `test_report_survives_bot_restart` | Pending reports queryable from DB |

**Manual:** Screenshot comparison — player vs GM view of Green Box. **Zero tolerance for leaks.**

**Gate (HARD):** 100% pass on ALL T3 sub-tests. Both layers. Channel routing. Screenshot comparison.

---

### Phase 5 Tests — Conflict Guard (T4) & Offline (T5) — **P0 CRITICAL**

**`tests/test_guard.py`**:

#### T4: The Race Condition
| Test | Assertion |
|------|-----------|
| `test_matching_hash_allows_sync` | Hash match → SyncResult.UPDATED, update_journal called |
| `test_mismatched_hash_aborts_sync` | Hash mismatch → SyncResult.CONFLICT, **update_journal NOT called** |
| `test_conflict_logs_notification` | "Conflict detected" in log/message |
| `test_new_entity_creates_journal` | No foundry_id → SyncResult.CREATED |
| `test_sync_updates_stored_hash` | After sync, DB hash matches new content |
| `test_conflict_preserves_foundry_content` | After conflict, Foundry content is untouched |

#### T5: Offline Fallback
| Test | Assertion |
|------|-----------|
| `test_codex_check_works_when_foundry_down` | ConnectionError from Foundry → search still works from SQLite |
| `test_sync_queues_when_foundry_down` | Offline → SyncResult.QUEUED, sync_queue row created |
| `test_queued_changes_process_when_foundry_returns` | Queue processed, queue emptied |

#### Foundry Client
| Test | Assertion |
|------|-----------|
| `test_retry_with_backoff` | 3 retries on 5xx |
| `test_no_retry_on_4xx` | Single attempt on 404 |

**Manual T6 (Docker Strangle):** Run stress test script — 50 queries/sec + audio pipeline simultaneously. `docker stats` must show CPU ≤ 50%, RAM ≤ 512MB.

**Gate (HARD):** 100% pass on T4 + T5. Manual T6 resource compliance. Full end-to-end: audio → approve → sync → Foundry journal created.

---

### Alpha Release Exit Criteria

| Criterion | Test | Phase | Required |
|-----------|------|-------|----------|
| Spoiler Shield — zero leaks | T3 (all sub-tests) | 4 | **100% pass** |
| Conflict Guard — never overwrites | T4 (all sub-tests) | 5 | **100% pass** |
| Fuzzy search — "Did you mean?" | T2 (all sub-tests) | 2 | 100% pass |
| 3-Bullet formatting | T1 | 2 | 100% pass |
| AVAX resource safety | T6 (manual) | 5 | CPU < 50%, RAM < 512MB |
| Offline fallback | T5 | 5 | Pass |

### Test File Summary

| File | Phase | Priority | Est. Tests |
|------|-------|----------|------------|
| `tests/conftest.py` | 1 | — | Fixtures |
| `tests/test_database.py` | 1 | Medium | 7 |
| `tests/test_search.py` | 2 | High | 10 |
| `tests/test_formatter.py` | 2 | Medium | 6 |
| `tests/test_pipeline.py` | 3 | Medium | 4 |
| `tests/test_gemini.py` | 3 | Medium | 3 |
| `tests/test_watcher.py` | 3 | Low | 3 |
| **`tests/test_permissions.py`** | **4** | **CRITICAL** | **12** |
| `tests/test_report.py` | 4 | High | 3 |
| **`tests/test_guard.py`** | **5** | **CRITICAL** | **8** |
| `tests/test_admin_commands.py` | 5 | Medium | 3 |
| `scripts/stress_test.py` | 5 | High | Manual |

**Total: ~61 automated tests.** One-third concentrated on the two P0 features.

---

## Deferred (Not for MVP)

These were flagged but intentionally deferred:
- **`audit_log` table** — useful for accountability, add post-alpha
- **`errors.py` exception hierarchy** — revisit if scattered try/except becomes a problem
- **Network bandwidth limiting (5 Mbps)** — infrastructure concern, not application code
- **Splitting `models.py` into submodules** — wait until it actually gets unwieldy
