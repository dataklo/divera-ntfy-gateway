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
import hmac
import json
import logging
import os
import random
import shlex
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


DEFAULT_ENV_FILE = "/etc/alarm-gateway/alarm-gateway.env"



LOGGER = logging.getLogger("alarm_gateway")
ENV_DEFINITIONS: List[Dict[str, Any]] = []


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


configure_logging()


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
    except Exception as exc:
        LOGGER.warning("Failed to load env file '%s': %s", path, exc)
        return


load_env_file(os.environ.get("ALARM_GATEWAY_ENV_FILE", DEFAULT_ENV_FILE))


DIVERA_URL_DEFAULT = "https://www.divera247.com/api/v2/alarms?accesskey=<API-Key>"
DIVERA_FALLBACK_URL_DEFAULT = "https://divera247.com/api/v2/alarms?accesskey=<API-Key>"
DIVERA_ACCESSKEY_PLACEHOLDER = "PASTE_YOUR_DIVERA_ACCESSKEY_HERE"


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    if not any(item.get("name") == name for item in ENV_DEFINITIONS):
        ENV_DEFINITIONS.append({"name": name, "default": default, "required": required})
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
DIVERA_FALLBACK_URL = env("DIVERA_FALLBACK_URL", DIVERA_FALLBACK_URL_DEFAULT)
_raw_divera_accesskey = env("DIVERA_ACCESSKEY", "")
DIVERA_ACCESSKEY = "" if _is_placeholder_secret(_raw_divera_accesskey, DIVERA_ACCESSKEY_PLACEHOLDER) else _raw_divera_accesskey

POLL_SECONDS = int(env("POLL_SECONDS", env("POLL_INTERVAL_SECONDS", "20")))
STATE_FILE = env("STATE_FILE", "/var/lib/alarm-gateway/state.json")

NTFY_URL = env("NTFY_URL", "").rstrip("/")
NTFY_TOPIC = env("NTFY_TOPIC", "")
NTFY_PRIORITY = env("NTFY_PRIORITY", "5")
NTFY_DEFAULT_PRIORITY = env("NTFY_DEFAULT_PRIORITY", NTFY_PRIORITY)
NTFY_PRIORITY_KEYWORDS = env("NTFY_PRIORITY_KEYWORDS", "")
NTFY_AUTH_TOKEN = env("NTFY_AUTH_TOKEN", "")
NTFY_FALLBACK_URLS = env("NTFY_FALLBACK_URLS", "")
NTFY_RETRY_ATTEMPTS = int(env("NTFY_RETRY_ATTEMPTS", "2"))
NTFY_RETRY_DELAY_SECONDS = float(env("NTFY_RETRY_DELAY_SECONDS", "1.5"))
NTFY_RETRY_JITTER_SECONDS = float(env("NTFY_RETRY_JITTER_SECONDS", "0.0"))

REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT", "15"))
VERIFY_TLS = env("VERIFY_TLS", "true").lower() not in ("0", "false", "no")
DEBUG_DIVERA = env("DEBUG_DIVERA", "false").lower() in ("1", "true", "yes", "on")


WEBHOOK_ENABLED = env("WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes", "on")
WEBHOOK_BIND = env("WEBHOOK_BIND", "0.0.0.0")
WEBHOOK_PORT = int(env("WEBHOOK_PORT", "8080"))
WEBHOOK_PATH = env("WEBHOOK_PATH", "/webhook/alarm")
WEBHOOK_TOKEN = env("WEBHOOK_TOKEN", "")
WEBHOOK_UI_PATH = env("WEBHOOK_UI_PATH", "/")
WEBHOOK_CONFIG_PATH = env("WEBHOOK_CONFIG_PATH", "/admin/config")
WEBHOOK_UPDATE_PATH = env("WEBHOOK_UPDATE_PATH", "/admin/update")
WEBHOOK_TRIGGER_PATH = env("WEBHOOK_TRIGGER_PATH", "/webhook/trigger")
WEBHOOK_REPLAY_PROTECTION = env("WEBHOOK_REPLAY_PROTECTION", "false").lower() in ("1", "true", "yes", "on")
WEBHOOK_MAX_SKEW_SECONDS = int(env("WEBHOOK_MAX_SKEW_SECONDS", "120"))
WEBHOOK_HMAC_SECRET = env("WEBHOOK_HMAC_SECRET", "")

