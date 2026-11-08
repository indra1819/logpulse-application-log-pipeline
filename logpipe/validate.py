"""Schema mapping, business-rule validation and normalization of one record.

Validation order (first failing rule wins, so each reject has exactly one reason):
  required fields -> timestamp -> future timestamp -> severity -> service
  (reference check) -> http status -> latency
"""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta

from logpipe.config import Config
from logpipe.errors import RejectError
from logpipe.normalize import (ERROR_LEVELS, hour_bucket, normalize_severity,
                               normalize_timestamp, to_iso_utc)

CANONICAL_ALIASES = {
    "timestamp": ("timestamp", "ts", "time", "@timestamp", "datetime"),
    "level": ("level", "severity", "lvl", "log_level", "loglevel"),
    "service": ("service", "svc", "service_name", "app", "application"),
    "endpoint": ("endpoint", "path", "route", "url"),
    "status": ("status", "status_code", "http_status", "http_code"),
    "request_id": ("request_id", "req_id", "trace_id", "correlation_id"),
    "client_host": ("client_host", "remote_addr", "client_ip", "remote_host"),
    "component": ("component", "logger", "logger_name"),
    "message": ("message", "msg"),
}
# (field name, multiplier to milliseconds). Seconds variants are converted.
LATENCY_ALIASES = (
    ("latency_ms", 1.0), ("duration_ms", 1.0), ("elapsed_ms", 1.0),
    ("response_time_ms", 1.0), ("latency_s", 1000.0), ("duration_s", 1000.0),
)
REQUIRED = ("timestamp", "level", "service")
# alias name -> (canonical name, priority rank, multiplier). One dict lookup per input key.
_ALIAS_INDEX = {}
for _canon, _names in CANONICAL_ALIASES.items():
    for _rank, _name in enumerate(_names):
        _ALIAS_INDEX[_name] = (_canon, _rank, 1.0)
for _rank, (_name, _factor) in enumerate(LATENCY_ALIASES):
    _ALIAS_INDEX[_name] = ("latency", _rank, _factor)
_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
MAX_MESSAGE_CHARS = 2000


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def canonicalize(fields: dict):
    """Map alias keys to canonical names in a single pass over the input keys.

    Returns (canonical dict, latency (value, factor) or None, sorted unknown keys).
    Keys are case-insensitive; null/blank values count as absent; when several aliases
    of one field are present, the earliest alias in CANONICAL_ALIASES wins.
    """
    best = {}
    unknown = []
    for key, value in fields.items():
        key = str(key).lower()
        entry = _ALIAS_INDEX.get(key)
        if entry is None:
            unknown.append(key)
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        canon, rank, factor = entry
        current = best.get(canon)
        if current is None or rank < current[0]:
            best[canon] = (rank, value, factor)
    latency = (best["latency"][1], best["latency"][2]) if "latency" in best else None
    out = {c: v[1] for c, v in best.items() if c != "latency"}
    return out, latency, sorted(unknown)


def _parse_status(v, cfg: Config) -> int:
    if isinstance(v, bool):
        raise RejectError("invalid_status")
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not v.is_integer():
            raise RejectError("invalid_status")
        n = int(v)
    elif isinstance(v, str) and re.fullmatch(r"\d{1,4}", v.strip()):
        n = int(v.strip())
    else:
        raise RejectError("invalid_status")
    if not (cfg.min_http_status <= n <= cfg.max_http_status):
        raise RejectError("invalid_status")
    return n


def _parse_latency_ms(value, factor: float, cfg: Config) -> float:
    if isinstance(value, bool):
        raise RejectError("invalid_latency")
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str) and _NUMERIC_RE.match(value.strip()):
        num = float(value.strip())
    else:
        raise RejectError("invalid_latency")
    if not math.isfinite(num) or num < 0:
        raise RejectError("invalid_latency")
    ms = round(num * factor, 3)
    if ms > cfg.max_latency_ms:
        raise RejectError("latency_out_of_range")
    return ms


def _clean_text(v, limit=None):
    if _is_blank(v):
        return None
    s = str(v).strip()
    return s[:limit] if limit else s


def make_event_id(parts) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "evt_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def validate_event(fields: dict, known_services: set, as_of: datetime, cfg: Config) -> dict:
    """Return a normalized event dict or raise RejectError(reason)."""
    canon, latency, unknown = canonicalize(fields)

    for name in REQUIRED:
        if name not in canon:
            raise RejectError(f"missing_required_field:{name}")

    try:
        ts = normalize_timestamp(canon["timestamp"])
    except ValueError:
        raise RejectError("invalid_timestamp") from None
    if ts > as_of + timedelta(hours=cfg.max_future_skew_hours):
        raise RejectError("future_timestamp")

    try:
        level = normalize_severity(canon["level"])
    except ValueError:
        raise RejectError("invalid_severity") from None

    service = str(canon["service"]).strip().lower()
    if service not in known_services:
        raise RejectError("unknown_service")

    status = _parse_status(canon["status"], cfg) if "status" in canon else None
    latency_ms = _parse_latency_ms(latency[0], latency[1], cfg) if latency else None

    endpoint = _clean_text(canon.get("endpoint"))
    request_id = _clean_text(canon.get("request_id"))
    client_host = _clean_text(canon.get("client_host"))
    component = _clean_text(canon.get("component"))
    message = _clean_text(canon.get("message"), MAX_MESSAGE_CHARS)
    ts_iso = to_iso_utc(ts)

    return {
        "event_id": make_event_id([ts_iso, level, service, endpoint, status, latency_ms, request_id, client_host, component, message]),
        "event_ts": ts_iso,
        "event_hour": hour_bucket(ts),
        "level": level,
        "service": service,
        "endpoint": endpoint,
        "http_status": status,
        "latency_ms": latency_ms,
        "request_id": request_id,
        "client_host": client_host,
        "component": component,
        "message": message,
        "is_error": int(level in ERROR_LEVELS or (status is not None and status >= 500)),
        "unknown_fields": unknown,
        "_ts": ts,
    }
