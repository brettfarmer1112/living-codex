# Living Codex v0.1.0 — Operating Manual

A Discord bot that records, indexes, and answers questions about your tabletop RPG campaigns. It transcribes session audio, extracts entities (NPCs, locations, factions, clues), generates narrative summaries, and syncs data to Foundry VTT.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Configuration](#configuration)
3. [Discord Commands](#discord-commands)
4. [The Scribe Pipeline (Audio to Entities)](#the-scribe-pipeline)
5. [Approving Staged Changes](#approving-staged-changes)
6. [Swapping AI Models](#swapping-ai-models)
7. [Uploading Lore Documents](#uploading-lore-documents)
8. [Foundry VTT Sync](#foundry-vtt-sync)
9. [Editing the System Prompt](#editing-the-system-prompt)
10. [AI Prompt Reference](#ai-prompt-reference)
11. [CLI Scripts](#cli-scripts)
12. [Docker Operations](#docker-operations)
13. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Discord bot token (from [Discord Developer Portal](https://discord.com/developers/applications))
- At least one AI API key:
  - **Anthropic** key for Claude models (entity extraction, summaries, queries)
  - **Google Gemini** key for Gemini models and/or audio transcription
- A Discord server with the bot invited (needs `applications.commands` scope)

### Minimum `.env` File

Create a `.env` file in the project root:

```env
# Required — Discord
CODEX_DISCORD_TOKEN=your_bot_token
CODEX_DISCORD_GUILD_ID=123456789
CODEX_GM_ROLE_ID=123456789
CODEX_GM_CHANNEL_ID=123456789
CODEX_PLAYER_CHANNEL_ID=123456789

# Required — AI (set at least one)
CODEX_AI_MODEL=claude-haiku-4-5
CODEX_ANTHROPIC_API_KEY=sk-ant-...

# Required for audio transcription
CODEX_GEMINI_API_KEY=AIza...
```

### Start the Bot

```bash
docker compose up -d
docker logs living-codex -f   # watch startup
```

You should see:
```
AI router: using ClaudeClient (model=claude-haiku-4-5)
Commands synced to guild 123456789.
Logged in as Living Codex#1234
```

---

## Configuration

All config is set via environment variables with the `CODEX_` prefix. Place them in `.env` at the project root.

### Required Variables

| Variable | Type | Description |
|----------|------|-------------|
| `CODEX_DISCORD_TOKEN` | string | Discord bot token |
| `CODEX_DISCORD_GUILD_ID` | int | Your Discord server ID |
| `CODEX_GM_ROLE_ID` | int | Discord role ID for the Game Master |
| `CODEX_GM_CHANNEL_ID` | int | Channel ID for GM-facing messages |
| `CODEX_PLAYER_CHANNEL_ID` | int | Channel ID for player-facing messages |

### AI Model Selection

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CODEX_AI_MODEL` | string | `claude-haiku-4-5` | Which AI model to use for entity extraction, summaries, and queries. See [Swapping AI Models](#swapping-ai-models). |
| `CODEX_ANTHROPIC_API_KEY` | string | `""` | Anthropic API key. Required if `AI_MODEL` starts with `claude-`. Also accepts `CODEX_CLAUDE_API_KEY`. |
| `CODEX_GEMINI_API_KEY` | string | `""` | Google Gemini API key. Required if `AI_MODEL` starts with `gemini-`. **Also required for audio transcription regardless of AI_MODEL.** |

> **Important:** Audio transcription always uses Gemini 2.0 Flash (via the Files API). Even if your `AI_MODEL` is set to a Claude model, you still need `CODEX_GEMINI_API_KEY` for the Scribe pipeline to transcribe audio.

### Optional Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CODEX_DB_PATH` | path | `./data/codex.db` | SQLite database file location |
| `CODEX_INPUT_DIR` | path | `./inputs` | Directory watched for new audio files |
| `CODEX_DEFAULT_CAMPAIGN_ID` | int | `1` | Campaign ID used by all slash commands |
| `CODEX_FOUNDRY_URL` | string | `""` | Foundry VTT base URL (enables sync features) |
| `CODEX_FOUNDRY_API_KEY` | string | `""` | Foundry VTT API key |
| `CODEX_RCLONE_GDRIVE_PATH` | string | `living-codex-transcriptions` | rclone remote path for Google Drive audio ingestion |
| `CODEX_GDRIVE_CRAIG_PATH` | string | `""` | Google Drive path for Craig-mode speaker folders |

---

## Discord Commands

All commands are under the `/codex` group. Most responses are **ephemeral** (only visible to you).

### `/codex ping`

Health check. Returns bot latency in milliseconds.

### `/codex check <query>`

Look up an entity by name or alias.

- **Exact match:** Shows an embed card with type, status, description, first/last session seen.
  - Click **"View Full"** to expand with events and relationships.
- **Multiple candidates:** Shows a dropdown to pick the right one.
- **No match:** `No results found for "query".`

Supports fuzzy matching — misspellings and partial names will return candidates.

### `/codex query <question>`

Ask the AI a question about your campaign. The bot assembles context from:

1. All entity profiles (names, types, descriptions, statuses)
2. The full relationship map (who is connected to whom)
3. Session summaries (narrative recaps)
4. Uploaded lore documents
5. Raw transcripts (only for sessions that don't have summaries yet)

The AI answers with inline citations in the format `Session N, [HH:MM]`.

**Response time:** 3-15 seconds depending on context size and model.

Long answers (>1900 chars) are sent as a short preview + attached `.md` file.

### `/codex lastsession`

Shows a narrative summary of the most recent processed session.

- If a summary is already cached, returns it instantly.
- If no summary exists but a transcript does, generates one on-demand via the AI (may take 5-10 seconds) and caches it for future calls.
- Long summaries are sent as preview + `.md` file attachment.

### `/codex upload <attachment> [title]`

Upload a `.md` or `.txt` lore document to the Codex database.

- **File types:** `.md` and `.txt` only.
- **Size limit:** 1 MB.
- **Title:** Defaults to the filename (minus extension) if not provided.
- The document is stored in the database and becomes available to `/codex query` as context.
- If Foundry VTT is configured, also pushes as a journal entry.

### `/codex sync [entity_name] [force]`

Push entities to Foundry VTT.

- No arguments: syncs all unsynced entities.
- `entity_name`: sync only that entity.
- `force: True`: overwrite manual edits in Foundry (bypasses conflict detection).

Per-entity status icons:
- ✅ Synced successfully
- ⏳ Queued (Foundry responded slowly)
- ⚠️ Conflict detected — use `force: True` to override
- 📵 Foundry offline — queued for retry
- ❌ Error

### `/codex syncstatus`

Shows Foundry sync summary: count of synced entities, pending queue items, and conflicts (with entity names).

---

## The Scribe Pipeline

The Scribe pipeline automatically processes audio recordings into structured campaign data. It runs in the background when the bot starts.

### How It Works

```
1. Drop audio file into input directory
       ↓
2. AudioWatcher detects file, waits 10 seconds (debounce)
       ↓
3. Upload to Gemini Files API
       ↓
4. Gemini 2.0 Flash transcribes audio
   Output: "[HH:MM] Speaker: text" format
       ↓
5. Transcript saved to sessions table
       ↓
6. AI extracts entities (NPCs, locations, factions, etc.)
   Output: JSON with names, descriptions, relationships, events
       ↓
7. AI generates narrative session summary
       ↓
8. Entities staged in staged_changes table (status: pending)
       ↓
9. Audio file deleted from disk
```

### Supported Audio Formats

`.mp3`, `.wav`, `.flac`, `.ogg`

### Input Modes

**Single-file mode:** Drop one audio file into the input directory.

```
inputs/
  session_5.mp3
```

The bot transcribes with automatic speaker identification (labels speakers as "GM:", "Player:", etc.).

**Craig mode (per-speaker):** Drop a folder containing one `.flac` file per speaker.

```
inputs/
  Session_5_Craig/
    1-Brett.flac
    2-Rick.flac
    3-Erin.flac
    4-Mikey.flac
```

Speaker names are extracted from filenames (everything after the first `-`). Each file is transcribed individually and merged with speaker headers.

### Input Directory

| Context | Path |
|---------|------|
| Docker container | `/app/inputs` |
| Server host (via volume mount) | `/mnt/mediadrive/codex_raw` |
| Local development | `./inputs` |

### What Gets Created

For each processed audio file:

| Table | Data |
|-------|------|
| `sessions` | Session row with `transcript_text`, `summary`, `session_number` |
| `staged_changes` | 5-15 rows per entity found (descriptions, aliases, relationships, events) |

**Staged changes are NOT live entities yet.** They must be approved first. See next section.

### Requirements

- `CODEX_GEMINI_API_KEY` must be set (transcription always uses Gemini).
- `CODEX_AI_MODEL` must be set to a valid model with its API key (entity extraction and summaries).
- The input directory must exist and be writable.

### If Processing Fails

- The audio file is **kept** (not deleted).
- The session row is marked with `processed_at = NULL`.
- Errors are logged. Check `docker logs living-codex`.
- Fix the issue and restart the bot — the watcher will re-detect the file.

---

## Approving Staged Changes

The Scribe pipeline does NOT write directly to the entities table. Instead, all AI-extracted data lands in `staged_changes` with `status = 'pending'`. This prevents AI hallucinations from corrupting your campaign database.

### Running Approval

Run the CLI script from the project root or inside the container:

```bash
# From project root
python scripts/approve.py

# Or inside the Docker container
docker exec living-codex python scripts/approve.py
```

### What Approval Does

1. Reads all pending `staged_changes` rows.
2. Groups them by entity name + type.
3. For each entity:
   - **Existing entity** (same name in same campaign): updates fields, adds new aliases/relationships/events.
   - **New entity**: creates with a UUID, then adds children.
4. Marks all processed rows as `status = 'approved'`.
5. Pushes to Foundry VTT if configured.

### What Gets Created/Updated

| Staged Field | Target Table | Notes |
|-------------|-------------|-------|
| `description_public` | `entities.description_public` | Overwrites previous |
| `description_private` | `entities.description_private` | Overwrites previous |
| `status_label` | `entities.status_label` | Active, Inactive, Dead, etc. |
| `motivation` | `entities.motivation` | NPC goals/wants |
| `appearance` | `entities.appearance` | Physical description |
| `alias` | `aliases` table | Additive (new row per alias) |
| `relationship` | `relationships` table | Additive (source → target with type) |
| `event` | `entity_events` table | Additive (with timestamp + visibility) |

### Inspecting Before Approving

```bash
python scripts/inspect_staged.py
```

Shows all pending changes grouped by entity, so you can review what the AI extracted before committing.

---

## Swapping AI Models

Change one line in `.env` to switch between AI providers:

```env
# Claude options (need CODEX_ANTHROPIC_API_KEY)
CODEX_AI_MODEL=claude-haiku-4-5          # Fast, cheap
CODEX_AI_MODEL=claude-sonnet-4-6         # Balanced
CODEX_AI_MODEL=claude-opus-4-6           # Most capable

# Gemini options (need CODEX_GEMINI_API_KEY)
CODEX_AI_MODEL=gemini-2.5-flash-lite     # Fast, cheap
CODEX_AI_MODEL=gemini-2.5-pro            # More capable
```

After changing the model:

```bash
# No rebuild needed — just restart the container
docker compose up -d
```

The model name prefix determines which SDK is used:
- `claude-*` → Anthropic SDK → requires `CODEX_ANTHROPIC_API_KEY`
- `gemini-*` → Google GenAI SDK → requires `CODEX_GEMINI_API_KEY`

> **Note:** Audio transcription (Scribe pipeline) always uses Gemini 2.0 Flash regardless of `AI_MODEL`. The `AI_MODEL` setting only controls entity extraction, summarization, and query answering.

---

## Uploading Lore Documents

Lore documents add GM-authored context to the AI's knowledge base. Uploaded docs are included in every `/codex query` response.

### Via Discord

```
/codex upload [attach a .md or .txt file] [optional title]
```

### What Happens

1. File is validated (`.md` or `.txt`, under 1 MB).
2. Content is stored in the `lore_docs` table, scoped to the current campaign.
3. If Foundry is configured, also pushed as a journal entry.
4. All future `/codex query` calls include this document as context.

### Use Cases

- Campaign setting documents (world history, faction descriptions)
- House rules or session zero notes
- Maps or location descriptions (as markdown text)
- Handouts or letters the party has received

---

## Foundry VTT Sync

Optional integration that pushes entities and session journals to Foundry VTT.

### Setup

```env
CODEX_FOUNDRY_URL=https://your-foundry-instance.com
CODEX_FOUNDRY_API_KEY=your_api_key
```

### Features

- **Entity sync:** Each entity becomes a Foundry journal entry with HTML-formatted content.
- **Session sync:** Session summaries are pushed as journal entries after processing.
- **Lore sync:** Uploaded lore docs are pushed as journal entries.
- **Conflict detection:** Detects when a Foundry journal was manually edited since last sync. Use `force: True` to override.
- **Offline queue:** If Foundry is unreachable, changes are queued and retried every 5 minutes.

### Commands

| Command | Action |
|---------|--------|
| `/codex sync` | Push all unsynced entities |
| `/codex sync entity_name` | Push one entity |
| `/codex sync entity_name force:True` | Force-overwrite conflicts |
| `/codex syncstatus` | View sync queue and conflicts |

---

## Editing the System Prompt

The file `src/living_codex/ai/codex_rules.md` defines the AI's persona and output rules. It is loaded as the system instruction for every AI call.

### Hot-Reload

The system prompt is **volume-mounted read-only** in Docker. Edits take effect on the next AI call — no restart needed.

```bash
# Edit on the host
nano src/living_codex/ai/codex_rules.md

# The bot picks up changes automatically (cached with mtime check)
```

### What It Controls

- **Persona:** "Campaign Archivist" — factual, citation-based, no speculation
- **Output format:** Discord-friendly (bold headers, bullets, short paragraphs)
- **Character limits:** 2,000 chars default, 4,000 extended
- **Citation style:** `Session N, [HH:MM]`
- **Entity extraction rules:** How to classify PCs vs NPCs, relationship verbs, visibility
- **Writing style:** Present tense for states, active voice, named actors, no hedging

---

## AI Prompt Reference

The system uses two prompt sources:

| Source | File | Purpose |
|--------|------|---------|
| System prompt | `src/living_codex/ai/codex_rules.md` | Persona, output style, hard rules — applies to every AI call |
| Task prompts | `src/living_codex/ai/prompts.py` | Per-operation instructions with data placeholders — no persona framing |

### System Prompt (`codex_rules.md`)

Loaded once at bot startup and injected as the `system` role into every AI call. See [Editing the System Prompt](#editing-the-system-prompt) for hot-reload details.

Controls: Archivist persona, Discord formatting, citation style (`Session N, [HH:MM]`), character limits (2000 / 4000), entity extraction rules, and writing voice.

---

### `TRANSCRIBE_SINGLE` — Audio transcription (single file)

**Model:** Gemini 2.0 Flash (Files API)
**Used by:** Scribe pipeline, single-file input mode

```
You are transcribing an RPG podcast recording.
Identify and label speakers when possible (e.g., "GM:", "Player:").
Output a clean, verbatim transcript with timestamps every 5 minutes.
Format: [HH:MM] Speaker: text
```

---

### `TRANSCRIBE_SPEAKER` — Audio transcription (Craig per-speaker)

**Model:** Gemini 2.0 Flash (Files API)
**Used by:** Scribe pipeline, Craig multi-file input mode
**Placeholders:** `{speaker_name}`

```
You are transcribing audio from one speaker: {speaker_name}.
Output verbatim text. Do not add timestamps or speaker labels.
```

Called once per `.flac` file in a Craig folder. The pipeline merges the results with speaker headers derived from the filenames.

---

### `EXTRACT_ENTITIES` — Entity extraction from transcript

**Model:** Controlled by `CODEX_AI_MODEL`
**Used by:** Scribe pipeline (step 6), after transcription
**Placeholders:** `{campaign_name}`, `{known_pcs}`, `{transcript}`

Instructs the AI to extract all named entities as a JSON array. Each entity includes:

| Field | Description |
|-------|-------------|
| `name` | Canonical name |
| `type` | `NPC`, `PC`, `Faction`, `Location`, `Asset`, or `Clue` |
| `aliases` | Nicknames and alternate names |
| `public_description` | 3–6 sentences of observable facts |
| `private_description` | GM secrets, unrevealed lore |
| `motivation` | What the entity wants (NPCs) |
| `appearance` | Physical description (NPCs, Locations) |
| `status_label` | `Active`, `Inactive`, `Dead`, `Destroyed`, or `Unknown` |
| `first_appearance` | Transcript timestamp of first mention |
| `relationships` | Array of `{target_name, rel_type, citation}` |
| `events` | Array of `{timestamp, description, visibility}` |

Returns raw JSON only — no markdown fences. Known PCs are injected via `{known_pcs}` to prevent misclassification.

---

### `SUMMARIZE_SESSION` — Narrative session summary

**Model:** Controlled by `CODEX_AI_MODEL`
**Used by:** Scribe pipeline (step 7); also on-demand by `/codex lastsession` when no cached summary exists
**Placeholders:** `{campaign_name}`, `{session_number}`, `{transcript}`

Produces a four-part narrative summary:

1. **Opening paragraph** — scene setting and prior state
2. **Bullet list** — key events with named actors
3. **Prose paragraphs** — 2–3 most dramatic moments
4. **Closing paragraph** — cliffhangers and open threads

Style: present tense, character names only (no player names), no GM-private information.

---

### `QUERY_CODEX` — Natural language campaign query

**Model:** Controlled by `CODEX_AI_MODEL`
**Used by:** `/codex query <question>`
**Placeholders:** `{campaign_name}`, `{question}`, `{entities}`, `{relationships}`, `{summaries}`, `{lore_docs}`, `{transcripts}`

Assembles all campaign context into a single prompt and asks the AI to answer a question. Context sections injected in priority order:

| Placeholder | Source |
|-------------|--------|
| `{entities}` | All entity profiles (name, type, description, status) |
| `{relationships}` | Full directional relationship map |
| `{summaries}` | Narrative session summaries |
| `{lore_docs}` | GM-uploaded lore documents |
| `{transcripts}` | Raw transcripts only for sessions without summaries |

Citations use `Session N, [HH:MM]` format. If the answer isn't in context, the AI is instructed to say so directly rather than speculate.

---

## CLI Scripts

Run from the project root or inside the Docker container.

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/approve.py` | Approve pending staged changes into entities | `python scripts/approve.py` |
| `scripts/inspect_staged.py` | View pending staged changes before approving | `python scripts/inspect_staged.py` |
| `scripts/show_entities.py` | Dump all entities, aliases, and relationships | `python scripts/show_entities.py` |
| `scripts/seed.py` | Seed demo entities for testing | `python scripts/seed.py` |
| `scripts/seed_players.py` | Seed player roster for a campaign | `python scripts/seed_players.py` |
| `scripts/setup_rclone.sh` | Install rclone and configure Google Drive audio ingestion | `bash scripts/setup_rclone.sh` |

### Running Scripts Inside Docker

```bash
docker exec living-codex python scripts/approve.py
docker exec living-codex python scripts/show_entities.py
```

---

## Docker Operations

### Start / Stop / Restart

```bash
docker compose up -d          # Start (detached)
docker compose down            # Stop and remove container
docker compose restart         # Restart without rebuild
```

### Rebuild After Code Changes

```bash
docker compose build && docker compose up -d
```

Or with `--no-cache` to force a clean rebuild:

```bash
docker compose build --no-cache && docker compose up -d
```

### View Logs

```bash
docker logs living-codex -f          # Follow live logs
docker logs living-codex --tail 50   # Last 50 lines
```

### Resource Limits

The container runs with restricted resources to avoid impacting other services:

| Resource | Limit |
|----------|-------|
| CPU | 0.50 cores |
| Memory | 512 MB (128 MB reserved) |
| I/O priority | Idle (`ionice -c 3`) |
| CPU priority | Lowest (`nice -n 19`) |
| Log rotation | 3 x 10 MB files (30 MB max) |

### Volume Mounts

| Container Path | Host Path | Purpose |
|----------------|-----------|---------|
| `/app/data` | `./data` | Database (persistent) |
| `/app/inputs` | `/mnt/mediadrive/codex_raw` | Audio input directory |
| `/app/src/living_codex/ai/codex_rules.md` | `./src/living_codex/ai/codex_rules.md` | System prompt (hot-reload, read-only) |

---

## Troubleshooting

### Bot doesn't respond to slash commands

1. Check the bot is running: `docker ps | grep living-codex`
2. Check logs for errors: `docker logs living-codex --tail 30`
3. Verify `CODEX_DISCORD_GUILD_ID` matches your server.
4. Commands take up to 1 hour to register after first deploy. Force-refresh Discord (Ctrl+R).

### "AI client not configured" error

The `CODEX_AI_MODEL` is set to a model whose API key is missing.

- If model is `claude-*`: set `CODEX_ANTHROPIC_API_KEY`
- If model is `gemini-*`: set `CODEX_GEMINI_API_KEY`
- Then restart: `docker compose up -d`

### Audio files aren't being processed

1. Verify the Gemini API key is set: check logs for `AudioWatcher started on inputs`.
2. If you see `Gemini API key not set — Scribe pipeline disabled`, add `CODEX_GEMINI_API_KEY`.
3. Check the input directory exists: `ls /mnt/mediadrive/codex_raw`
4. The watcher debounces for 10 seconds — wait at least 15 seconds after dropping a file.
5. Check for pipeline errors: `docker logs living-codex | grep -i "pipeline\|error"`

### Entities not showing up in /codex check

Entities extracted by the pipeline land in `staged_changes`, not the `entities` table. You must approve them first:

```bash
docker exec living-codex python scripts/approve.py
```

### /codex query is slow

Response time depends on:

1. **Context size:** More sessions/entities = more tokens = slower.
2. **Model choice:** `claude-haiku-4-5` is fastest; `claude-opus-4-6` is slowest.
3. **Network latency:** API calls to Anthropic/Google take 2-10 seconds.

The bot already optimizes by only sending raw transcripts for sessions that lack summaries. Run `approve.py` to generate summaries for all sessions, which reduces the context size sent to the AI.

### Database is corrupted or needs reset

The database is a single SQLite file at `./data/codex.db`. To reset:

```bash
docker compose down
rm ./data/codex.db
docker compose up -d    # recreates empty DB on startup
```

To back up:

```bash
cp ./data/codex.db ./data/codex.db.backup
```

### Foundry sync shows conflicts

A conflict means someone manually edited a journal entry in Foundry after the bot synced it. Options:

1. `/codex sync entity_name force:True` — overwrite the Foundry version
2. Manually update the entity in the Codex database to match Foundry, then sync again

### Container runs out of memory

The container is limited to 512 MB. If processing very long audio files (3+ hours), memory may spike. Options:

1. Split long recordings into smaller files before dropping in the input directory.
2. Increase the memory limit in `docker-compose.yml` under `deploy.resources.limits.memory`.
