#!/usr/bin/env bash
# setup_rclone.sh — Install and configure rclone for Living Codex audio ingestion.
# Run on the validator host (NOT inside the container) — needs Google OAuth.
set -euo pipefail

echo "=== Living Codex — rclone Setup ==="

# 1. Install rclone
if command -v rclone &>/dev/null; then
    echo "[OK] rclone already installed: $(rclone version | head -1)"
else
    echo "[*] Installing rclone..."
    curl https://rclone.org/install.sh | sudo bash
    echo "[OK] rclone installed: $(rclone version | head -1)"
fi

# 2. Configure Google Drive remote
echo ""
echo "=== Google Drive Configuration ==="
echo "Run: rclone config"
echo ""
echo "  n) New remote"
echo "  name: gdrive"
echo "  Storage: drive (Google Drive)"
echo "  client_id: (leave blank for default)"
echo "  client_secret: (leave blank for default)"
echo "  scope: 1 (Full access)"
echo "  root_folder_id: (leave blank)"
echo "  service_account_file: (leave blank)"
echo "  Edit advanced config? n"
echo "  Use auto config? n (for headless server — copy the link to your browser)"
echo ""
echo "After config, test with:"
echo "  rclone ls gdrive:/living-codex-transcriptions"
echo ""

# 3. Create the local input directory
INPUT_DIR="/mnt/mediadrive/codex_raw"
if [ -d "$INPUT_DIR" ]; then
    echo "[OK] Input directory exists: $INPUT_DIR"
else
    echo "[*] Creating input directory: $INPUT_DIR"
    sudo mkdir -p "$INPUT_DIR"
    sudo chown "$(whoami):$(whoami)" "$INPUT_DIR"
    echo "[OK] Created $INPUT_DIR"
fi

# 4. Manual pull command (for testing)
echo ""
echo "=== Manual Pull Command ==="
echo "rclone move \"gdrive:/living-codex-transcriptions\" $INPUT_DIR --bwlimit 5M"
echo ""
echo "Google Drive folder: living-codex-transcriptions"
echo "Folder ID: 1wVZXzJpHT8YK2Kat8YB1vEhKJLbmosBv"

# 5. Production cron (uncomment after testing confirmed)
echo ""
echo "=== Production Cron ==="
echo "Add to crontab (crontab -e):"
echo "# Pull audio from Google Drive every 10 minutes"
echo "# */10 * * * * rclone move \"gdrive:/living-codex-transcriptions\" $INPUT_DIR --bwlimit 5M"
