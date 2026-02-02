#!/usr/bin/env python3
"""
DiVeRa -> Polling -> Push (UnifiedPush compatible via ntfy)

This service polls the DiVeRa API for new (non-archived) alarms and sends a push
notification through an ntfy topic. This works without Google Play Services/FCM.

Config is done via environment variables (see .env.example). In the recommended
systemd setup, env vars are stored in /etc/alarm-gateway/alarm-gateway.env.
"""

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


DIVERA_URL_DEFAULT = "https://divera247.com/api/v2/alarms"

def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        raise SystemExit(f"Missing required environment variable: {name}")
    return str(val) if val is not None else ""


DIVERA_URL = env("DIVERA_URL", DIVERA_URL_DEFAULT)
DIVERA_ACCESSKEY = env("DIVERA_ACCESSKEY", required=True)

POLL_SECONDS = int(env("POLL_SECONDS", "20"))
STATE_FILE = env("STATE_FILE", "/var/lib/alarm-gateway/state.json")

NTFY_URL = env("NTFY_URL", required=True).rstrip("/")
NTFY_TOPIC = env("NTFY_TOPIC", required=True)
NTFY_PRIORITY = env("NTFY_PRIORITY", "5")

REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT", "15"))
VERIFY_TLS = env("VERIFY_TLS", "true").lower() not in ("0", "false", "no")


def load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_fingerprint": None}
    except Exception:
        # corrupted state file -> start fresh
        return {"last_fingerprint": None}


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def safe_get(alarm: Dict[str, Any], keys: List[str]) -> str:
    for k in keys:
        v = alarm.get(k)
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def get_alarms_list(data: Any) -> List[Dict[str, Any]]:
    # DiVeRa responses can differ depending on configuration; handle common cases.
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    if isinstance(data, dict):
        for k in ("alarms", "data", "items", "result"):
            v = data.get(k)
            if isinstance(v, list):
                return [a for a in v if isinstance(a, dict)]
    return []


def pick_latest_alarm(alarms: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not alarms:
        return None
    # In most cases: newest first
    return alarms[0]


def fingerprint(alarm: Dict[str, Any]) -> str:
    parts = [
        safe_get(alarm, ["id", "alarm_id", "alarmId"]),
        safe_get(alarm, ["title", "stichwort", "keyword", "einsatzstichwort"]),
        safe_get(alarm, ["address", "adresse", "ort", "location"]),
        safe_get(alarm, ["date", "datetime", "time", "created_at", "createdAt"]),
    ]
    raw = "|".join([p for p in parts if p])
    if not raw:
        raw = json.dumps(alarm, sort_keys=True)[:1000]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_alarm(alarm: Dict[str, Any]) -> Tuple[str, str]:
    title = safe_get(alarm, ["title", "stichwort", "keyword", "einsatzstichwort"]) or "DiVeRa Alarm"
    lines: List[str] = []

    addr = safe_get(alarm, ["address", "adresse", "ort", "location"])
    if addr:
        lines.append(f"📍 {addr}")

    info = safe_get(alarm, ["text", "info", "description", "beschreibung", "note"])
    if info:
        lines.append(info)

    link = safe_get(alarm, ["url", "link", "alarm_url"])
    if link:
        lines.append(link)

    if not lines:
        lines.append("Neue Alarmierung eingegangen.")
    return title, "\n".join(lines)


def ntfy_publish(title: str, message: str) -> None:
    # ntfy supports Title/Priority headers and can be used as UnifiedPush distributor.
    requests.post(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": NTFY_PRIORITY},
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    ).raise_for_status()


def fetch_alarms() -> Any:
    r = requests.get(
        DIVERA_URL,
        params={"accesskey": DIVERA_ACCESSKEY},
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    )
    r.raise_for_status()
    return r.json()


def main() -> None:
    state = load_state(STATE_FILE)
    while True:
        try:
            data = fetch_alarms()
            alarms = get_alarms_list(data)
            latest = pick_latest_alarm(alarms)
            if latest:
                fp = fingerprint(latest)
                if fp != state.get("last_fingerprint"):
                    title, msg = format_alarm(latest)
                    ntfy_publish(title, msg)
                    state["last_fingerprint"] = fp
                    save_state(STATE_FILE, state)
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
