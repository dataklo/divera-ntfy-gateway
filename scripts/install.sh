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

# PUSH TARGET (choose one variant)
# Variant A: ntfy
NTFY_URL="https://ntfy.example.com"
NTFY_TOPIC="fw-alarme-x9k3p"

# Variant B: UnifiedPush endpoint (e.g. Nextcloud uppush)
# UPPUSH_ENDPOINT="https://nextcloud.example.com/index.php/apps/uppush/push/<endpoint-token>"
# Optional auth header for endpoint auth:
# UPPUSH_AUTH_HEADER="Bearer <token>"

# OPTIONAL
DIVERA_URL="https://divera247.com/api/v2/alarms"
POLL_SECONDS="20"
STATE_FILE="/var/lib/alarm-gateway/state.json"
NTFY_PRIORITY="5"
REQUEST_TIMEOUT="15"
VERIFY_TLS="true"

# OPTIONAL (Shelly Plus Uni input polling)
# SHELLY_UNI_URL="http://192.168.1.50"
# SHELLY_INPUT_IDS="0,1"
# SHELLY_POLL_SECONDS="1"
# SHELLY_TRIGGER_ON="true"
# SHELLY_DEBOUNCE_SECONDS="10"
# SHELLY_TITLE_TEMPLATE="Shelly Input {input_id}"
# SHELLY_MESSAGE_TEMPLATE="Shelly Plus Uni Eingang {input_id} wurde ausgelöst."
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