HEALTH_ENABLED = env("HEALTH_ENABLED", "true").lower() in ("1", "true", "yes", "on")
HEALTH_BIND = env("HEALTH_BIND", "0.0.0.0")
HEALTH_PORT = int(env("HEALTH_PORT", "8081"))
HEALTH_PATH = env("HEALTH_PATH", env("WEBHOOK_HEALTH_PATH", "/healthz"))
HEALTH_METRICS_PATH = env("HEALTH_METRICS_PATH", "/metrics")

NODE_ID = env("NODE_ID", os.uname().nodename)
NODE_PRIORITY = int(env("NODE_PRIORITY", "100"))
PEER_NODES = env("PEER_NODES", "")
CLUSTER_PING_TIMEOUT = float(env("CLUSTER_PING_TIMEOUT", "2"))
CLUSTER_STATUS_TTL_SECONDS = float(env("CLUSTER_STATUS_TTL_SECONDS", "5"))
CLUSTER_SHARED_TOKEN = env("CLUSTER_SHARED_TOKEN", "")

AUDIT_LOG_FILE = env("AUDIT_LOG_FILE", "")
UPDATE_COMMAND = env("UPDATE_COMMAND", "")
DEDUP_RETENTION_HOURS = float(env("DEDUP_RETENTION_HOURS", "48"))

STATE_LOCK = threading.RLock()  # reentrant: some locked paths update metrics
RUNTIME_METRICS: Dict[str, int] = {
    "divera_poll_ok": 0,
    "divera_poll_error": 0,
    "push_sent": 0,
    "webhook_requests": 0,
    "webhook_success": 0,
    "webhook_error": 0,
    "cluster_standby_skip": 0,
}


def debug_log(message: str) -> None:
    if DEBUG_DIVERA:
        LOGGER.debug(message)


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


def parse_csv_list(raw_value: str) -> List[str]:
    return [x.strip() for x in raw_value.split(",") if x.strip()]


def _priority_rank(priority: str) -> int:
    try:
        return int(priority.strip())
    except (TypeError, ValueError, AttributeError):
        return -1


def _parse_alarm_level(raw: Any) -> Optional[int]:
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return None

    if 1 <= parsed <= 5:
        return parsed
    return None


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

    return NTFY_DEFAULT_PRIORITY



_CLUSTER_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "leader_id": NODE_ID,
    "leader_priority": NODE_PRIORITY,
    "reachable": [],
}


def _normalize_peer_health_url(raw_peer: str) -> str:
    peer = raw_peer.strip()
    if not peer:
        return ""

    if "://" not in peer:
        peer = f"http://{peer}"

    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(peer)
    path = parsed.path or HEALTH_PATH
    return urlunsplit((parsed.scheme or "http", parsed.netloc, path, "", ""))


def _fetch_peer_node_status(health_url: str) -> Optional[Dict[str, Any]]:
    try:
        headers: Dict[str, str] = {}
        if CLUSTER_SHARED_TOKEN:
            headers["X-Cluster-Token"] = CLUSTER_SHARED_TOKEN
        r = requests.get(health_url, timeout=CLUSTER_PING_TIMEOUT, verify=VERIFY_TLS, headers=headers)
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            return None
        node_id = str(payload.get("node_id", "")).strip()
        priority_raw = payload.get("node_priority")
        try:
            node_priority = int(str(priority_raw).strip())
        except Exception:
            return None
        if not node_id:
            return None
        return {"node_id": node_id, "node_priority": node_priority, "url": health_url}
    except Exception:
        return None


