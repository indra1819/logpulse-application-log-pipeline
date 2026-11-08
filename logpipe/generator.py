"""Seeded synthetic log generator with labelled invalid records and duplicates.

`rows` is the number of DISTINCT valid events. On top of those the generator adds
duplicates, labelled invalid lines, blank lines, out-of-order and late-arriving lines.
Expected results are written to a separate labels file, never into the input data.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
BASE_TIME = datetime(2026, 11, 1, tzinfo=UTC)
SPAN_HOURS = 48

SERVICES = {
    "auth": (["/login", "/logout", "/token/refresh"], 60),
    "payments": (["/charge", "/refund", "/invoice"], 220),
    "search": (["/query", "/suggest"], 90),
    "orders": (["/orders", "/orders/{id}", "/cart"], 140),
    "notifications": (["/email/send", "/push/send"], 180),
    "inventory": (["/stock", "/stock/reserve"], 80),
}
LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LEVEL_WEIGHTS = [10, 60, 15, 12, 3]
LEVEL_SPELLINGS = {
    "DEBUG": ["DEBUG", "debug", "TRACE"], "INFO": ["INFO", "info", "Info"],
    "WARNING": ["WARNING", "WARN", "warn"], "ERROR": ["ERROR", "error", "ERR"],
    "CRITICAL": ["CRITICAL", "FATAL", "crit"],
}
STATUS_BY_LEVEL = {
    "DEBUG": [200], "INFO": [200, 200, 201, 204], "WARNING": [400, 404, 429],
    "ERROR": [500, 502, 503, 504], "CRITICAL": [500, 503],
}
MESSAGES = {
    "DEBUG": ["cache lookup", "query plan chosen"],
    "INFO": ["request completed", "user session refreshed", "batch accepted"],
    "WARNING": ["slow upstream response", "retry scheduled", "rate limit near"],
    "ERROR": ["upstream timeout", "db connection reset", 'worker said "no capacity" retry=3'],
    "CRITICAL": ["out of memory", "primary db unreachable"],
}
GARBAGE_LINES = [
    "Traceback (most recent call last):", "    at com.example.Handler.run(Handler.java:42)",
    "---- service restarted ----", "[ERROR] no timestamp here", "random text that is not a log line",
]
MAX_LATENCY_MS = 3_600_000

INVALID_KINDS = [
    "malformed_json", "malformed_line", "missing_required_field:timestamp",
    "missing_required_field:level", "missing_required_field:service", "invalid_timestamp",
    "invalid_severity", "unknown_service", "invalid_latency", "latency_out_of_range",
    "invalid_status", "future_timestamp", "invalid_encoding",
]


def _make_event(rng, i):
    service = rng.choice(list(SERVICES))
    endpoints, base_latency = SERVICES[service]
    ts = BASE_TIME + timedelta(seconds=rng.randrange(SPAN_HOURS * 3600))
    if rng.random() < 0.5:
        ts += timedelta(milliseconds=rng.randrange(1, 1000))
    level = rng.choices(LEVELS, LEVEL_WEIGHTS)[0]
    is_http = rng.random() > 0.1
    status = endpoint = None
    if is_http:
        endpoint = rng.choice(endpoints)
        status = rng.choice(STATUS_BY_LEVEL[level])
        if rng.random() < 0.003:
            status = rng.choice([100, 599])  # boundary values
    latency = None
    if is_http or rng.random() < 0.3:
        latency = int(base_latency * rng.lognormvariate(0, 0.6) * (4 if level in ("ERROR", "CRITICAL") else 1))
        roll = rng.random()
        if roll < 0.002:
            latency = 0  # boundary
        elif roll < 0.004:
            latency = MAX_LATENCY_MS  # boundary
        latency = min(latency, MAX_LATENCY_MS)
    return {"ts": ts, "level": level, "service": service, "endpoint": endpoint, "status": status,
            "latency_ms": latency, "request_id": f"req-{i:08d}", "message": rng.choice(MESSAGES[level])}


def _iso_z(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S") + f".{ts.microsecond // 1000:03d}Z"


def _iso_offset(ts, rng):
    off = rng.choice([timedelta(hours=5, minutes=30), timedelta(hours=-8), timedelta(0), timedelta(hours=1)])
    local = ts + off
    sign = "+" if off >= timedelta(0) else "-"
    a = abs(off)
    return local.strftime("%Y-%m-%dT%H:%M:%S") + f".{local.microsecond // 1000:03d}" \
        f"{sign}{a.seconds // 3600:02d}:{a.seconds % 3600 // 60:02d}"


def _epoch_ms(ts):
    return int((ts - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000 + 0.5)


def _lat_value(latency, rng):
    r = rng.random()
    if r < 0.15:
        return str(latency)  # sloppy numeric string
    if r < 0.3:
        return float(latency)
    return latency


def _render(ev, rng):
    """Render one event in a randomly chosen layout. Returns bytes."""
    has_ms = ev["ts"].microsecond != 0
    fmts = ["json_iso", "json_offset", "json_epoch_ms", "json_alias", "text", "text_offset"]
    if not has_ms:
        fmts.append("json_epoch_s")
    fmt = rng.choice(fmts)
    level = rng.choice(LEVEL_SPELLINGS[ev["level"]])

    if fmt.startswith("json"):
        if fmt == "json_alias":
            rec = {"ts": _epoch_ms(ev["ts"]) if rng.random() < 0.5 else _iso_z(ev["ts"]),
                   "severity": level, "svc": ev["service"].upper() if rng.random() < 0.3 else ev["service"],
                   "host": f"node-{rng.randrange(1, 9)}", "trace": {"span": rng.randrange(1000)}}
            if ev["endpoint"]:
                rec["path"] = ev["endpoint"]
            if ev["status"] is not None:
                rec["status_code"] = ev["status"]
            if ev["latency_ms"] is not None:
                rec["duration_s"] = ev["latency_ms"] / 1000
            rec["req_id"] = ev["request_id"]
            rec["msg"] = ev["message"]
        else:
            if fmt == "json_iso":
                ts_val = _iso_z(ev["ts"])
            elif fmt == "json_offset":
                ts_val = _iso_offset(ev["ts"], rng)
            elif fmt == "json_epoch_ms":
                ts_val = _epoch_ms(ev["ts"])
            else:
                ts_val = _epoch_ms(ev["ts"]) // 1000
            rec = {"timestamp": ts_val, "level": level, "service": ev["service"]}
            if ev["endpoint"]:
                rec["endpoint"] = ev["endpoint"]
            if ev["status"] is not None:
                rec["status"] = ev["status"]
            if ev["latency_ms"] is not None:
                rec["latency_ms"] = _lat_value(ev["latency_ms"], rng)
            elif rng.random() < 0.5:
                rec["latency_ms"] = None  # explicit null
            rec["request_id"] = ev["request_id"]
            rec["message"] = ev["message"]
        return json.dumps(rec).encode("utf-8")

    if fmt == "text":
        ts_txt = ev["ts"].strftime("%Y-%m-%d %H:%M:%S") + f",{ev['ts'].microsecond // 1000:03d}"
    else:
        ts_txt = _iso_offset(ev["ts"], rng)
    parts = [ts_txt, level, f"[{ev['service']}]", f"request_id={ev['request_id']}"]
    if ev["endpoint"]:
        parts.append(f"endpoint={ev['endpoint']}")
    if ev["status"] is not None:
        parts.append(f"status={ev['status']}")
    if ev["latency_ms"] is not None:
        parts.append(f"latency_ms={ev['latency_ms']}")
    parts.append('msg="' + ev["message"].replace("\\", "\\\\").replace('"', '\\"') + '"')
    return " ".join(parts).encode("utf-8")


def _invalid_line(kind, rng, i):
    ev = _make_event(rng, 9_000_000 + i)
    rec = json.loads(_render_json(ev))
    if kind == "malformed_json":
        line = json.dumps(rec)
        return line[: -rng.randrange(1, 12)].encode("utf-8")
    if kind == "malformed_line":
        return rng.choice(GARBAGE_LINES).encode("utf-8")
    if kind.startswith("missing_required_field:"):
        key = kind.split(":", 1)[1]
        if rng.random() < 0.5:
            rec.pop(key)
        else:
            rec[key] = None
    elif kind == "invalid_timestamp":
        rec["timestamp"] = rng.choice(["not-a-time", "2026-13-45 99:99:99", "yesterday", "0", "2026-02-30T10:00:00Z"])
    elif kind == "invalid_severity":
        rec["level"] = rng.choice(["LOUD", "unknown", "42"])
    elif kind == "unknown_service":
        rec["service"] = "ghost-service"
    elif kind == "invalid_latency":
        rec["latency_ms"] = rng.choice(["abc", -5, "12ms"])
    elif kind == "latency_out_of_range":
        rec["latency_ms"] = rng.choice([MAX_LATENCY_MS + 1, 99_999_999])
    elif kind == "invalid_status":
        rec["status"] = rng.choice([999, 99, "OK", 600])
    elif kind == "future_timestamp":
        rec["timestamp"] = _iso_z(BASE_TIME + timedelta(days=400))
    elif kind == "invalid_encoding":
        return b"\xff\xfe corrupted bytes \x80\x81"
    return json.dumps(rec).encode("utf-8")


def _render_json(ev):
    rec = {"timestamp": _iso_z(ev["ts"]), "level": ev["level"], "service": ev["service"],
           "endpoint": ev["endpoint"] or "/health", "status": ev["status"] or 200,
           "latency_ms": ev["latency_ms"] if ev["latency_ms"] is not None else 50,
           "request_id": ev["request_id"], "message": ev["message"]}
    return json.dumps(rec)


def generate(rows: int, seed: int, out_dir, labels_dir, n_files: int = 4) -> dict:
    """Write input files to out_dir and expected_labels.json to labels_dir. Returns the labels."""
    rng = random.Random(seed)
    out_dir, labels_dir = Path(out_dir), Path(labels_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    span_s = SPAN_HOURS * 3600

    events = [_make_event(rng, i) for i in range(rows)]
    entries = []  # (sort_key_seconds, bytes)
    for ev in events:
        entries.append(((ev["ts"] - BASE_TIME).total_seconds(), _render(ev, rng)))

    n_dups = max(2, rows // 50)
    for ev in rng.sample(events, min(n_dups, len(events))):
        delay = rng.randrange(0, 3600)  # a retry shipped the same event again later
        entries.append(((ev["ts"] - BASE_TIME).total_seconds() + delay, _render(ev, rng)))
    n_dups = min(n_dups, len(events))

    per_kind = max(1, round(rows * 0.004))
    expected_rejects = {}
    for kind in INVALID_KINDS:
        for j in range(per_kind):
            entries.append((rng.uniform(0, span_s), _invalid_line(kind, rng, len(entries) + j)))
        expected_rejects[kind] = per_kind

    n_blank = max(1, rows // 200)
    for _ in range(n_blank):
        entries.append((rng.uniform(0, span_s), b""))

    entries.sort(key=lambda e: e[0])
    n_files = max(1, n_files)
    size = -(-len(entries) // n_files)
    files = [[e[1] for e in entries[k * size:(k + 1) * size]] for k in range(n_files)]

    # Late-arriving lines: ~1% of lines are delivered in a later file than their timestamp suggests.
    if n_files > 1:
        for k in range(n_files - 1):
            for _ in range(max(1, len(files[k]) // 100)):
                line = files[k].pop(rng.randrange(len(files[k])))
                files[rng.randrange(k + 1, n_files)].append(line)
    # Out-of-order lines: swap ~3% of neighbouring lines inside each file.
    for lines in files:
        for _ in range(len(lines) * 3 // 100):
            a = rng.randrange(len(lines))
            b = min(len(lines) - 1, a + rng.randrange(1, 6))
            lines[a], lines[b] = lines[b], lines[a]

    for k, lines in enumerate(files):
        newline = b"\r\n" if k == 1 else b"\n"  # one file uses Windows line endings
        (out_dir / f"app_logs_{k:03d}.log").write_bytes(newline.join(lines) + newline)

    labels = {
        "seed": seed, "rows_requested": rows, "input_files": n_files,
        "total_data_lines": rows + n_dups + sum(expected_rejects.values()),
        "blank_lines": n_blank,
        "expected_loaded": rows, "expected_duplicates": n_dups, "expected_rejects": expected_rejects,
    }
    (labels_dir / "expected_labels.json").write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    return labels
