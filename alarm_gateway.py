#!/usr/bin/env python3
"""
DiVeRa -> Polling -> Push (UnifiedPush compatible via ntfy/uppush endpoint)

This service polls the DiVeRa API for new (non-archived) alarms and sends a push
notification through either an ntfy topic or a generic UnifiedPush endpoint URL.
This works without Google Play Services/FCM.

Config is done via environment variables (see .env.example). In the recommended
systemd setup, env vars are stored in /etc/alarm-gateway/alarm-gateway.env.
"""

import hashlib
import argparse
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
DIVERA_ACCESSKEY = env("DIVERA_ACCESSKEY", "")

POLL_SECONDS = int(env("POLL_SECONDS", "20"))
STATE_FILE = env("STATE_FILE", "/var/lib/alarm-gateway/state.json")

NTFY_URL = env("NTFY_URL", "").rstrip("/")
NTFY_TOPIC = env("NTFY_TOPIC", "")
NTFY_PRIORITY = env("NTFY_PRIORITY", "5")

UPPUSH_ENDPOINT = env("UPPUSH_ENDPOINT", "")
UPPUSH_AUTH_HEADER = env("UPPUSH_AUTH_HEADER", "")

REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT", "15"))
VERIFY_TLS = env("VERIFY_TLS", "true").lower() not in ("0", "false", "no")

SHELLY_UNI_URL = env("SHELLY_UNI_URL", "").rstrip("/")
SHELLY_INPUT_IDS = [
    int(x.strip())
    for x in env("SHELLY_INPUT_IDS", "0,1").split(",")
    if x.strip()
]
SHELLY_POLL_SECONDS = float(env("SHELLY_POLL_SECONDS", "1"))
SHELLY_TRIGGER_ON = env("SHELLY_TRIGGER_ON", "1").lower() not in ("0", "false", "no")
SHELLY_DEBOUNCE_SECONDS = float(env("SHELLY_DEBOUNCE_SECONDS", "10"))
SHELLY_TITLE_TEMPLATE = env("SHELLY_TITLE_TEMPLATE", "Shelly Input {input_id}")
SHELLY_MESSAGE_TEMPLATE = env(
    "SHELLY_MESSAGE_TEMPLATE",
    "Shelly Plus Uni Eingang {input_id} wurde ausgelöst.",
)

if not UPPUSH_ENDPOINT and (not NTFY_URL or not NTFY_TOPIC):
    raise SystemExit(
        "Missing push target: either set UPPUSH_ENDPOINT or set both NTFY_URL and NTFY_TOPIC"
    )


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


def _coerce_alarm_items_map(items: Any, sorting: Any = None) -> List[Dict[str, Any]]:
    if not isinstance(items, dict):
        return []

    if isinstance(sorting, list):
        alarms: List[Dict[str, Any]] = []
        for alarm_id in sorting:
            alarm = items.get(str(alarm_id))
            if isinstance(alarm, dict):
                alarms.append(alarm)
        if alarms:
            return alarms

    return [alarm for alarm in items.values() if isinstance(alarm, dict)]


def get_alarms_list(data: Any) -> List[Dict[str, Any]]:
    # DiVeRa responses can differ depending on endpoint; handle common cases.
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]

    if not isinstance(data, dict):
        return []

    # Legacy/common direct list payloads.
    for k in ("alarms", "data", "items", "result"):
        v = data.get(k)
        if isinstance(v, list):
            return [a for a in v if isinstance(a, dict)]

    # pull/all style payload: data -> alarm -> items (+ optional sorting list).
    root_data = data.get("data")
    if isinstance(root_data, dict):
        alarm_section = root_data.get("alarm")
        if isinstance(alarm_section, dict):
            alarms = _coerce_alarm_items_map(
                alarm_section.get("items"),
                alarm_section.get("sorting"),
            )
            if alarms:
                return alarms

    # Generic nested fallback if caller points directly at an alarm section.
    alarms = _coerce_alarm_items_map(data.get("items"), data.get("sorting"))
    if alarms:
        return alarms

    return []


