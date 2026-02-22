# Living Codex — Phase 3 Plan: The Scribe Pipeline

## Phase 3 Context

Build the Scribe Pipeline: audio → Gemini transcription → entity extraction → `staged_changes`.

**Modified testing flow** (user's requirement):
1. User uploads RPG podcast MP3 to a Google Drive folder (e.g., `Codex-Test`)
2. rclone pulls files from Drive → `/mnt/mediadrive/codex_raw/` (container bind mount: `/app/inputs`)
3. File watcher detects new audio
4. Pipeline uploads to Gemini Files API → transcribes → extracts entities → writes `staged_changes`
5. Audio deleted immediately after extraction (privacy)
6. Inspect DB to validate schema handles real transcription output

---

## Files to Create

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `src/living_codex/ai/prompts.py` | ~80 | Prompt templates (transcription + extraction) |
| `src/living_codex/ai/gemini.py` | ~120 | Gemini Files API client (async wrapper) |
| `src/living_codex/scribe/pipeline.py` | ~150 | Full pipeline: file → staged_changes |
| `src/living_codex/scribe/watcher.py` | ~80 | watchfiles directory monitor |
| `scripts/setup_rclone.sh` | ~40 | rclone install + config instructions |
| `tests/test_pipeline.py` | ~80 | Pipeline tests (mocked Gemini) |
| `tests/test_gemini.py` | ~60 | Files API usage tests |

**Files to modify:**
- `src/living_codex/config.py` — add `default_campaign_id: int = 1`
- `src/living_codex/bot.py` — start watcher as background task in `setup_hook`

---

## Implementation Details

### `ai/prompts.py`

Three prompt templates — return plain strings, no logic:

```python
TRANSCRIBE_SINGLE = """
You are transcribing an RPG podcast recording.
Identify and label speakers when possible (e.g., "GM:", "Player:").
Output a clean, verbatim transcript with timestamps every 5 minutes.
Format: [HH:MM] Speaker: text
"""

TRANSCRIBE_SPEAKER = """
You are transcribing audio from one speaker: {speaker_name}.
Output verbatim text. Do not add timestamps or speaker labels.
"""

EXTRACT_ENTITIES = """
You are an archivist for the TTRPG campaign: {campaign_name}.

From the transcript below, extract all named entities.
Return a JSON array. Each object must have:
  - name: string (canonical name)
  - type: one of ["NPC", "Faction", "Location", "Asset", "Clue"]
  - aliases: array of strings (nicknames, alternate names)
  - public_description: string (what players can know, max 300 chars)
  - private_description: string (GM secrets, lore not yet revealed, may be empty string)
  - relationships: array of {target_name, rel_type, citation}
  - status_label: one of ["Active", "Inactive", "Dead", "Destroyed", "Unknown"]

Redact real names, emails, or personal data — replace with [REDACTED].
Only include entities that are narratively significant.
Return ONLY valid JSON. No markdown fences. No explanation text.

TRANSCRIPT:
{transcript}
"""
```

---

### `ai/gemini.py`

**Critical architecture note:** `google-generativeai` is synchronous. All blocking calls must run
in a thread executor. The Files API must be used for audio — never load audio bytes into memory.

```python
class GeminiClient:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self._model_name = "gemini-2.0-flash"   # Per CLAUDE.md — never 1.5-flash

    async def _run(self, fn, *args, **kwargs):
        """Run blocking genai call in thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def upload_audio(self, path: Path) -> genai.File:
        """Upload via Files API. Returns File object with URI."""
        mime = _detect_mime(path)   # audio/mpeg, audio/flac, audio/wav
        return await self._run(genai.upload_file, str(path), mime_type=mime)

    async def transcribe_single(self, audio_file: genai.File) -> str:
        model = genai.GenerativeModel(self._model_name)
        response = await self._run(model.generate_content, [TRANSCRIBE_SINGLE, audio_file])
        return response.text

    async def transcribe_speaker(self, audio_file: genai.File, speaker_name: str) -> str:
        prompt = TRANSCRIBE_SPEAKER.format(speaker_name=speaker_name)
        model = genai.GenerativeModel(self._model_name)
        response = await self._run(model.generate_content, [prompt, audio_file])
        return response.text

    async def extract_entities(self, transcript: str, campaign_name: str) -> list[dict]:
        prompt = EXTRACT_ENTITIES.format(campaign_name=campaign_name, transcript=transcript)
        model = genai.GenerativeModel(self._model_name)
        response = await self._run(model.generate_content, prompt)
        return json.loads(response.text)

    async def delete_file(self, file: genai.File) -> None:
        """Clean up uploaded file from Gemini storage (48hr limit anyway)."""
        await self._run(genai.delete_file, file.name)
```

`_detect_mime()` maps `.mp3→audio/mpeg`, `.flac→audio/flac`, `.wav→audio/wav`, `.ogg→audio/ogg`.

---

### `scribe/pipeline.py`

**Mode detection logic:**
- Folder with multiple `.flac` files → per-speaker mode (Craig recording)
- Single `.mp3`/`.wav`/`.flac` file → single-file mode (podcast/manual upload)

**Pipeline steps (single-file mode for testing):**
1. Create `sessions` row (campaign_id, session_number auto-incremented, audio_path)
2. Upload audio to Gemini Files API
3. Transcribe → string
4. Extract entities → list of dicts
5. For each entity, for each field: insert `staged_changes` row
6. Delete local audio file
7. Delete Gemini uploaded file
8. Log summary: N entities staged

**Staged change field mapping:**
Each extracted entity produces multiple `staged_changes` rows:
- `field_name="description_public"`, `visibility="public"`
- `field_name="description_private"`, `visibility="private"`
- `field_name="status_label"`, `visibility="public"`
- `field_name="alias"` per alias, `visibility="public"`
- `field_name="relationship"`, `new_value="rel_type:target_name:citation"`, `visibility="public"`

Entity `id` is NULL until GM approves (new entities don't exist yet). `entity_name` and
`entity_type` carry the data. Approve logic (Phase 4) will resolve/create the entity then link.

**Error handling:** If Gemini fails, log error, set session `processed_at=NULL`, keep audio.
Only delete audio after successful extraction.

---

### `scribe/watcher.py`

```python
class AudioWatcher:
    AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg"}

    async def watch(self):
        async for changes in awatch(self.input_dir):
            for change_type, raw_path in changes:
                path = Path(raw_path)
                if change_type in (Change.added, Change.modified):
                    if path.suffix in self.AUDIO_EXTENSIONS:
                        await self._handle_file(path)
                    elif path.is_dir():
                        await self._handle_folder(path)
```

Debounce: wait 10 seconds after first detection before processing (avoids partial writes during rclone transfer).

Watcher is started in `bot.py`'s `setup_hook` as a background task:
```python
self.loop.create_task(AudioWatcher(...).watch())
```

---

### `scripts/setup_rclone.sh`

Covers:
1. Install rclone (curl installer)
2. `rclone config` instructions for Google Drive remote (interactive — can't automate OAuth)
3. Test command: `rclone ls gdrive:/Codex-Test`
4. Manual pull command for testing:
   ```bash
   rclone move "gdrive:/living-codex-transcriptions" /mnt/mediadrive/codex_raw/ --bwlimit 5M
   ```
   (Folder ID: `1wVZXzJpHT8YK2Kat8YB1vEhKJLbmosBv` — rclone will resolve by name)
5. Production cron line (commented out — add after testing confirmed):
   ```bash
   # */10 * * * * rclone move "gdrive:/living-codex-transcriptions" /mnt/mediadrive/codex_raw/ --bwlimit 5M
   ```

rclone should be installed **on the validator** (not in the container) since it needs Google OAuth.

---

### `config.py` additions

```python
default_campaign_id: int = 1                          # Fallback when folder name doesn't match a campaign
rclone_gdrive_path: str = "living-codex-transcriptions"  # Google Drive folder to pull from
```

**Production Google Drive folder:**
- Name: `living-codex-transcriptions`
- Folder ID: `1wVZXzJpHT8YK2Kat8YB1vEhKJLbmosBv`
- rclone path: `gdrive:/living-codex-transcriptions` (uses rclone remote named `gdrive`)

---

## Testing Flow (End-to-End Validation)

### Setup (one-time)
```bash
# On validator: create the input directory
mkdir -p /mnt/mediadrive/codex_raw

# Install rclone, configure gdrive remote
scp scripts/setup_rclone.sh validator:/tmp/
ssh validator "bash /tmp/setup_rclone.sh"

# Seed the DB with campaigns (provides campaign_id=1)
docker exec living-codex python scripts/seed.py
```

### Per-test run
```bash
# 1. Upload a short (~10 min) RPG podcast clip to Google Drive "Codex-Test" folder

# 2. Pull it to the server
ssh validator "rclone move 'gdrive:/Codex-Test' /mnt/mediadrive/codex_raw/ --bwlimit 5M"

# 3. Watch pipeline logs
docker logs living-codex -f

# 4. Inspect staged changes
docker exec living-codex python -c "
import asyncio, aiosqlite
async def show():
    async with aiosqlite.connect('./data/codex.db') as db:
        async with db.execute(
            'SELECT entity_name, entity_type, field_name, new_value, visibility FROM staged_changes WHERE status=?',
            ('pending',)
        ) as cur:
            async for row in cur:
                print(row)
asyncio.run(show())
"
```

### Schema validation checklist
After running on real audio, verify:
- [ ] `sessions` row created with correct `campaign_id`, auto-incremented `session_number`
- [ ] `staged_changes` rows exist for every extracted entity × field
- [ ] `description_private` rows have `visibility='private'`
- [ ] `alias` rows correctly reference parent `entity_name`
- [ ] `relationship` new_value format parses cleanly (`rel_type:target:citation`)
- [ ] Audio file deleted from `/app/inputs` after extraction
- [ ] Gemini uploaded file deleted (call `genai.list_files()` — should be empty)
- [ ] RAM under 512MB during pipeline run: `docker stats living-codex`

### Test data recommendation
Use a **5-10 minute clip** for first test run — reduces API cost (~$0.01-0.05) and iteration time.
Full session FLAC (60+ min) for second run once schema is confirmed.

---

## Tests to Write

### `test_gemini.py`
- `test_upload_uses_files_api` — assert `genai.upload_file` called (not `generate_content` with raw bytes)
- `test_model_is_flash` — assert model name is `gemini-2.0-flash` not `1.5-flash`
- `test_extract_returns_list` — mock response with valid JSON, assert list of dicts returned
- `test_extract_invalid_json_raises` — mock bad response, assert raises

### `test_pipeline.py`
- `test_single_file_creates_session` — process a mocked file, assert `sessions` row created
- `test_staged_changes_per_field` — one entity with 3 fields → 3 `staged_changes` rows
- `test_alias_staged_correctly` — alias in extraction → `field_name="alias"` staged_change
- `test_audio_deleted_after_success` — mock gemini, assert `unlink()` called on audio
- `test_audio_preserved_on_failure` — mock gemini to raise, assert audio NOT deleted

---

## Verification (Phase 3 Complete)

- [ ] Craig folder → watcher detects → per-speaker mode triggered
- [ ] Single MP3 → watcher detects → single-file mode triggered
- [ ] Audio uploaded via Files API (not loaded into memory)
- [ ] `staged_changes` populated after processing
- [ ] Audio deleted from disk after extraction
- [ ] `docker stats` — RAM ≤ 512MB during pipeline run
- [ ] All `test_pipeline.py` and `test_gemini.py` pass
