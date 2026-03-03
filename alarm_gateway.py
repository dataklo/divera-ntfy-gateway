#!/usr/bin/env python3
"""
DiVeRa -> Polling -> Push (ntfy)

This service polls the DiVeRa API for new (non-archived) alarms and sends a push
notification to an ntfy topic.

Config is done via environment variables (see .env.example). In the recommended
systemd setup, env vars are stored in /etc/alarm-gateway/alarm-gateway.env.
"""

import argparse
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


DEFAULT_ENV_FILE = "/etc/alarm-gateway/alarm-gateway.env"


def load_env_file(path: str) -> None:
    if not path or not os.path.isfile(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("export "):
                    line = line[len("export "):].strip()

                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue

                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]

                os.environ.setdefault(key, value)
    except Exception:
        return


load_env_file(os.environ.get("ALARM_GATEWAY_ENV_FILE", DEFAULT_ENV_FILE))


DIVERA_URL_DEFAULT = "https://divera247.com/api/v2/alarms"
DIVERA_ACCESSKEY_PLACEHOLDER = "PASTE_YOUR_DIVERA_ACCESSKEY_HERE"


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        raise SystemExit(f"Missing required environment variable: {name}")
    return str(val) if val is not None else ""


def _is_placeholder_secret(value: str, placeholder: str) -> bool:
    normalized = value.strip().strip('"').strip("'")
    if not normalized:
        return False
    return normalized.upper() == placeholder.upper()


DIVERA_URL = env("DIVERA_URL", DIVERA_URL_DEFAULT)
DIVERA_FALLBACK_URL = env("DIVERA_FALLBACK_URL", "https://app.divera247.com/api/v2/pull/all")
_raw_divera_accesskey = env("DIVERA_ACCESSKEY", "")
DIVERA_ACCESSKEY = "" if _is_placeholder_secret(_raw_divera_accesskey, DIVERA_ACCESSKEY_PLACEHOLDER) else _raw_divera_accesskey

POLL_SECONDS = int(env("POLL_SECONDS", "20"))
STATE_FILE = env("STATE_FILE", "/var/lib/alarm-gateway/state.json")

NTFY_URL = env("NTFY_URL", "").rstrip("/")
NTFY_TOPIC = env("NTFY_TOPIC", "")
NTFY_PRIORITY = env("NTFY_PRIORITY", "5")
NTFY_STANDARD_PRIORITY = env("NTFY_STANDARD_PRIORITY", "3")
NTFY_PRIORITY_KEYWORDS = env("NTFY_PRIORITY_KEYWORDS", "")
NTFY_AUTH_TOKEN = env("NTFY_AUTH_TOKEN", "")

REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT", "15"))
VERIFY_TLS = env("VERIFY_TLS", "true").lower() not in ("0", "false", "no")
DEBUG_DIVERA = env("DEBUG_DIVERA", "false").lower() in ("1", "true", "yes", "on")

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
SHELLY_INPUT_EVENTS = env("SHELLY_INPUT_EVENTS", "")
SHELLY_OUTPUT_LEVELS = env("SHELLY_OUTPUT_LEVELS", "")

WEBHOOK_ENABLED = env("WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes", "on")
WEBHOOK_BIND = env("WEBHOOK_BIND", "0.0.0.0")
WEBHOOK_PORT = int(env("WEBHOOK_PORT", "8080"))
WEBHOOK_PATH = env("WEBHOOK_PATH", "/webhook/alarm")
WEBHOOK_TOKEN = env("WEBHOOK_TOKEN", "")
WEBHOOK_HEALTH_PATH = env("WEBHOOK_HEALTH_PATH", "/healthz")

STATE_LOCK = threading.RLock()  # reentrant: some locked paths update metrics
RUNTIME_METRICS: Dict[str, int] = {
    "divera_poll_ok": 0,
    "divera_poll_error": 0,
    "push_sent": 0,
    "webhook_requests": 0,
    "webhook_success": 0,
    "webhook_error": 0,
    "shelly_output_switches": 0,
}


def debug_log(message: str) -> None:
    if DEBUG_DIVERA:
        print(f"[debug] {message}")


def parse_priority_keyword_map(raw_value: str) -> List[Tuple[str, str]]:
    """
    Parse keyword/priority pairs from env var format:
    "keyword=priority,other keyword=priority".
    """
    entries: List[Tuple[str, str]] = []
    if not raw_value.strip():
        return entries

    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        keyword, priority = item.split("=", 1)
        normalized_keyword = keyword.strip().casefold()
        normalized_priority = priority.strip()
        if normalized_keyword and normalized_priority:
            entries.append((normalized_keyword, normalized_priority))

    return entries


