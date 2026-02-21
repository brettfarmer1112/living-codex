# Living Codex Deployment Runbook

**Last Updated:** 2026-02-21
**Deployment Target:** axavvalidator (207.237.250.18)
**Purpose:** Deploy Living Codex D&D campaign management Discord bot as Docker container

---

## Pre-Mortem (Risk Assessment)

### Dependencies

**System Requirements:**
- Docker: **VERIFIED** ✅ (version check required)
- docker-compose: **VERIFIED** ✅ (version check required)
- Available memory: **24GB free** ✅ (512MB + 100MB overhead = conservative)
- Available CPU: **Load avg 0.29** ✅ (0.5 core addition is safe)
- Disk space: **1006GB available on root** ✅

**Paths:**
- `/mnt/mediadrive/codex_raw` — ⚠️ **DOES NOT EXIST** (must create)
- `./data` (local) — Will be created by Docker
- Workspace root — docker-compose.yml location

**Secrets:**
- `CODEX_DISCORD_TOKEN` — From `.env` (must configure)
- `CODEX_DISCORD_GUILD_ID` — From `.env` (must configure)
- `CODEX_GM_ROLE_ID` — From `.env` (must configure)
- `CODEX_GM_CHANNEL_ID` — From `.env` (must configure)
- `CODEX_PLAYER_CHANNEL_ID` — From `.env` (must configure)
- `CODEX_GEMINI_API_KEY` — **Needs new key** (manual setup required)

**Service Dependencies:**
- Discord API (external dependency)
- Gemini API (external dependency)
- Local Docker daemon

### Failure Modes

| Failure Mode | Symptom | Impact | Mitigation |
|--------------|---------|--------|------------|
| **Insufficient memory** | Container OOM killed | Other services impacted, validator could be affected | Resource limits enforced (512MB hard cap), monitor `docker stats` |
| **Path missing** | Container mount failure | Container won't start | Pre-deployment verification creates path |
| **Discord auth failure** | Bot offline in guild | No user impact, feature unavailable | Test token before full deployment |
| **Gemini API quota** | Processing stalls | Reduced functionality | Monitor quota, implement rate limiting |
| **Docker daemon conflict** | Container won't start | Deployment blocked | Check existing containers, verify ports |
| **Port conflict** | Port binding failure | Container won't start | No exposed ports in compose = unlikely |
| **Log volume growth** | Disk fills unexpectedly | System-wide impact | 3×10MB rotation = 30MB max, monitor with `du` |
| **Validator impact** | Rewarding stake drops | Revenue loss | Pre/post validation stake checks, immediate rollback if <95% |

### Rollback Plan

**Step 1: Stop Container**
```bash
docker stop living-codex
```
*Preserves data volume, immediately frees resources*

**Step 2: Verify Validator Unaffected**
```bash
# Check rewarding stake (must be ≥95%)
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake | jq -r '.data.result[0].value[1]'

# Check peer count (must be >100)
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_peers | jq -r '.data.result[0].value[1]'
```

**Step 3: Verify Memory Recovery**
```bash
free -h
# Should show ~512MB freed
```

**Step 4: Remove Container (if data corruption suspected)**
```bash
docker rm living-codex
# Note: Destroys container, preserves ./data volume
```

**Step 5: Restore Configuration (if config changes made)**
```bash
git checkout .env docker-compose.yml
```

### Success Criteria

- [ ] Container status: `docker ps` shows "healthy" or "running" status
- [ ] Memory within budget: `docker stats living-codex` shows ≤512MB
- [ ] CPU within budget: `docker stats living-codex` shows ≤50% (0.5 core)
- [ ] Logs clean: `docker logs living-codex --tail 50` shows no errors
- [ ] Discord bot online: Bot appears online in Discord guild member list
- [ ] Validator unaffected: Rewarding stake % ≥99%, peer count ≥3000
- [ ] No resource alerts: Prometheus/Grafana show stable metrics

---

## Pre-Deployment Verification

