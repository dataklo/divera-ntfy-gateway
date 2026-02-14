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

if [[ ! -x "$APP_DIR/venv/bin/pip" ]]; then
  echo "[!] Python virtualenv missing in $APP_DIR/venv - creating it..."
  python3 -m venv "$APP_DIR/venv"
fi

"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[*] Updating systemd unit..."
install -m 0644 "$REPO_ROOT/systemd/alarm-gateway.service" "$SERVICE_FILE"

echo "[*] Restarting service..."
systemctl daemon-reload
systemctl restart alarm-gateway

echo "[✓] Update complete. Logs: journalctl -u alarm-gateway -f"


echo "[*] Checking DiVeRa alarm status..."
if "$APP_DIR/venv/bin/python" "$APP_DIR/alarm_gateway.py" --check-divera-alarm; then
  echo "[✓] DiVeRa check: mind. ein aktiver Alarm vorhanden."
else
  echo "[i] DiVeRa check: kein aktiver Alarm gefunden oder API nicht erreichbar."
fi
