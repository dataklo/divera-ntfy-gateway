#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/alarm-gateway"
ENV_DIR="/etc/alarm-gateway"
STATE_DIR="/var/lib/alarm-gateway"
SERVICE_FILE="/etc/systemd/system/alarm-gateway.service"
USER_NAME="alarm-gateway"

echo "[*] Installing packages..."
apt update
apt install -y python3 python3-venv python3-pip ca-certificates curl rsync

echo "[*] Creating user/group..."
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi

echo "[*] Creating directories..."
mkdir -p "$APP_DIR" "$ENV_DIR" "$STATE_DIR"
chown -R "$USER_NAME:$USER_NAME" "$STATE_DIR"

echo "[*] Copying application files to $APP_DIR ..."
rsync -a --delete \
  --exclude ".git" \
  --exclude ".github" \
  --exclude "scripts" \
  --exclude "systemd" \
  "$REPO_ROOT/" "$APP_DIR/"

echo "[*] Creating python venv + installing requirements..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[*] Installing systemd unit..."
install -m 0644 "$REPO_ROOT/systemd/alarm-gateway.service" "$SERVICE_FILE"

echo "[*] Installing environment file template..."
if [[ ! -f "$ENV_DIR/alarm-gateway.env" ]]; then
  cat > "$ENV_DIR/alarm-gateway.env" <<'EOF'
# REQUIRED
DIVERA_ACCESSKEY="PASTE_YOUR_DIVERA_ACCESSKEY_HERE"

NTFY_URL="https://ntfy.example.com"
NTFY_TOPIC="fw-alarme-x9k3p"


# OPTIONAL
DIVERA_URL="https://www.divera247.com/api/v2/alarms?accesskey=<API-Key>"
DIVERA_FALLBACK_URL="https://divera247.com/api/v2/alarms?accesskey=<API-Key>"
POLL_SECONDS="20"
STATE_FILE="/var/lib/alarm-gateway/state.json"

# ntfy priority / routing
NTFY_PRIORITY="5"
NTFY_DEFAULT_PRIORITY="5"
NTFY_PRIORITY_KEYWORDS=""
NTFY_AUTH_TOKEN=""
NTFY_FALLBACK_URLS=""
NTFY_RETRY_ATTEMPTS="2"
NTFY_RETRY_DELAY_SECONDS="1.5"
NTFY_RETRY_JITTER_SECONDS="0.0"

REQUEST_TIMEOUT="15"
VERIFY_TLS="true"
LOG_LEVEL="INFO"

# Cluster / HA
NODE_ID="gateway-standort-a"
NODE_PRIORITY="100"
PEER_NODES=""
CLUSTER_PING_TIMEOUT="2"
CLUSTER_STATUS_TTL_SECONDS="5"
CLUSTER_SHARED_TOKEN=""

# Audit Log
AUDIT_LOG_FILE=""

# Webhook / Trigger / UI
WEBHOOK_ENABLED="true"
WEBHOOK_BIND="0.0.0.0"
WEBHOOK_PORT="8080"
WEBHOOK_PATH="/webhook/alarm"
WEBHOOK_TRIGGER_PATH="/webhook/trigger"
WEBHOOK_UI_PATH="/"
WEBHOOK_TOKEN=""

# Replay-Schutz (optional)
WEBHOOK_REPLAY_PROTECTION="false"
WEBHOOK_MAX_SKEW_SECONDS="120"
WEBHOOK_HMAC_SECRET=""

# Separater Health-Port
HEALTH_ENABLED="true"
HEALTH_BIND="0.0.0.0"
HEALTH_PORT="8081"
HEALTH_PATH="/healthz"
HEALTH_METRICS_PATH="/metrics"
EOF
  chmod 0640 "$ENV_DIR/alarm-gateway.env"
  chown root:"$USER_NAME" "$ENV_DIR/alarm-gateway.env"
  echo "[!] Created $ENV_DIR/alarm-gateway.env - please edit it now!"
else
  echo "[*] Environment file already exists: $ENV_DIR/alarm-gateway.env"
fi

echo "[*] Setting ownership..."
chown -R root:root "$APP_DIR"
chown -R "$USER_NAME:$USER_NAME" "$STATE_DIR"

echo "[*] Enabling + starting service..."
systemctl daemon-reload
systemctl enable --now alarm-gateway

echo
echo "[✓] Installed."
echo "Next steps:"
echo "  1) Edit:   $ENV_DIR/alarm-gateway.env"
echo "  2) Restart systemctl restart alarm-gateway"
echo "  3) Logs:   journalctl -u alarm-gateway -f"