**Assumptions:**
- OS: Ubuntu 24.04 LTS
- User: brettfarmer (SSH key authentication)
- Working directory: `c:\Users\brett\Documents\Projects\living-codex`
- Deployment method: SCP workspace → SSH execute

### Step 1: Verify Local Workspace

```powershell
# From Windows workstation
cd C:\Users\brett\Documents\Projects\living-codex

# Verify docker-compose.yml exists
cat docker-compose.yml

# Verify .env exists and has required keys
cat .env | grep -E "CODEX_DISCORD_TOKEN|CODEX_GEMINI_API_KEY"
```

**Verification:** All files exist, .env contains Discord/Gemini keys

### Step 2: Check Remote Docker Health

```bash
ssh -i ~/.ssh/avax_validator_automation brettfarmer@207.237.250.18

# Verify Docker daemon running
sudo systemctl status docker

# Verify docker-compose available
docker compose version

# Check current resource usage
free -h
df -h
docker ps -a
```

**Verification:**
- Docker daemon: active (running)
- docker-compose: v2.x or higher
- Memory: ≥1GB free
- Disk: ≥10GB free on root

### Step 3: Create Missing Paths

```bash
# Create codex_raw directory on media drive
sudo mkdir -p /mnt/mediadrive/codex_raw
sudo chown brettfarmer:brettfarmer /mnt/mediadrive/codex_raw
sudo chmod 755 /mnt/mediadrive/codex_raw

# Verify path exists and is writable
ls -ld /mnt/mediadrive/codex_raw
touch /mnt/mediadrive/codex_raw/test && rm /mnt/mediadrive/codex_raw/test
```

**Verification:** Path exists, owned by brettfarmer, writable

### Step 4: Baseline Validator Metrics

```bash
# Record current metrics for comparison
echo "=== PRE-DEPLOYMENT BASELINE ===" > /tmp/codex_deployment_baseline.txt
date >> /tmp/codex_deployment_baseline.txt

# Rewarding stake
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake | jq -r '.data.result[0].value[1]' | tee -a /tmp/codex_deployment_baseline.txt

# Peer count
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_peers | jq -r '.data.result[0].value[1]' | tee -a /tmp/codex_deployment_baseline.txt

# Memory usage
free -h | tee -a /tmp/codex_deployment_baseline.txt

cat /tmp/codex_deployment_baseline.txt
```

**Verification:** Baseline recorded, stake ≥95%, peers ≥3000

---

## Deployment Procedure (Battle Plan)

### Step 1: Transfer Workspace to Server

```powershell
# From Windows workstation
cd C:\Users\brett\Documents\Projects\living-codex

# Create deployment archive (exclude unnecessary files)
tar -czf living-codex-deploy.tar.gz --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='data' .

# Transfer to server
scp -i ~/.ssh/avax_validator_automation living-codex-deploy.tar.gz brettfarmer@207.237.250.18:/tmp/

# Clean up local archive
rm living-codex-deploy.tar.gz
```

**Verification:** Archive transferred successfully (check file size)

### Step 2: Extract and Position on Server

```bash
# SSH to server
ssh -i ~/.ssh/avax_validator_automation brettfarmer@207.237.250.18

# Create deployment directory
mkdir -p ~/living-codex
cd ~/living-codex

# Extract archive
tar -xzf /tmp/living-codex-deploy.tar.gz

# Verify extraction
ls -la
cat docker-compose.yml
```

**Verification:** Files extracted, docker-compose.yml present

### Step 3: Build Container Image

```bash
cd ~/living-codex

# Build the image
docker build -t living-codex:latest .

# Verify image built
docker images | grep living-codex
```

**Verification:** Image `living-codex:latest` appears in `docker images`

### Step 4: Start Container

```bash
cd ~/living-codex

# Start in foreground first (easier to catch immediate errors)
docker compose up

# Watch logs for 30-60 seconds
# Look for successful Discord connection, database initialization
# Press Ctrl+C when confirmed healthy

# Start in background (detached)
docker compose up -d

# Verify container running
docker ps | grep living-codex
```