def resolve_cluster_status(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.monotonic()
    if not force_refresh and now - float(_CLUSTER_CACHE.get("ts", 0.0)) < CLUSTER_STATUS_TTL_SECONDS:
        return dict(_CLUSTER_CACHE)

    candidates: List[Dict[str, Any]] = [{"node_id": NODE_ID, "node_priority": NODE_PRIORITY, "url": "self"}]

    for peer in parse_csv_list(PEER_NODES):
        health_url = _normalize_peer_health_url(peer)
        if not health_url:
            continue
        status = _fetch_peer_node_status(health_url)
        if status is not None:
            candidates.append(status)

    leader = max(candidates, key=lambda x: (int(x["node_priority"]), str(x["node_id"])))
    _CLUSTER_CACHE.update(
        {
            "ts": now,
            "leader_id": str(leader["node_id"]),
            "leader_priority": int(leader["node_priority"]),
            "reachable": [c["node_id"] for c in candidates],
        }
    )
    return dict(_CLUSTER_CACHE)


def is_active_sender() -> bool:
    status = resolve_cluster_status()
    return str(status.get("leader_id", "")) == NODE_ID


def _looks_like_https(url: str) -> bool:
    return url.lower().startswith("https://")


def validate_runtime_config() -> None:
    warnings: Set[str] = set()

    if WEBHOOK_ENABLED and not WEBHOOK_TOKEN:
        warnings.add("WEBHOOK_ENABLED=true but WEBHOOK_TOKEN is empty")

    if WEBHOOK_ENABLED and not WEBHOOK_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_PATH must start with '/'")

    if WEBHOOK_ENABLED and not WEBHOOK_UI_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_UI_PATH must start with '/'")

    if WEBHOOK_ENABLED and not WEBHOOK_TRIGGER_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_TRIGGER_PATH must start with '/'")

    if WEBHOOK_ENABLED and not WEBHOOK_CONFIG_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_CONFIG_PATH must start with '/'")

    if WEBHOOK_ENABLED and not WEBHOOK_UPDATE_PATH.startswith("/"):
        raise SystemExit("WEBHOOK_UPDATE_PATH must start with '/'")

    if HEALTH_ENABLED and not HEALTH_PATH.startswith("/"):
        raise SystemExit("HEALTH_PATH must start with '/'")

    if WEBHOOK_ENABLED and HEALTH_ENABLED and WEBHOOK_BIND == HEALTH_BIND and WEBHOOK_PORT == HEALTH_PORT:
        raise SystemExit("WEBHOOK_PORT and HEALTH_PORT must be different when using same bind address")

    if not (1 <= NODE_PRIORITY <= 100):
        raise SystemExit("NODE_PRIORITY must be between 1 and 100")

    if not HEALTH_METRICS_PATH.startswith("/"):
        raise SystemExit("HEALTH_METRICS_PATH must start with '/'")

    if HEALTH_PATH == HEALTH_METRICS_PATH:
        raise SystemExit("HEALTH_PATH and HEALTH_METRICS_PATH must be different")

    if WEBHOOK_REPLAY_PROTECTION and not WEBHOOK_HMAC_SECRET:
        raise SystemExit("WEBHOOK_REPLAY_PROTECTION=true requires WEBHOOK_HMAC_SECRET")

    if NTFY_RETRY_ATTEMPTS < 1:
        raise SystemExit("NTFY_RETRY_ATTEMPTS must be >= 1")

    if NTFY_RETRY_DELAY_SECONDS < 0:
        raise SystemExit("NTFY_RETRY_DELAY_SECONDS must be >= 0")

    if NTFY_RETRY_JITTER_SECONDS < 0:
        raise SystemExit("NTFY_RETRY_JITTER_SECONDS must be >= 0")

    if NTFY_URL and not _looks_like_https(NTFY_URL):
        warnings.add("NTFY_URL is not https")

    if DIVERA_URL and not _looks_like_https(DIVERA_URL):
        warnings.add("DIVERA_URL is not https")

    if DIVERA_FALLBACK_URL and not _looks_like_https(DIVERA_FALLBACK_URL):
        warnings.add("DIVERA_FALLBACK_URL is not https")

    for target in _build_ntfy_targets():
        if target and not _looks_like_https(target):
            warnings.add(f"NTFY target is not https: {target}")

    if not VERIFY_TLS:
        warnings.add("VERIFY_TLS is disabled")


    for warning in sorted(warnings):
        LOGGER.warning(warning)


def audit_log(event: str, payload: Dict[str, Any]) -> None:
    if not AUDIT_LOG_FILE:
        return
    try:
        entry = {
            "ts": int(time.time()),
            "event": event,
            "node_id": NODE_ID,
            "payload": payload,
        }
        os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


def _build_ntfy_targets() -> List[str]:
    targets: List[str] = []
    primary = NTFY_URL.rstrip("/")
    if primary:
        targets.append(primary)
    for raw in NTFY_FALLBACK_URLS.split(","):
        item = raw.strip().rstrip("/")
        if item and item not in targets:
            targets.append(item)
    return targets


def _build_webhook_signature(data: Dict[str, Any], ts: int) -> str:
    if not WEBHOOK_HMAC_SECRET:
        return ""
    basis = "|".join(
        [
            str(ts),
            str(data.get("title", "")).strip(),
            str(data.get("text", "")).strip(),
            str(data.get("address", "")).strip(),
            str(data.get("priority", "")).strip(),
        ]
    )
    return hmac.new(WEBHOOK_HMAC_SECRET.encode("utf-8"), basis.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_replay_guard(data: Dict[str, Any], headers: Any) -> None:
    if not WEBHOOK_REPLAY_PROTECTION:
        return

    ts_raw = data.get("ts", headers.get("X-Webhook-Timestamp", ""))
    sig = str(data.get("sig", headers.get("X-Webhook-Signature", ""))).strip().lower()
    try:
        ts = int(str(ts_raw).strip())
    except Exception:
        raise ValueError("Missing/invalid ts for replay protection")

    if abs(int(time.time()) - ts) > WEBHOOK_MAX_SKEW_SECONDS:
        raise ValueError("Webhook timestamp outside allowed skew")

    expected = _build_webhook_signature(data, ts)
    if not expected or not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid webhook signature")


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
        "active_alarm_keys": [],
        "recent_fingerprints": [],
        "recent_alarm_keys": {},
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


def alarm_dedup_key(alarm: Dict[str, Any]) -> str:
    alarm_id = alarm_id_value(alarm)
    if alarm_id:
        return f"id:{alarm_id}"

    title = safe_get(alarm, ["title", "stichwort", "keyword", "einsatzstichwort"]).casefold()
    address = safe_get(alarm, ["address", "adresse", "ort", "location"]).casefold()
    text = safe_get(alarm, ["text", "info", "description", "beschreibung", "note"]).casefold()
    return "content:" + hashlib.sha256(f"{title}|{address}|{text}".encode("utf-8")).hexdigest()


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


def ntfy_publish(title: str, message: str, priority_override: Optional[str] = None) -> None:
    # Keep title/message payload unchanged; only Priority header is derived from title keywords unless explicitly set.
    priority = priority_override.strip() if priority_override and priority_override.strip() else resolve_ntfy_priority(title)
    headers = {"Title": title, "Priority": priority}
    if NTFY_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_AUTH_TOKEN}"

    targets = _build_ntfy_targets()
    if not targets:
        raise RuntimeError("No NTFY target configured")

    errors: List[str] = []
    for attempt in range(max(1, NTFY_RETRY_ATTEMPTS)):
        for target in targets:
            try:
                requests.post(
                    f"{target}/{NTFY_TOPIC}",
                    data=message.encode("utf-8"),
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    verify=VERIFY_TLS,
                ).raise_for_status()
                audit_log("ntfy_sent", {"target": target, "title": title, "priority": priority})
                return
            except Exception as exc:
                errors.append(f"{target}: {exc}")
        if attempt + 1 < max(1, NTFY_RETRY_ATTEMPTS):
            jitter = random.uniform(0.0, NTFY_RETRY_JITTER_SECONDS) if NTFY_RETRY_JITTER_SECONDS > 0 else 0.0
            time.sleep(NTFY_RETRY_DELAY_SECONDS + jitter)

    audit_log("ntfy_failed", {"title": title, "priority": priority, "errors": errors[-5:]})
    raise RuntimeError("All ntfy targets failed: " + " | ".join(errors[-5:]))


def build_divera_request_url(base_url: str, accesskey: str) -> str:
    raw = base_url.strip()
    if not raw:
        return ""

    if "<api-key>" in raw.lower():
        return raw.replace("<API-Key>", accesskey).replace("<api-key>", accesskey)

    if "accesskey=" in raw.lower():
        if raw.rstrip().endswith("="):
            return f"{raw}{accesskey}"
        return raw

    separator = "&" if "?" in raw else "?"
    return f"{raw}{separator}accesskey={accesskey}"


def fetch_alarms() -> Any:
    if not DIVERA_ACCESSKEY:
        raise RuntimeError(
            "DIVERA_ACCESSKEY is empty or still set to template placeholder "
            f"('{DIVERA_ACCESSKEY_PLACEHOLDER}')."
        )

    urls = [build_divera_request_url(DIVERA_URL, DIVERA_ACCESSKEY)]
    fallback = build_divera_request_url(DIVERA_FALLBACK_URL, DIVERA_ACCESSKEY) if DIVERA_FALLBACK_URL else ""
    if fallback and fallback not in urls:
        urls.append(fallback)

    errors: List[str] = []
    for request_url in urls:
        try:
            r = requests.get(
                request_url,
                timeout=REQUEST_TIMEOUT,
                verify=VERIFY_TLS,
            )
            r.raise_for_status()
            payload = r.json()
            debug_log(f"DiVeRa API OK via {request_url}; top-level type={type(payload).__name__}")
            return payload
        except Exception as e:
            errors.append(f"{request_url}: {e}")

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


def enqueue_notification(state: Dict[str, Any], title: str, message: str, priority_override: Optional[str], error: str) -> None:
    with STATE_LOCK:
        queue = state.setdefault("pending_notifications", [])
        queue.append(
            {
                "title": title,
                "message": message,
                "priority": priority_override or "",
                "error": error,
                "ts": int(time.time()),
            }
        )
        state["pending_notifications"] = queue[-200:]
        save_state(STATE_FILE, state)


def flush_pending_notifications(state: Dict[str, Any]) -> None:
    with STATE_LOCK:
        pending = list(state.get("pending_notifications", []))
    if not pending:
        return

    remaining: List[Dict[str, Any]] = []
    for item in pending:
        try:
            ntfy_publish(item.get("title", ""), item.get("message", ""), priority_override=item.get("priority", ""))
            metric_inc("push_sent")
        except Exception as exc:
            item["error"] = str(exc)
            remaining.append(item)

    with STATE_LOCK:
        state["pending_notifications"] = remaining[-200:]
        save_state(STATE_FILE, state)


def publish_message(state: Dict[str, Any], title: str, message: str, priority_override: Optional[str] = None) -> None:
    try:
        ntfy_publish(title, message, priority_override=priority_override)
        metric_inc("push_sent")
    except Exception as exc:
        enqueue_notification(state, title, message, priority_override, str(exc))
        raise


def run_test_push(args: argparse.Namespace) -> None:
    validate_push_target()
    alarm = build_test_alarm(args)
    title, msg = format_alarm(alarm)
    ntfy_publish(title, msg)
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

    priority = payload.get("priority", payload.get("prio", payload.get("alarm_level", payload.get("level"))))
    if priority is not None and str(priority).strip() != "":
        parsed_priority = _parse_alarm_level(priority)
        if parsed_priority is None:
            raise ValueError("priority must be an integer between 1 and 5")
        alarm["priority"] = str(parsed_priority)

    return alarm


def handle_webhook_alarm(payload: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    alarm = build_alarm_from_webhook_payload(payload)
    title, msg = format_alarm(alarm)
    publish_message(state, title, msg, priority_override=safe_get(alarm, ["priority"]))

    metric_inc("webhook_success")
    audit_log("webhook_alarm", {"title": title, "priority": safe_get(alarm, ["priority"]), "address": safe_get(alarm, ["address"])})
    return {
        "status": "ok",
        "title": title,
        "priority": safe_get(alarm, ["priority"]),
    }




def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_web_form_page(message: str = "", error: bool = False) -> str:
    status_html = ""
    if message:
        color = "#b00020" if error else "#0a7f2e"
        status_html = f'<p style="color:{color};font-weight:600;">{_html_escape(message)}</p>'

    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Alarm Gateway Webformular</title>
</head>
<body style="font-family:Arial,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;">
  <h1>Alarm manuell senden</h1>
  <p>Felder: Titel, Beschreibung, Adresse, Priorität (1-5).</p>
  {status_html}
  <form method="post" action="{_html_escape(WEBHOOK_UI_PATH)}" style="display:grid;gap:0.75rem;">
    <label>Titel*<br/><input required name="title" style="width:100%;padding:0.5rem;"/></label>
    <label>Beschreibung<br/><textarea name="text" rows="4" style="width:100%;padding:0.5rem;"></textarea></label>
    <label>Adresse<br/><input name="address" style="width:100%;padding:0.5rem;"/></label>
    <label>Priorität (1-5)<br/><input name="priority" type="number" min="1" max="5" style="width:120px;padding:0.5rem;"/></label>
    <button type="submit" style="padding:0.6rem 1rem;">Alarm senden</button>
  </form>
</body>
</html>
"""


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ["TOKEN", "SECRET", "PASSWORD", "ACCESSKEY"])


def _current_env_value(name: str, default: Optional[str]) -> str:
    if name in os.environ:
        return str(os.environ.get(name, ""))
    if default is None:
        return ""
    return str(default)


def render_config_page(message: str = "", error: bool = False) -> str:
    status_html = ""
    if message:
        color = "#b00020" if error else "#0a7f2e"
        status_html = f'<p style="color:{color};font-weight:600;">{_html_escape(message)}</p>'

    rows: List[str] = []
    for item in sorted(ENV_DEFINITIONS, key=lambda x: str(x.get("name", ""))):
        name = str(item.get("name", ""))
        default = item.get("default")
        value = _current_env_value(name, default)
        input_type = "password" if _is_secret_name(name) else "text"
        rows.append(
            "<tr>"
            f"<td><code>{_html_escape(name)}</code></td>"
            f"<td><input name=\"cfg_{_html_escape(name)}\" type=\"{input_type}\" value=\"{_html_escape(value)}\" style=\"width:100%;padding:0.4rem;\"/></td>"
            f"<td><small>{_html_escape(str(default) if default is not None else '')}</small></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Alarm Gateway Konfiguration</title>
</head>
<body style="font-family:Arial,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;">
  <h1>Konfiguration</h1>
  <p>Diese Seite zeigt alle bekannten Umgebungsvariablen. Neue Variablen aus Updates erscheinen automatisch.</p>
  {status_html}
  <form method="post" action="{_html_escape(WEBHOOK_CONFIG_PATH)}" style="margin-bottom:1.5rem;">
    <table style="width:100%;border-collapse:collapse;">
      <thead><tr><th style="text-align:left;">Variable</th><th style="text-align:left;">Wert</th><th style="text-align:left;">Default</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p><button type="submit" style="padding:0.6rem 1rem;">Konfiguration speichern</button></p>
  </form>
  <form method="post" action="{_html_escape(WEBHOOK_UPDATE_PATH)}">
    <button type="submit" style="padding:0.6rem 1rem;">Update starten</button>
    <small>Command: <code>{_html_escape(UPDATE_COMMAND or 'nicht konfiguriert')}</code></small>
  </form>
</body>
</html>
"""


def save_config_to_env_file(values: Dict[str, str]) -> None:
    env_file_path = os.environ.get("ALARM_GATEWAY_ENV_FILE", DEFAULT_ENV_FILE)
    os.makedirs(os.path.dirname(env_file_path), exist_ok=True)
    lines = [
        "# alarm-gateway environment file",
        "# managed by web configuration",
    ]
    for item in sorted(ENV_DEFINITIONS, key=lambda x: str(x.get("name", ""))):
        name = str(item.get("name", ""))
        val = values.get(name, _current_env_value(name, item.get("default")))
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}="{escaped}"')
        os.environ[name] = val

    with open(env_file_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def start_update_command() -> None:
    if not UPDATE_COMMAND.strip():
        raise RuntimeError("UPDATE_COMMAND ist nicht gesetzt")
    subprocess.Popen(shlex.split(UPDATE_COMMAND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_form_urlencoded(raw_body: bytes) -> Dict[str, str]:
    from urllib.parse import parse_qs

    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def parse_query_params(path: str) -> Tuple[str, Dict[str, str]]:
    from urllib.parse import parse_qs, urlsplit

    parsed = urlsplit(path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return parsed.path, {k: (v[0] if v else "") for k, v in query.items()}


def _is_authorized(headers: Any, query_params: Dict[str, str]) -> bool:
    if not WEBHOOK_TOKEN:
        return True

    auth = headers.get("Authorization", "")
    expected = f"Bearer {WEBHOOK_TOKEN}"
    if auth.strip() == expected:
        return True

    return str(query_params.get("token", "")).strip() == WEBHOOK_TOKEN


def _is_cluster_authorized(headers: Any, query_params: Dict[str, str]) -> bool:
    if not CLUSTER_SHARED_TOKEN:
        return True

    header_token = str(headers.get("X-Cluster-Token", "")).strip()
    query_token = str(query_params.get("cluster_token", "")).strip()
    return header_token == CLUSTER_SHARED_TOKEN or query_token == CLUSTER_SHARED_TOKEN


def make_webhook_handler(state: Dict[str, Any]):
    class WebhookHandler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_html(self, code: int, html: str) -> None:
            encoded = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            request_path, query_params = parse_query_params(self.path)

            if request_path == WEBHOOK_UI_PATH:
                self._send_html(200, render_web_form_page())
                return

            if request_path == WEBHOOK_CONFIG_PATH:
                if not _is_authorized(self.headers, query_params):
                    self._send_json(401, {"error": "unauthorized"})
                    return
                self._send_html(200, render_config_page())
                return

            if request_path == WEBHOOK_TRIGGER_PATH:
                metric_inc("webhook_requests")
                if not _is_authorized(self.headers, query_params):
                    metric_inc("webhook_error")
                    self._send_json(401, {"error": "unauthorized"})
                    return
                try:
                    _verify_replay_guard(query_params, self.headers)
                    result = handle_webhook_alarm(query_params, state)
                    self._send_json(200, result)
                except Exception as exc:
                    metric_inc("webhook_error")
                    self._send_json(400, {"error": str(exc)})
                return

            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            request_path, query_params = parse_query_params(self.path)

            if request_path == WEBHOOK_PATH:
                metric_inc("webhook_requests")

                if not _is_authorized(self.headers, query_params):
                    metric_inc("webhook_error")
                    self._send_json(401, {"error": "unauthorized"})
                    return

                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length)
                try:
                    content_type = self.headers.get("Content-Type", "")
                    if "application/x-www-form-urlencoded" in content_type:
                        payload = parse_form_urlencoded(body)
                    else:
                        payload = json.loads(body.decode("utf-8")) if body else {}
                    if not isinstance(payload, dict):
                        raise ValueError("Payload must be an object")
                    _verify_replay_guard(payload, self.headers)
                    result = handle_webhook_alarm(payload, state)
                    self._send_json(200, result)
                except Exception as exc:
                    metric_inc("webhook_error")
                    self._send_json(400, {"error": str(exc)})
                return

            if request_path == WEBHOOK_UI_PATH:
                metric_inc("webhook_requests")
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length)
                try:
                    payload = parse_form_urlencoded(body)
                    handle_webhook_alarm(payload, state)
                    self._send_html(200, render_web_form_page("Alarm wurde gesendet."))
                except Exception as exc:
                    metric_inc("webhook_error")
                    self._send_html(400, render_web_form_page(f"Fehler: {exc}", error=True))
                return

            if request_path == WEBHOOK_CONFIG_PATH:
                if not _is_authorized(self.headers, query_params):
                    self._send_json(401, {"error": "unauthorized"})
                    return

                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length)
                try:
                    payload = parse_form_urlencoded(body)
                    values: Dict[str, str] = {}
                    for key, value in payload.items():
                        if key.startswith("cfg_"):
                            values[key[len("cfg_"):]] = str(value)
                    save_config_to_env_file(values)
                    self._send_html(200, render_config_page("Konfiguration gespeichert. Neustart empfohlen."))
                except Exception as exc:
                    self._send_html(400, render_config_page(f"Fehler: {exc}", error=True))
                return

            if request_path == WEBHOOK_UPDATE_PATH:
                if not _is_authorized(self.headers, query_params):
                    self._send_json(401, {"error": "unauthorized"})
                    return
                try:
                    start_update_command()
                    self._send_html(200, render_config_page("Update wurde gestartet."))
                except Exception as exc:
                    self._send_html(400, render_config_page(f"Fehler: {exc}", error=True))
                return

            self._send_json(404, {"error": "not found"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            debug_log(f"webhook: {format % args}")

    return WebhookHandler

def make_health_handler():
    class HealthHandler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            request_path, query_params = parse_query_params(self.path)

            if request_path == HEALTH_METRICS_PATH:
                metrics = metrics_snapshot()
                lines = [
                    "# HELP alarm_gateway_metric Generic runtime metric",
                    "# TYPE alarm_gateway_metric gauge",
                ]
                for key, value in metrics.items():
                    lines.append(f'alarm_gateway_metric{{name="{key}"}} {value}')
                payload = "\n".join(lines) + "\n"
                encoded = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            if request_path != HEALTH_PATH:
                self._send_json(404, {"error": "not found"})
                return

            if not _is_cluster_authorized(self.headers, query_params):
                self._send_json(401, {"error": "unauthorized"})
                return

            cluster = dict(_CLUSTER_CACHE)
            leader_id = str(cluster.get("leader_id", NODE_ID))
            self._send_json(
                200,
                {
                    "status": "ok",
                    "node_id": NODE_ID,
                    "node_priority": NODE_PRIORITY,
                    "leader_id": leader_id,
                    "is_active_sender": leader_id == NODE_ID,
                    "reachable_nodes": cluster.get("reachable", []),
                    "metrics": metrics_snapshot(),
                },
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            debug_log(f"health: {format % args}")

    return HealthHandler


def start_health_server() -> Optional[ThreadingHTTPServer]:
    if not HEALTH_ENABLED:
        return None

    handler = make_health_handler()
    server = ThreadingHTTPServer((HEALTH_BIND, HEALTH_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Health endpoint: http://%s:%s%s", HEALTH_BIND, HEALTH_PORT, HEALTH_PATH)
    LOGGER.info("Prometheus metrics: http://%s:%s%s", HEALTH_BIND, HEALTH_PORT, HEALTH_METRICS_PATH)
    return server


def start_webhook_server(state: Dict[str, Any]) -> Optional[ThreadingHTTPServer]:
    if not WEBHOOK_ENABLED:
        return None

    handler = make_webhook_handler(state)
    server = ThreadingHTTPServer((WEBHOOK_BIND, WEBHOOK_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Webhook JSON endpoint: http://%s:%s%s", WEBHOOK_BIND, WEBHOOK_PORT, WEBHOOK_PATH)
    LOGGER.info("Webhook Trigger endpoint (GET): http://%s:%s%s", WEBHOOK_BIND, WEBHOOK_PORT, WEBHOOK_TRIGGER_PATH)
    LOGGER.info("Web UI: http://%s:%s%s", WEBHOOK_BIND, WEBHOOK_PORT, WEBHOOK_UI_PATH)
    return server


def handle_divera_poll(state: Dict[str, Any]) -> None:
    cluster = resolve_cluster_status(force_refresh=True)
    if str(cluster.get("leader_id", "")) != NODE_ID:
        metric_inc("cluster_standby_skip")
        debug_log(
            f"Standby mode: leader={cluster.get('leader_id')} prio={cluster.get('leader_priority')}"
        )
        return

    data = fetch_alarms()
    alarms = sort_alarms_oldest_first(get_alarms_list(data))
    debug_log(f"DiVeRa Poll: {len(alarms)} Alarm(e) erkannt")

    with STATE_LOCK:
        prev_active = set(state.get("active_fingerprints", []))
        recent = [x for x in state.get("recent_fingerprints", []) if isinstance(x, str)]
    recent_set = set(recent)

    current_fingerprints: List[str] = []
    current_alarm_keys: List[str] = []
    any_sent = False

    now_ts = int(time.time())
    dedup_cutoff = now_ts - int(max(1.0, DEDUP_RETENTION_HOURS) * 3600)
    with STATE_LOCK:
        recent_keys_raw = state.get("recent_alarm_keys", {})
    recent_alarm_keys = {
        str(k): int(v)
        for k, v in (recent_keys_raw.items() if isinstance(recent_keys_raw, dict) else [])
        if isinstance(k, str) and isinstance(v, (int, float)) and int(v) >= dedup_cutoff
    }
    prev_active_keys = set(state.get("active_alarm_keys", []))

    for alarm in alarms:
        fp = fingerprint(alarm)
        dedup_key = alarm_dedup_key(alarm)
        current_fingerprints.append(fp)
        current_alarm_keys.append(dedup_key)

        if fp in prev_active or fp in recent_set or dedup_key in prev_active_keys or dedup_key in recent_alarm_keys:
            continue

        title, msg = format_alarm(alarm)
        publish_message(state, title, msg)
        any_sent = True
        recent.append(fp)
        recent_set.add(fp)
        recent_alarm_keys[dedup_key] = now_ts

    with STATE_LOCK:
        state["active_fingerprints"] = list(dict.fromkeys(current_fingerprints))
        state["active_alarm_keys"] = list(dict.fromkeys(current_alarm_keys))
        state["recent_fingerprints"] = recent[-500:]
        state["recent_alarm_keys"] = dict(list(recent_alarm_keys.items())[-2000:])

        latest = pick_latest_alarm(alarms)
        state["last_fingerprint"] = fingerprint(latest) if latest else None

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
    health_server = start_health_server()
    webhook_server = start_webhook_server(state)
    next_divera = 0.0
    while True:
        try:
            mono_now = time.monotonic()
            if DIVERA_ACCESSKEY and mono_now >= next_divera:
                handle_divera_poll(state)
                metric_inc("divera_poll_ok")
                next_divera = mono_now + POLL_SECONDS

            if is_active_sender():
                flush_pending_notifications(state)
        except Exception as e:
            metric_inc("divera_poll_error")
            LOGGER.error("%s", e)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
