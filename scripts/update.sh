#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/alarm-gateway"
SERVICE_FILE="/etc/systemd/system/alarm-gateway.service"

is_git_repo() {
  git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

check_for_update() {
  if ! is_git_repo; then
    echo "[i] Kein Git-Repository unter $REPO_ROOT gefunden."
    return 1
  fi

  local branch upstream ahead behind
  branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"

  if ! upstream="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name "${branch}@{upstream}" 2>/dev/null)"; then
    echo "[i] Kein Upstream für Branch '$branch' konfiguriert."
    return 1
  fi

  git -C "$REPO_ROOT" fetch --quiet
  read -r ahead behind < <(git -C "$REPO_ROOT" rev-list --left-right --count "${upstream}...HEAD")

  if (( ahead > 0 )); then
    echo "[i] Lokaler Branch ist $ahead Commit(s) vor $upstream."
    return 1
  fi

  if (( behind > 0 )); then
    echo "Update verfügbar ($behind Commit(s) hinter $upstream)."
    return 0
  fi

  echo "Kein Update verfügbar."
  return 1
}

if [[ "${1:-}" == "--check" ]]; then
  check_for_update
  exit $?
fi

if is_git_repo; then
  echo "[*] Synchronizing repository in $REPO_ROOT ..."
  git -C "$REPO_ROOT" fetch --quiet
  git -C "$REPO_ROOT" pull --ff-only
else
  echo "[i] Kein Git-Repository unter $REPO_ROOT gefunden - überspringe git pull."
fi

echo "[*] Updating application files in $APP_DIR ..."
rsync -a --delete \
  --exclude ".git" \
  --exclude ".github" \
  --exclude "venv" \
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