PRIORITY_KEYWORD_MAP = parse_priority_keyword_map(NTFY_PRIORITY_KEYWORDS)


def parse_input_event_map(raw_value: str) -> Dict[int, Tuple[str, str]]:
    """Parse input-specific title/message map from 'id=title|message,id=title|message'."""
    mapped: Dict[int, Tuple[str, str]] = {}
    if not raw_value.strip():
        return mapped

    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item or "=" not in item or "|" not in item:
            continue

        input_id_raw, rest = item.split("=", 1)
        title_raw, message_raw = rest.split("|", 1)
        try:
            input_id = int(input_id_raw.strip())
        except ValueError:
            continue

        title = title_raw.strip()
        message = message_raw.strip()
        if title and message:
            mapped[input_id] = (title, message)

    return mapped


def parse_output_levels_map(raw_value: str) -> Dict[int, Set[int]]:
    """Parse output level map from 'output=1|2|3,output=4|5'."""
    mapped: Dict[int, Set[int]] = {}
    if not raw_value.strip():
        return mapped

    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue

        output_id_raw, levels_raw = item.split("=", 1)
        try:
            output_id = int(output_id_raw.strip())
        except ValueError:
            continue

        levels: Set[int] = set()
        normalized_levels = levels_raw.replace(";", "|").replace(":", "|")
        for token in normalized_levels.split("|"):
            level_text = token.strip()
            if not level_text:
                continue
            try:
                levels.add(int(level_text))
            except ValueError:
                continue

        if levels:
            mapped[output_id] = levels

    return mapped


SHELLY_INPUT_EVENT_MAP = parse_input_event_map(SHELLY_INPUT_EVENTS)
SHELLY_OUTPUT_LEVEL_MAP = parse_output_levels_map(SHELLY_OUTPUT_LEVELS)


def _priority_rank(priority: str) -> int:
    try:
        return int(priority.strip())
    except (TypeError, ValueError, AttributeError):
        return -1


def resolve_ntfy_priority(title: str) -> str:
    """Resolve only the ntfy Priority header based on title keyword matches.

    Matching is case-insensitive and substring-based, e.g. "MANV-Alles" matches "manv".
    """
    normalized_title = title.casefold()
    matched_priorities: List[str] = []

    for keyword, priority in PRIORITY_KEYWORD_MAP:
        if keyword in normalized_title:
            matched_priorities.append(priority)

    if matched_priorities:
        return max(matched_priorities, key=_priority_rank)

    # Backward-compatible fallback for existing setups.
    if "probealarm" in normalized_title:
        return NTFY_STANDARD_PRIORITY

    return NTFY_PRIORITY


def _looks_like_https(url: str) -> bool:
    return url.lower().startswith("https://")


def validate_runtime_config() -> None:
    warnings: List[str] = []

    if WEBHOOK_ENABLED and not WEBHOOK_TOKEN:
        warnings.append("WEBHOOK_ENABLED=true but WEBHOOK_TOKEN is empty")

    if WEBHOOK_ENABLED and WEBHOOK_PATH == WEBHOOK_HEALTH_PATH:
        raise SystemExit("WEBHOOK_PATH and WEBHOOK_HEALTH_PATH must be different")

    if WEBHOOK_ENABLED and not WEBHOOK_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_PATH must start with '/'")

    if WEBHOOK_ENABLED and not WEBHOOK_HEALTH_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_HEALTH_PATH must start with '/'")

    if NTFY_URL and not _looks_like_https(NTFY_URL):
        warnings.append("NTFY_URL is not https")

    if DIVERA_URL and not _looks_like_https(DIVERA_URL):
        warnings.append("DIVERA_URL is not https")

    if DIVERA_FALLBACK_URL and not _looks_like_https(DIVERA_FALLBACK_URL):
        warnings.append("DIVERA_FALLBACK_URL is not https")

    if not VERIFY_TLS:
        warnings.append("VERIFY_TLS is disabled")

    for warning in warnings:
        print(f"WARNING: {warning}")


def metric_inc(name: str, amount: int = 1) -> None:
    with STATE_LOCK:
        RUNTIME_METRICS[name] = int(RUNTIME_METRICS.get(name, 0)) + amount