**Verification:** Container status "Up X seconds" or "healthy"

### Step 5: Health Checks

```bash
# Check container stats
docker stats living-codex --no-stream

# Check logs for errors
docker logs living-codex --tail 50

# Verify Discord bot online
# (Manual check in Discord guild member list)

# Check database created
ls -lh ~/living-codex/data/
```

**Verification:**
- Memory: ≤512MB
- CPU: ≤50%
- Logs: No ERROR or CRITICAL messages
- Discord: Bot shows online
- Database: codex.db file exists

### Step 6: Post-Deployment Validator Check

```bash
# Wait 5 minutes after deployment, then check metrics

# Rewarding stake (compare to baseline)
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake | jq -r '.data.result[0].value[1]'

# Peer count (compare to baseline)
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_peers | jq -r '.data.result[0].value[1]'

# Memory delta
free -h
```

**Verification:**
- Stake: No decrease (tolerance: ±0.1%)
- Peers: No decrease (tolerance: ±50 peers)
- Memory: Increased by ~512MB (expected)

### Step 7: Functional Testing

```bash
# Test Discord commands (from Discord client)
# /help (should respond with command list)
# /status (should show bot status)

# Verify Gemini API connectivity
docker logs living-codex | grep -i gemini

# Monitor for 24 hours, check logs daily
docker logs living-codex --since 24h --tail 100
```

**Verification:** Bot responds to commands, Gemini API functional

---

## Monitoring & Ongoing Health

### Daily Checks (First Week)

```bash
# Container health
docker ps | grep living-codex

# Resource usage
docker stats living-codex --no-stream

# Error logs
docker logs living-codex --since 24h | grep -E "ERROR|CRITICAL"
```

### Weekly Checks (Ongoing)

```bash
# Disk usage (logs, database growth)
du -sh ~/living-codex/data/

# Validator stability
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake | jq -r '.data.result[0].value[1]'
```

### Alert Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Container memory | >450MB | >512MB (OOM) | Investigate memory leak, consider restart |
| Container CPU | >75% | >100% | Check for infinite loops, rate limit API calls |
| Log size | >20MB | >30MB | Verify rotation working, check for log spam |
| Database size | >500MB | >1GB | Review retention policy, archive old sessions |
| Validator stake | <97% | <95% | Investigate impact, consider rollback |

---

## Rollback Execution (If Needed)

**Trigger Conditions:**
- Validator rewarding stake drops below 95%
- Container repeatedly crashes (>3 restarts in 1 hour)
- Critical resource exhaustion (OOM killer active)
- Discord bot authentication failures (>30 minutes)

**Rollback Steps:**
```bash
# 1. Stop container immediately
docker compose down

# 2. Verify validator recovery
curl -s http://127.0.0.1:9090/api/v1/query?query=avalanche_network_node_uptime_rewarding_stake | jq -r '.data.result[0].value[1]'

# 3. Check memory freed
free -h

# 4. If data corruption suspected
docker rm living-codex
# (Preserves ./data volume for later investigation)

# 5. Document failure for post-mortem
docker logs living-codex > /tmp/codex_failure_$(date +%Y%m%d_%H%M%S).log
```

**Post-Rollback:**
- Analyze failure logs
- Fix root cause
- Test in isolated environment before retry
- Consider resource limit adjustments

---

## Definition of Done

**Deployment Complete When:**
- ✅ Living Codex container running for 24 hours without crashes
- ✅ Memory usage stable at ≤512MB
- ✅ CPU usage stable at ≤50%
- ✅ Discord bot responsive to commands
- ✅ Gemini API integration functional
- ✅ Validator metrics unchanged (stake ≥99%, peers ≥3000)
- ✅ No ERROR logs in past 24 hours
- ✅ Database growing as expected (sessions persisted)
- ✅ Monitoring added to daily routine

**Sign-Off:** Document completion date, final resource metrics, any deviations from plan in deployment log.
