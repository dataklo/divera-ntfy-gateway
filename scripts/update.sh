#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/alarm-gateway"
SERVICE_NAME="alarm-gateway"
DEFAULT_REPO="dataklo/divera-ntfy-gateway"
DEFAULT_BRANCH="main"

ENV_FILE="${ALARM_GATEWAY_ENV_FILE:-/etc/alarm-gateway/alarm-gateway.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

REPO="${UPDATE_REPO:-$DEFAULT_REPO}"
BRANCH="${UPDATE_BRANCH:-$DEFAULT_BRANCH}"
VERSION_FILE="$APP_DIR/VERSION"
ENV_FILE_PATH="${ALARM_GATEWAY_ENV_FILE:-/etc/alarm-gateway/alarm-gateway.env}"
SELECTED_BRANCH="$BRANCH"

fetch_default_branch() {
  local response default_branch
  response="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/${REPO}" 2>/dev/null || true)"
  if [[ -z "$response" ]]; then
    return 1
  fi

  default_branch="$(python3 -c "import json,sys; print(json.loads(sys.stdin.read() or '{}').get('default_branch',''))" <<<"$response" 2>/dev/null || true)"
  if [[ -z "$default_branch" ]]; then
    return 1
  fi

  printf '%s\n' "$default_branch"
}

fetch_latest_sha() {
  local response sha branch_ref="$1"
  response="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/${REPO}/commits/${branch_ref}" 2>/dev/null || true)"
  if [[ -z "$response" ]]; then
    return 1
  fi

  sha="$(python3 -c "import json,sys; print(json.loads(sys.stdin.read() or '{}').get('sha',''))" <<<"$response" 2>/dev/null || true)"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    return 1
  fi

  printf '%s\n' "$sha"
}

latest_sha="$(fetch_latest_sha "$BRANCH" || true)"
if [[ -z "$latest_sha" ]]; then
  fallback_branch="$(fetch_default_branch || true)"
  if [[ -n "$fallback_branch" && "$fallback_branch" != "$BRANCH" ]]; then
    SELECTED_BRANCH="$fallback_branch"
    latest_sha="$(fetch_latest_sha "$SELECTED_BRANCH" || true)"
  fi
fi
if [[ -z "$latest_sha" ]]; then
  echo "[!] Konnte keine aktuelle SHA von GitHub lesen (${REPO}@${BRANCH})."
  exit 1
fi

current_sha=""
if [[ -f "$VERSION_FILE" ]]; then
  current_sha="$(tr -d '[:space:]' < "$VERSION_FILE")"
fi

if [[ "${1:-}" == "--check" ]]; then
  if [[ -n "$current_sha" && "$current_sha" == "$latest_sha" ]]; then
    echo "Kein Update verfügbar (${current_sha:0:7})."
    exit 1
  fi

  if [[ -n "$current_sha" ]]; then
    echo "Update verfügbar (${current_sha:0:7} -> ${latest_sha:0:7})."
  else
    echo "Remote-Version gefunden (${latest_sha:0:7}), lokale VERSION fehlt."
  fi
  exit 0
fi

if [[ -n "$current_sha" && "$current_sha" == "$latest_sha" ]]; then
  echo "[*] Bereits aktuell (${current_sha:0:7}), kein Update nötig."
  exit 0
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "[*] Lade ${REPO}@${SELECTED_BRANCH} ..."
curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${SELECTED_BRANCH}" -o "$tmp_dir/source.tar.gz"
tar -xzf "$tmp_dir/source.tar.gz" -C "$tmp_dir"

src_dir="$(find "$tmp_dir" -maxdepth 1 -mindepth 1 -type d -name '*-*' | head -n 1)"
if [[ -z "$src_dir" ]]; then
  echo "[!] Konnte entpacktes Quellverzeichnis nicht finden."
  exit 1
fi

echo "[*] Synchronisiere Dateien nach $APP_DIR ..."
rsync -a --delete \
  --exclude ".git" \
  --exclude ".github" \
  --exclude "venv" \
  --exclude "scripts" \
  --exclude "systemd" \
  "$src_dir/" "$APP_DIR/"

install -d -m 0755 "$APP_DIR/scripts"
install -m 0755 "$src_dir/scripts/update.sh" "$APP_DIR/scripts/update.sh"

echo "[*] Aktualisiere sudoers-Regeln für Admin-Aktionen ..."
cat > /etc/sudoers.d/alarm-gateway-update <<'EOF'
alarm-gateway ALL=(root) NOPASSWD: /opt/alarm-gateway/scripts/update.sh
EOF
chmod 0440 /etc/sudoers.d/alarm-gateway-update
cat > /etc/sudoers.d/alarm-gateway-admin <<'EOF'
alarm-gateway ALL=(root) NOPASSWD: /usr/bin/systemctl restart alarm-gateway
alarm-gateway ALL=(root) NOPASSWD: /usr/sbin/reboot
alarm-gateway ALL=(root) NOPASSWD: /sbin/reboot
EOF
chmod 0440 /etc/sudoers.d/alarm-gateway-admin

if id -u alarm-gateway >/dev/null 2>&1; then
  install -d -m 0775 -o root -g alarm-gateway "/etc/alarm-gateway"
  if [[ -f "$ENV_FILE_PATH" ]]; then
    chown root:alarm-gateway "$ENV_FILE_PATH"
    chmod 0660 "$ENV_FILE_PATH"
  fi
fi

if [[ ! -x "$APP_DIR/venv/bin/pip" ]]; then
  echo "[!] Python virtualenv fehlt - wird erstellt..."
  python3 -m venv "$APP_DIR/venv"
fi

echo "[*] Aktualisiere Python-Abhängigkeiten ..."
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[*] Aktualisiere systemd Unit ..."
install -m 0644 "$src_dir/systemd/alarm-gateway.service" "/etc/systemd/system/alarm-gateway.service"

echo "$latest_sha" > "$VERSION_FILE"

echo "[*] Starte Service neu ..."
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

echo "[✓] Update abgeschlossen: ${latest_sha:0:7}"
