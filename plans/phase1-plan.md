# Phase 1 Foundation — Detailed Implementation Plan

## Context
Phase 1 of the Living Codex is code-complete. All required files are already written and structurally correct. The remaining work is: run the tests locally to verify correctness, then deploy to the validator and satisfy the acceptance criteria from the plan. No new Python files need to be written for Phase 1.

---

## Code Inventory (All Complete ✅)

- **pyproject.toml**: ✅ Complete. Key Detail: All deps incl. pydantic-settings, hatchling, ruff
- **src/living_codex/config.py**: ✅ Complete. Key Detail: CodexConfig(BaseSettings), env_prefix="CODEX_", all required fields
- **src/living_codex/database.py**: ✅ Complete. Key Detail: 8 tables, 8 indexes, WAL mode, FK ON, CodexDB class
- **src/living_codex/bot.py**: ✅ Complete. Key Detail: LivingCodex(commands.Bot), /codex ping (ephemeral), guild-specific sync
- **src/living_codex/main.py**: ✅ Complete. Key Detail: Logging, graceful shutdown, bot.run()
- **Dockerfile**: ✅ Complete. Key Detail: python:3.10-slim, nice -n 19 ionice -c 3 in CMD
- **docker-compose.yml**: ✅ Complete. Key Detail: 0.5 vCPU / 512M hard caps, NVMe data vol + HDD input vol
- **tests/conftest.py**: ✅ Complete. Key Detail: db fixture (fresh schema), seeded_db (Vrax, 4th Fleet, Green Box, Kora)
- **tests/test_database.py**: ✅ Complete. Key Detail: 10 tests (schema, WAL, FK, indexes, type constraint, config, campaign, meta)

---

## Step 1: Local Test Run

Goal: Prove the code is correct before touching the validator.

\\\ash
# From the project directory on Windows
cd C:\Users\brett\validator-universe\projects\living-codex

# Install package + dev deps (use whichever python is active)
pip install -e ".[dev]"

# Run Phase 1 tests
pytest tests/test_database.py -v
\\\

Expected output: 10/10 pass. Specific tests:
- test_schema_creates_all_tables — 8 tables present
- test_wal_mode_enabled — journal_mode = wal
- test_foreign_keys_enabled — foreign_keys = 1
- test_indexes_exist — 8 idx_* indexes present
- test_entity_type_check_constraint — INSERT with type='Garbage' raises
- test_config_loads_from_env — monkeypatched vars load into CodexConfig
- test_config_missing_required_field — missing vars raise ValidationError
- test_get_or_create_campaign — idempotent
- test_seeded_data — 4 entities, 3 aliases returned
- test_meta_get_set — set/get/overwrite round-trips

If any test fails: Fix before proceeding. Do NOT deploy broken code.

---

## Step 2: Pre-Deployment Validator Gate

Run these from the validator server (\ssh validator\) before touching Docker:

\\\ash
# Validator healthy?
curl -s -X POST --data '{"jsonrpc":"2.0","id":1,"method":"health.health"}' \
  -H 'content-type:application/json' http://127.0.0.1:9650/ext/health | jq .result.healthy
# Must return: true

# Rewarding stake nominal?
curl -s 'http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake'
# Must show ~99.89% (current baseline) — not below 95%

# Resource headroom?
free -h && df -h /
# Expect: ~24GB RAM free, root disk <80% used
\\\

Abort condition: If validator is unhealthy or rewarding stake has dropped, stop and investigate before any deployment.

---

## Step 3: Create Missing Input Directory

The HDD path \/mnt/mediadrive/codex_raw\ does not yet exist on the server. Docker Compose will fail silently if the bind mount source doesn't exist.

\\\ash
ssh validator "sudo mkdir -p /mnt/mediadrive/codex_raw && sudo chown brettfarmer:brettfarmer /mnt/mediadrive/codex_raw"
\\\

---

## Step 4: Transfer Project to Validator

\\\ash
# From Windows (PowerShell or Git Bash)
# SCP the full project directory — .env is included intentionally (has real credentials)
scp -i C:/Users/brett/.ssh/avax_validator_automation -r \
  C:/Users/brett/validator-universe/projects/living-codex \
  brettfarmer@207.237.250.18:~/living-codex
\\\

Note: The \.env\ file contains real Discord/Gemini credentials and WILL be transferred. This is correct and necessary for the container to start.

---

## Step 5: Build and Start the Container

\\\ash
ssh validator "cd ~/living-codex && docker compose up -d --build"

# Verify container started
ssh validator "docker ps --filter name=living-codex"
\\\

Expected: \living-codex\ shows Up X seconds.

Check logs for startup errors:
\\\ash
ssh validator "docker logs living-codex --tail 30"
\\\

Expected log lines:
- Database initialized at /app/data/codex.db
- Commands synced to guild <guild_id>.
- Logged in as LivingCodex#XXXX (ID: ...)

---

## Step 6: Acceptance Verification

All three criteria from the plan must pass before Phase 1 is done.

### 6a. /ping in Discord

- Go to the configured guild's any channel
- Type \/codex ping\
- Must respond ephemeral: \Pong! Latency: XXms\

### 6b. Schema Correct

\\\ash
ssh validator "docker exec living-codex python -c \"
import asyncio
from pathlib import Path
from living_codex.database import CodexDB, EXPECTED_TABLES, EXPECTED_INDEXES

async def check():
    db = CodexDB(Path('/app/data/codex.db'))
    await db.connect()
    cursor = await db.db.execute(\\\"SELECT name FROM sqlite_master WHERE type='table'\\\")
    rows = await cursor.fetchall()
    tables = {r[0] for r in rows}
    missing = EXPECTED_TABLES - tables
    print('Missing tables:', missing or 'NONE')
    await db.close()

asyncio.run(check())
\""
\\\

Expected: \Missing tables: NONE\

### 6c. Resource Compliance

\\\ash
ssh validator "docker stats living-codex --no-stream --format 'CPU: {{.CPUPerc}}  MEM: {{.MemUsage}}'"
\\\

Must show:
- CPU: under 50% (at idle, should be <5%)
- Memory: under 512MiB (at idle, should be <150MiB)

### 6d. Validator Still Healthy (Post-Deploy)

Re-run the health check from Step 2. Rewarding stake must be unchanged.

---

## Phase 1 Definition of Done

- 10/10 test_database.py tests pass locally
- Container starts and logs show successful DB init + Discord login
- \/codex ping\ responds ephemeral in Discord
- Schema check shows \Missing tables: NONE\
- docker stats shows CPU <50%, RAM <512MiB
- Validator still healthy, rewarding stake ≥ 99%

---

## Known Risks

- **Risk**: .env missing on server
  - **Mitigation**: SCP transfers it; verify with \ssh validator "ls ~/living-codex/.env"\
- **Risk**: /mnt/mediadrive/codex_raw missing
  - **Mitigation**: Step 3 creates it
- **Risk**: Docker image build fails
  - **Mitigation**: Check docker logs for pip install errors; all deps are pinned in pyproject.toml
- **Risk**: Discord token expired
  - **Mitigation**: Check .env has valid CODEX_DISCORD_TOKEN
- **Risk**: OOM kill during startup
  - **Mitigation**: docker stats would show container restarting; 512MB cap is very conservative for idle bot

## Rollback

If anything goes wrong after deployment:
\\\ash
ssh validator "docker compose -f ~/living-codex/docker-compose.yml down"
\\\
Zero residue — no ports opened, no validator files touched. Validator is unaffected.