def pick_latest_alarm(alarms: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not alarms:
        return None

    # If a sortable timestamp exists, prefer the newest alarm deterministically.
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for alarm in alarms:
        for key in ("ts_update", "ts_create", "date", "id"):
            value = alarm.get(key)
            if isinstance(value, int):
                candidates.append((value, alarm))
                break
            if isinstance(value, str) and value.isdigit():
                candidates.append((int(value), alarm))
                break

    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    # Fallback: first item in already-sorted payload.
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


def uppush_publish(title: str, message: str) -> None:
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if UPPUSH_AUTH_HEADER:
        headers["Authorization"] = UPPUSH_AUTH_HEADER
    payload = f"{title}\n{message}".encode("utf-8")
    requests.post(
        UPPUSH_ENDPOINT,
        data=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    ).raise_for_status()


def fetch_alarms() -> Any:
    if not DIVERA_ACCESSKEY:
        raise RuntimeError("DIVERA_ACCESSKEY is empty (required for DiVeRa polling)")
    r = requests.get(
        DIVERA_URL,
        params={"accesskey": DIVERA_ACCESSKEY},
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    )
    r.raise_for_status()
    return r.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test-push",
        action="store_true",
        help="Send one test push and exit",
    )
    parser.add_argument(
        "--test-alarm-json",
        default="",
        help="Raw JSON object to build the test alarm payload",
    )
    parser.add_argument("--test-title", default="")
    parser.add_argument("--test-text", default="")
    parser.add_argument("--test-address", default="")
    parser.add_argument("--test-url", default="")
    parser.add_argument("--test-id", default="")
    parser.add_argument("--test-date", default="")
    parser.add_argument(
        "--test-field",
        action="append",
        default=[],
        help="Additional alarm field in key=value syntax (repeatable)",
    )
    return parser.parse_args()


def build_test_alarm(args: argparse.Namespace) -> Dict[str, Any]:
    alarm: Dict[str, Any] = {}
    if args.test_alarm_json:
        parsed = json.loads(args.test_alarm_json)
        if not isinstance(parsed, dict):
            raise ValueError("--test-alarm-json must be a JSON object")
        alarm.update(parsed)

    direct_fields = {
        "title": args.test_title,
        "text": args.test_text,
        "address": args.test_address,
        "url": args.test_url,
        "id": args.test_id,
        "date": args.test_date,
    }
    for k, v in direct_fields.items():
        if v:
            alarm[k] = v

    for raw in args.test_field:
        if "=" not in raw:
            raise ValueError(f"Invalid --test-field '{raw}' (expected key=value)")
        k, v = raw.split("=", 1)
        if not k.strip():
            raise ValueError(f"Invalid --test-field '{raw}' (empty key)")
        alarm[k.strip()] = v

    return alarm


def publish_message(title: str, message: str) -> None:
    if UPPUSH_ENDPOINT:
        uppush_publish(title, message)
    else:
        ntfy_publish(title, message)


def run_test_push(args: argparse.Namespace) -> None:
    alarm = build_test_alarm(args)
    title, msg = format_alarm(alarm)
    publish_message(title, msg)
    print("Test push sent.")


def fetch_shelly_input_state(input_id: int) -> Optional[bool]:
    if not SHELLY_UNI_URL:
        return None
    r = requests.get(
        f"{SHELLY_UNI_URL}/rpc/Input.GetStatus",
        params={"id": input_id},
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    )
    r.raise_for_status()
    data = r.json()
    val = data.get("state")
    if isinstance(val, bool):
        return val
    return None


def handle_divera_poll(state: Dict[str, Any]) -> None:
    data = fetch_alarms()
    alarms = get_alarms_list(data)
    latest = pick_latest_alarm(alarms)
    if latest:
        fp = fingerprint(latest)
        if fp != state.get("last_fingerprint"):
            title, msg = format_alarm(latest)
            publish_message(title, msg)
            state["last_fingerprint"] = fp
            save_state(STATE_FILE, state)


def handle_shelly_poll(state: Dict[str, Any]) -> None:
    if not SHELLY_UNI_URL:
        return
    previous: Dict[str, bool] = state.setdefault("shelly_inputs", {})
    now = time.time()
    for input_id in SHELLY_INPUT_IDS:
        current = fetch_shelly_input_state(input_id)
        if current is None:
            continue

        key = str(input_id)
        old = previous.get(key)
        previous[key] = current

        if old is None:
            continue

        rising_edge = (not old) and current
        falling_edge = old and (not current)
        should_trigger = rising_edge if SHELLY_TRIGGER_ON else falling_edge
        if not should_trigger:
            continue

        last_ts = float(state.get("shelly_last_trigger_ts", 0.0))
        if now - last_ts < SHELLY_DEBOUNCE_SECONDS:
            continue

        title = SHELLY_TITLE_TEMPLATE.format(input_id=input_id, state=current)
        message = SHELLY_MESSAGE_TEMPLATE.format(input_id=input_id, state=current)
        publish_message(title, message)
        state["shelly_last_trigger_ts"] = now
        save_state(STATE_FILE, state)


def main() -> None:
    args = parse_args()
    if args.test_push:
        run_test_push(args)
        return

    state = load_state(STATE_FILE)
    next_divera = 0.0
    next_shelly = 0.0
    while True:
        try:
            mono_now = time.monotonic()
            if DIVERA_ACCESSKEY and mono_now >= next_divera:
                handle_divera_poll(state)
                next_divera = mono_now + POLL_SECONDS
            if SHELLY_UNI_URL and mono_now >= next_shelly:
                handle_shelly_poll(state)
                next_shelly = mono_now + SHELLY_POLL_SECONDS
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