def metrics_snapshot() -> Dict[str, int]:
    with STATE_LOCK:
        return dict(RUNTIME_METRICS)


def validate_push_target() -> None:
    if not NTFY_URL or not NTFY_TOPIC:
        raise SystemExit("Missing push target: set both NTFY_URL and NTFY_TOPIC")


def load_state(path: str) -> Dict[str, Any]:
    default_state = {
        "last_fingerprint": None,
        "active_fingerprints": [],
        "recent_fingerprints": [],
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                default_state.update(loaded)
            return default_state
    except FileNotFoundError:
        return default_state
    except Exception:
        return default_state


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
            v = _get_case_insensitive(alarm, k)
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def alarm_id_value(alarm: Dict[str, Any]) -> str:
    return safe_get(alarm, ["id", "alarm_id", "alarmId"])


def _with_alarm_id_from_key(alarm_id: Any, alarm: Dict[str, Any]) -> Dict[str, Any]:
    if alarm_id_value(alarm):
        return alarm

    normalized_id = str(alarm_id).strip()
    if not normalized_id:
        return alarm

    alarm_with_id = dict(alarm)
    alarm_with_id["id"] = normalized_id
    alarm_with_id.setdefault("alarm_id", normalized_id)
    return alarm_with_id


def _looks_like_alarm_entry(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    marker_keys = {
        "id", "alarm_id", "alarmid", "title", "text", "date", "ts_create",
        "ts_update", "address", "closed", "deleted", "stichwort", "einsatzstichwort",
        "adresse", "ort", "status",
    }
    normalized_keys = {str(k).lower() for k in value.keys()}
    return any(key in normalized_keys for key in marker_keys)


def _coerce_alarm_items_map(items: Any, sorting: Any = None) -> List[Dict[str, Any]]:
    if not isinstance(items, dict):
        return []

    if isinstance(sorting, list):
        alarms: List[Dict[str, Any]] = []
        for alarm_id in sorting:
            alarm = items.get(str(alarm_id))
            if _looks_like_alarm_entry(alarm):
                alarms.append(_with_alarm_id_from_key(alarm_id, alarm))
        if alarms:
            return alarms

    alarms_from_items: List[Dict[str, Any]] = []
    for alarm_id, alarm in items.items():
        if _looks_like_alarm_entry(alarm):
            alarms_from_items.append(_with_alarm_id_from_key(alarm_id, alarm))
    return alarms_from_items


def _coerce_alarm_collection(value: Any, sorting: Any = None) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [alarm for alarm in value if _looks_like_alarm_entry(alarm)]
    if isinstance(value, dict):
        return _coerce_alarm_items_map(value, sorting)
    return []


def _get_case_insensitive(mapping: Dict[str, Any], key: str) -> Any:
    for existing_key, value in mapping.items():
        if isinstance(existing_key, str) and existing_key.lower() == key.lower():
            return value
    return None


def _alarms_from_alarm_section(section: Any) -> List[Dict[str, Any]]:
    if isinstance(section, list):
        return _coerce_alarm_collection(section)

    if not isinstance(section, dict):
        return []

    items = _get_case_insensitive(section, "items")
    if items is not None:
        sorting = _get_case_insensitive(section, "sorting")
        return _coerce_alarm_collection(items, sorting)

    return _coerce_alarm_collection(section)


def _collect_alarms_deep(value: Any, seen: Optional[Set[int]] = None) -> List[Dict[str, Any]]:
    if seen is None:
        seen = set()

    if isinstance(value, dict):
        value_id = id(value)
        if value_id in seen:
            return []
        seen.add(value_id)

        collected: List[Dict[str, Any]] = []

        direct = _coerce_alarm_collection(value)
        if direct:
            collected.extend(direct)

        alarm_section = _get_case_insensitive(value, "alarm")
        if alarm_section is not None:
            collected.extend(_alarms_from_alarm_section(alarm_section))

        items = _get_case_insensitive(value, "items")
        if items is not None:
            sorting = _get_case_insensitive(value, "sorting")
            collected.extend(_coerce_alarm_items_map(items, sorting))

        for nested in value.values():
            collected.extend(_collect_alarms_deep(nested, seen))

        return collected

    if isinstance(value, list):
        collected: List[Dict[str, Any]] = []
        for item in value:
            collected.extend(_collect_alarms_deep(item, seen))
        return collected

    return []


def get_alarms_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [a for a in data if _looks_like_alarm_entry(a)]

    if not isinstance(data, dict):
        return []

    for key in ("alarms", "result"):
        alarms = _coerce_alarm_collection(_get_case_insensitive(data, key))
        if alarms:
            return alarms

    root_data = _get_case_insensitive(data, "data")
    if isinstance(root_data, list):
        alarms = _coerce_alarm_collection(root_data)
        if alarms:
            return alarms
    elif isinstance(root_data, dict):
        alarms = _alarms_from_alarm_section(_get_case_insensitive(root_data, "alarm"))
        if alarms:
            return alarms

    alarms = _alarms_from_alarm_section(_get_case_insensitive(data, "alarm"))
    if alarms:
        return alarms

    alarms = _coerce_alarm_items_map(
        _get_case_insensitive(data, "items"),
        _get_case_insensitive(data, "sorting"),
    )
    if alarms:
        return alarms

    alarms = _collect_alarms_deep(data)
    if alarms:
        deduplicated: Dict[str, Dict[str, Any]] = {}
        for alarm in alarms:
            deduplicated[fingerprint(alarm)] = alarm
        return list(deduplicated.values())

    return []


def _parse_sort_value(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _alarm_sort_key(alarm: Dict[str, Any], fallback_index: int) -> Tuple[int, int]:
    for key in ("ts_update", "ts_create", "date", "time", "created_at", "createdAt", "id"):
        parsed = _parse_sort_value(safe_get(alarm, [key]))
        if parsed is not None:
            return (1, parsed)
    return (0, fallback_index)


def pick_latest_alarm(alarms: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not alarms:
        return None
    keyed = [(_alarm_sort_key(a, idx), a) for idx, a in enumerate(alarms)]
    return max(keyed, key=lambda x: x[0])[1]


def sort_alarms_oldest_first(alarms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keyed = [(_alarm_sort_key(a, idx), a) for idx, a in enumerate(alarms)]
    keyed.sort(key=lambda x: x[0])
    return [a for _, a in keyed]


def fingerprint(alarm: Dict[str, Any]) -> str:
    parts = [
        alarm_id_value(alarm),
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
    alarm_id = alarm_id_value(alarm)
    text = safe_get(alarm, ["text", "info", "description", "beschreibung", "note"])
    address = safe_get(alarm, ["address", "adresse", "ort", "location"])

    lines: List[str] = []
    if alarm_id:
        lines.append(f"Alarmnummer: {alarm_id}")
    if text:
        lines.append(f"Text: {text}")
    if address:
        lines.append(f"Adresse: {address}")

    link = safe_get(alarm, ["url", "link", "alarm_url"])
    if link:
        lines.append(link)

    if not lines:
        lines.append("Neue Alarmierung eingegangen.")

    return title, "\n".join(lines)


def ntfy_publish(title: str, message: str) -> None:
    # Keep title/message payload unchanged; only Priority header is derived from title keywords.
    priority = resolve_ntfy_priority(title)
    headers = {"Title": title, "Priority": priority}
    if NTFY_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_AUTH_TOKEN}"
    requests.post(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    ).raise_for_status()


def fetch_alarms() -> Any:
    if not DIVERA_ACCESSKEY:
        raise RuntimeError(
            "DIVERA_ACCESSKEY is empty or still set to template placeholder "
            f"('{DIVERA_ACCESSKEY_PLACEHOLDER}')."
        )

    urls = [DIVERA_URL]
    if DIVERA_FALLBACK_URL and DIVERA_FALLBACK_URL not in urls:
        urls.append(DIVERA_FALLBACK_URL)

    errors: List[str] = []
    for url in urls:
        try:
            r = requests.get(
                url,
                params={"accesskey": DIVERA_ACCESSKEY},
                timeout=REQUEST_TIMEOUT,
                verify=VERIFY_TLS,
            )
            r.raise_for_status()
            payload = r.json()
            debug_log(f"DiVeRa API OK via {url}; top-level type={type(payload).__name__}")
            return payload
        except Exception as e:
            errors.append(f"{url}: {e}")

    raise RuntimeError("DiVeRa API request failed on all configured URLs: " + " | ".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-divera-alarm", action="store_true", help="Query DiVeRa once, print whether alarms are present, then exit")
    parser.add_argument("--check-json", action="store_true", help="With --check-divera-alarm: output matching alarm details as JSON")
    parser.add_argument("--test-push", action="store_true", help="Send one test push and exit")
    parser.add_argument("--test-alarm-json", default="", help="Raw JSON object to build the test alarm payload")
    parser.add_argument("--test-title", default="")
    parser.add_argument("--test-text", default="")
    parser.add_argument("--test-address", default="")
    parser.add_argument("--test-url", default="")
    parser.add_argument("--test-id", default="")
    parser.add_argument("--test-date", default="")
    parser.add_argument("--test-field", action="append", default=[], help="Additional alarm field in key=value syntax (repeatable)")
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
    ntfy_publish(title, message)
    metric_inc("push_sent")


def run_test_push(args: argparse.Namespace) -> None:
    validate_push_target()
    alarm = build_test_alarm(args)
    title, msg = format_alarm(alarm)
    publish_message(title, msg)
    print("Test push sent.")


def run_divera_alarm_check(output_json: bool) -> int:
    data = fetch_alarms()
    alarms = get_alarms_list(data)
    latest = pick_latest_alarm(alarms)

    if not latest:
        if isinstance(data, dict):
            debug_log("Keine Alarme erkannt; Top-Level-Keys: " + ", ".join([str(k) for k in list(data.keys())[:20]]))
        elif isinstance(data, list):
            debug_log(f"Keine Alarme erkannt; API lieferte Liste mit {len(data)} Elementen")
        print("DiVeRa check: kein aktiver Alarm gefunden.")
        return 1

    title, msg = format_alarm(latest)
    print("DiVeRa check: aktiver Alarm gefunden.")
    print(f"Titel: {title}")
    print(f"Details: {msg}")
    if output_json:
        print(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


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


def set_shelly_output_state(output_id: int, is_on: bool) -> None:
    if not SHELLY_UNI_URL:
        return

    r = requests.get(
        f"{SHELLY_UNI_URL}/rpc/Switch.Set",
        params={"id": output_id, "on": str(is_on).lower()},
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    )
    r.raise_for_status()


def alarm_level_value(alarm: Dict[str, Any]) -> Optional[int]:
    raw = safe_get(alarm, ["priority", "prio", "alarm_level", "alarmlevel", "level"])
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def highest_alarm_level(alarms: List[Dict[str, Any]]) -> Optional[int]:
    levels = [lvl for lvl in (alarm_level_value(alarm) for alarm in alarms) if lvl is not None]
    if not levels:
        return None
    return max(levels)


def apply_shelly_output_levels(alarms: List[Dict[str, Any]], state: Dict[str, Any]) -> None:
    if not SHELLY_OUTPUT_LEVEL_MAP:
        return

    current_level = highest_alarm_level(alarms)
    previous: Dict[str, bool] = state.setdefault("shelly_outputs", {})
    changed = False

    for output_id, active_levels in SHELLY_OUTPUT_LEVEL_MAP.items():
        should_be_on = current_level in active_levels if current_level is not None else False
        key = str(output_id)
        if previous.get(key) == should_be_on:
            continue

        set_shelly_output_state(output_id, should_be_on)
        previous[key] = should_be_on
        metric_inc("shelly_output_switches")
        changed = True

    if changed:
        save_state(STATE_FILE, state)


def build_alarm_from_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("Missing required field: title")

    alarm: Dict[str, Any] = {
        "title": title,
        "text": str(payload.get("text", "")).strip(),
    }

    address = str(payload.get("address", "")).strip()
    if address:
        alarm["address"] = address

    alarm_level = payload.get("alarm_level", payload.get("level", payload.get("priority", payload.get("prio"))))
    if alarm_level is not None and str(alarm_level).strip() != "":
        try:
            parsed_level = int(str(alarm_level).strip())
            alarm["alarm_level"] = str(parsed_level)
        except ValueError:
            raise ValueError("alarm_level must be numeric")

    return alarm


def handle_webhook_alarm(payload: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    alarm = build_alarm_from_webhook_payload(payload)
    title, msg = format_alarm(alarm)
    publish_message(title, msg)

    # Optional: drive outputs from webhook-provided alarm_level as well.
    with STATE_LOCK:
        apply_shelly_output_levels([alarm], state)
        save_state(STATE_FILE, state)

    metric_inc("webhook_success")
    return {
        "status": "ok",
        "title": title,
        "has_alarm_level": alarm_level_value(alarm) is not None,
    }


def make_webhook_handler(state: Dict[str, Any]):
    class WebhookHandler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != WEBHOOK_HEALTH_PATH:
                self._send_json(404, {"error": "not found"})
                return

            self._send_json(
                200,
                {
                    "status": "ok",
                    "webhook_enabled": WEBHOOK_ENABLED,
                    "metrics": metrics_snapshot(),
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if self.path != WEBHOOK_PATH:
                self._send_json(404, {"error": "not found"})
                return

            metric_inc("webhook_requests")

            if WEBHOOK_TOKEN:
                auth = self.headers.get("Authorization", "")
                expected = f"Bearer {WEBHOOK_TOKEN}"
                if auth.strip() != expected:
                    metric_inc("webhook_error")
                    self._send_json(401, {"error": "unauthorized"})
                    return

            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
                if not isinstance(payload, dict):
                    raise ValueError("JSON must be an object")
                result = handle_webhook_alarm(payload, state)
                self._send_json(200, result)
            except Exception as exc:
                metric_inc("webhook_error")
                self._send_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            debug_log(f"webhook: {format % args}")

    return WebhookHandler


def start_webhook_server(state: Dict[str, Any]) -> Optional[ThreadingHTTPServer]:
    if not WEBHOOK_ENABLED:
        return None

    handler = make_webhook_handler(state)
    server = ThreadingHTTPServer((WEBHOOK_BIND, WEBHOOK_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Webhook listening on http://{WEBHOOK_BIND}:{WEBHOOK_PORT}{WEBHOOK_PATH}")
    print(f"Health endpoint: http://{WEBHOOK_BIND}:{WEBHOOK_PORT}{WEBHOOK_HEALTH_PATH}")
    return server


def handle_divera_poll(state: Dict[str, Any]) -> None:
    data = fetch_alarms()
    alarms = sort_alarms_oldest_first(get_alarms_list(data))
    debug_log(f"DiVeRa Poll: {len(alarms)} Alarm(e) erkannt")

    with STATE_LOCK:
        prev_active = set(state.get("active_fingerprints", []))
        recent = [x for x in state.get("recent_fingerprints", []) if isinstance(x, str)]
    recent_set = set(recent)

    current_fingerprints: List[str] = []
    any_sent = False

    for alarm in alarms:
        fp = fingerprint(alarm)
        current_fingerprints.append(fp)

        if fp in prev_active or fp in recent_set:
            continue

        title, msg = format_alarm(alarm)
        publish_message(title, msg)
        any_sent = True
        recent.append(fp)
        recent_set.add(fp)

    with STATE_LOCK:
        state["active_fingerprints"] = list(dict.fromkeys(current_fingerprints))
        state["recent_fingerprints"] = recent[-500:]

        latest = pick_latest_alarm(alarms)
        state["last_fingerprint"] = fingerprint(latest) if latest else None

        apply_shelly_output_levels(alarms, state)
        save_state(STATE_FILE, state)


def handle_shelly_poll(state: Dict[str, Any]) -> None:
    if not SHELLY_UNI_URL:
        return
    with STATE_LOCK:
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

        with STATE_LOCK:
            last_ts = float(state.get("shelly_last_trigger_ts", 0.0))
        if now - last_ts < SHELLY_DEBOUNCE_SECONDS:
            continue

        title_template, message_template = SHELLY_INPUT_EVENT_MAP.get(
            input_id,
            (SHELLY_TITLE_TEMPLATE, SHELLY_MESSAGE_TEMPLATE),
        )
        title = title_template.format(input_id=input_id, state=current)
        message = message_template.format(input_id=input_id, state=current)
        publish_message(title, message)
        with STATE_LOCK:
            state["shelly_last_trigger_ts"] = now
            save_state(STATE_FILE, state)


def main() -> None:
    args = parse_args()
    if args.check_divera_alarm:
        raise SystemExit(run_divera_alarm_check(args.check_json))
    if args.test_push:
        run_test_push(args)
        return

    validate_push_target()
    validate_runtime_config()
    state = load_state(STATE_FILE)
    webhook_server = start_webhook_server(state)
    next_divera = 0.0
    next_shelly = 0.0
    while True:
        try:
            mono_now = time.monotonic()
            if DIVERA_ACCESSKEY and mono_now >= next_divera:
                handle_divera_poll(state)
                metric_inc("divera_poll_ok")
                next_divera = mono_now + POLL_SECONDS
            if SHELLY_UNI_URL and mono_now >= next_shelly:
                handle_shelly_poll(state)
                next_shelly = mono_now + SHELLY_POLL_SECONDS
        except Exception as e:
            metric_inc("divera_poll_error")
            print(f"ERROR: {e}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
