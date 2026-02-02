#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/alarm-gateway"
ENV_DIR="/etc/alarm-gateway"
STATE_DIR="/var/lib/alarm-gateway"
SERVICE_FILE="/etc/systemd/system/alarm-gateway.service"
USER_NAME="alarm-gateway"

echo "[*] Stopping + disabling service..."
systemctl stop alarm-gateway 2>/dev/null || true
systemctl disable alarm-gateway 2>/dev/null || true

echo "[*] Removing systemd unit..."
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "[*] Removing application directory..."
rm -rf "$APP_DIR"

echo "[*] Removing state directory..."
rm -rf "$STATE_DIR"

echo "[*] Removing env directory (contains secrets!)..."
rm -rf "$ENV_DIR"

echo "[*] Removing user..."
userdel "$USER_NAME" 2>/dev/null || true

echo "[✓] Uninstalled."
