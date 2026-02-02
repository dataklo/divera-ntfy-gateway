#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/alarm-gateway"
SERVICE_FILE="/etc/systemd/system/alarm-gateway.service"

echo "[*] Updating application files in $APP_DIR ..."
rsync -a --delete \
  --exclude ".git" \
  --exclude ".github" \
  --exclude "scripts" \
  --exclude "systemd" \
  "$REPO_ROOT/" "$APP_DIR/"

echo "[*] Updating python dependencies..."
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[*] Updating systemd unit..."
install -m 0644 "$REPO_ROOT/systemd/alarm-gateway.service" "$SERVICE_FILE"

echo "[*] Restarting service..."
systemctl daemon-reload
systemctl restart alarm-gateway

echo "[✓] Update complete. Logs: journalctl -u alarm-gateway -f"
