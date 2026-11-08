"""Ingest -> validate -> load -> summarize -> reconcile, with run tracking.

Safety model
  * One transaction per input file: a failed file is rolled back completely.
  * event_id (deterministic hash of normalized content) is the primary key, so
    loading the same event twice can never create a second row.
  * A file's sha256 is recorded only after it loaded fully; re-running skips it.
  * Summaries are rebuilt from the whole store every run (full refresh).
"""
import csv
import gzip
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from logpipe import db
from logpipe.config import Config
from logpipe.errors import RejectError
from logpipe.parser import parse_line
from logpipe.reconcile import render_markdown, run_reconciliation
from logpipe.report import (HOURLY_COLUMNS, OVERALL_COLUMNS, build_summaries,
                            refresh_summary_table, write_csv,
                            EVENT_EXPORT_COLUMNS, REJECT_EXPORT_COLUMNS, export_query_to_csv)
from logpipe.validate import validate_event

logger = logging.getLogger("logpipe.pipeline")

INSERT_EVENT = (
    "INSERT OR IGNORE INTO log_events (event_id, event_ts, event_hour, level, service, endpoint, "
    "http_status, latency_ms, request_id, client_host, component, message, is_error, is_out_of_order, source_file, "
    "line_no, run_id, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
INSERT_REJECT = (
    "INSERT OR IGNORE INTO rejected_records (source_file, line_no, raw_sha256, reject_type, "
    "reject_reason, raw_line, run_id, rejected_at) VALUES (?,?,?,?,?,?,?,?)"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FileStats:
    rows_read: int = 0
    rows_loaded: int = 0
    rows_duplicate: int = 0
    rows_replayed: int = 0
    rows_rejected: int = 0
    blank_lines: int = 0
    out_of_order: int = 0
    unknown_field_rows: int = 0
    reject_reasons: Counter = field(default_factory=Counter)


@dataclass
class RunResult:
    run_id: str
    status: str
    metrics: dict
    checks: list
    out_dir: Path

    @property
    def checks_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)


def make_run_id(started: datetime) -> str:
    return f"run_{started:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def load_services(path) -> list:
    """Read the reference list of valid services (CSV with header: service,owner_team)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [(r["service"].strip().lower(), (r.get("owner_team") or "").strip()) for r in rows if r.get("service")]


def discover_files(input_path, cfg: Config, exclude_paths=()) -> list:
    """Return supported input files, excluding output/database paths to prevent self-ingestion."""
    root = Path(input_path).resolve()
    excluded = [Path(p).resolve() for p in exclude_paths if p is not None]

    def is_excluded(candidate: Path) -> bool:
        resolved = candidate.resolve()
        for blocked in excluded:
            if resolved == blocked:
                return True
            try:
                resolved.relative_to(blocked)
                return True
            except ValueError:
                pass
        return False

    if root.is_file():
        return [] if is_excluded(root) else [(root, root.name)]
    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in cfg.input_extensions and not is_excluded(p))
    return [(p, p.relative_to(root).as_posix()) for p in files]


def _open_binary(path):
    """Plain files and .gz files are both read as bytes lines."""
    return gzip.open(path, "rb") if str(path).lower().endswith(".gz") else open(path, "rb")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_file(conn, run_id, path, source_file, known_services, as_of, cfg, now_iso) -> FileStats:
    """Parse, validate and load one file inside the caller's open transaction."""
    stats = FileStats()
    running_max = None
    # Lines of this file rejected as invalid by an earlier run. If such a line loads now (e.g. the reference
    # list was fixed and the file re-read with --force) its old reject is stale and must be removed, otherwise
    # the quarantine and the event store would both claim the same line.
    prior_invalid = {(ln, sha) for ln, sha in conn.execute(
        "SELECT line_no, raw_sha256 FROM rejected_records WHERE source_file=? AND reject_type='invalid'",
        (source_file,))}
    with _open_binary(path) as fh:
        for line_no, raw in enumerate(fh, start=1):
            body = raw.strip()
            if not body:
                stats.blank_lines += 1
                continue
            stats.rows_read += 1
            raw_sha = hashlib.sha256(body).hexdigest()

            try:
                try:
                    text = raw.decode("utf-8").lstrip("\ufeff").rstrip("\r\n")
                except UnicodeDecodeError:
                    raise RejectError("invalid_encoding") from None
                event = validate_event(parse_line(text, cfg.clf_service), known_services, as_of, cfg)
            except RejectError as exc:
                stats.rows_rejected += 1
                stats.reject_reasons[exc.reason] += 1
                conn.execute(INSERT_REJECT, (source_file, line_no, raw_sha, "invalid", exc.reason,
                                             raw.decode("utf-8", errors="replace").rstrip("\r\n"), run_id, now_iso))
            else:
                ts = event["_ts"]
                out_of_order = 0
                if running_max is not None and (running_max - ts).total_seconds() > cfg.out_of_order_tolerance_s:
                    out_of_order = 1
                    stats.out_of_order += 1
                if running_max is None or ts > running_max:
                    running_max = ts
                if event["unknown_fields"]:
                    stats.unknown_field_rows += 1
                cur = conn.execute(INSERT_EVENT, (
                    event["event_id"], event["event_ts"], event["event_hour"], event["level"], event["service"],
                    event["endpoint"], event["http_status"], event["latency_ms"], event["request_id"],
                    event["client_host"], event["component"], event["message"], event["is_error"], out_of_order, source_file, line_no, run_id, now_iso))
                if prior_invalid and (line_no, raw_sha) in prior_invalid:
                    conn.execute("DELETE FROM rejected_records WHERE source_file=? AND line_no=? "
                                 "AND raw_sha256=? AND reject_type='invalid'", (source_file, line_no, raw_sha))
                if cur.rowcount == 1:
                    stats.rows_loaded += 1
                else:
                    first = conn.execute("SELECT source_file, line_no FROM log_events WHERE event_id=?",
                                         (event["event_id"],)).fetchone()
                    if first == (source_file, line_no):
                        stats.rows_replayed += 1  # same source line re-read (e.g. --force): not a duplicate
                    else:  # same event seen at a different place -> keep first copy, quarantine this one
                        stats.rows_duplicate += 1
                        conn.execute(INSERT_REJECT, (source_file, line_no, raw_sha, "duplicate", "duplicate_event",
                                                     text, run_id, now_iso))

            if cfg.commit_every and stats.rows_read % cfg.commit_every == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
    return stats


def _add_run_file(conn, run_id, source_file, sha, status, stats=None, reason=None):
    s = stats or FileStats()
    conn.execute(
        "INSERT OR REPLACE INTO run_files (run_id, source_file, sha256, status, rows_read, rows_loaded, "
        "rows_duplicate, rows_replayed, rows_rejected, blank_lines, failure_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, source_file, sha, status, s.rows_read, s.rows_loaded, s.rows_duplicate,
         s.rows_replayed, s.rows_rejected, s.blank_lines, reason))


def _export_rejects(conn, path) -> int:
    n = 0
    cur = conn.execute("SELECT source_file, line_no, reject_type, reject_reason, raw_line, run_id "
                       "FROM rejected_records ORDER BY source_file, line_no")
    with open(path, "w", encoding="utf-8") as fh:
        for sf, ln, rtype, reason, raw, rid in cur:
            fh.write(json.dumps({"source_file": sf, "line_no": ln, "reject_type": rtype,
                                 "reject_reason": reason, "raw_line": raw, "run_id": rid},
                                ensure_ascii=False) + "\n")
            n += 1
    return n


def run_pipeline(input_path, db_path, out_dir, services_path, run_id=None, as_of=None,
                 force=False, cfg=None, labels=None) -> RunResult:
    cfg = cfg or Config()
    t_start = time.perf_counter()
    started = utc_now()
    as_of = as_of or started
    run_id = run_id or make_run_id(started)
    log = logging.LoggerAdapter(logger, {"run_id": run_id})
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now_iso = iso(started)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(db_path)
    db.init_schema(conn)
    services = load_services(services_path)
    known_services = {s for s, _ in services}
    conn.execute("BEGIN")
    conn.executemany("INSERT OR REPLACE INTO dim_service (service, owner_team) VALUES (?,?)", services)
    conn.execute("COMMIT")

    conn.execute("INSERT INTO runs (run_id, started_at, status, input_path, as_of) VALUES (?,?,?,?,?)",
                 (run_id, now_iso, "running", str(input_path), iso(as_of)))
    log.info("run started input=%s db=%s as_of=%s force=%s", input_path, db_path, iso(as_of), force)

    failures = []
    totals = Counter()
    reject_reasons = Counter()
    per_file = []
    files = []
    input_missing = not Path(input_path).exists()
    if input_missing:
        failures.append(f"input_path_not_found: {input_path}")
        log.error("input path not found: %s", input_path)
    else:
        files = discover_files(input_path, cfg, exclude_paths=(out_dir, db_path))
        if not files:
            log.warning("empty batch: no input files found under %s", input_path)

    t_ingest = time.perf_counter()
    for path, source_file in files:
        totals["files_total"] += 1
        try:
            sha = sha256_file(path)
        except OSError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _add_run_file(conn, run_id, source_file, None, "failed", reason=reason)
            totals["files_failed"] += 1
            failures.append(f"{source_file}: {reason}")
            per_file.append({"source_file": source_file, "status": "failed", "failure_reason": reason})
            log.error("file failed file=%s reason=%s", source_file, reason)
            continue

        if not force and conn.execute("SELECT 1 FROM ingested_files WHERE sha256=?", (sha,)).fetchone():
            _add_run_file(conn, run_id, source_file, sha, "skipped_already_ingested")
            totals["files_skipped"] += 1
            per_file.append({"source_file": source_file, "status": "skipped_already_ingested"})
            log.info("file skipped (already ingested) file=%s", source_file)
            continue

        try:
            conn.execute("BEGIN")
            stats = ingest_file(conn, run_id, path, source_file, known_services, as_of, cfg, now_iso)
            # Guard against a producer modifying a file while it is being ingested.
            # The pre-read hash must describe the exact bytes that were processed;
            # otherwise a later rerun could incorrectly skip changed content.
            post_read_sha = sha256_file(path)
            if post_read_sha != sha:
                raise RuntimeError("input_file_changed_during_ingestion")
            conn.execute("INSERT OR IGNORE INTO ingested_files (sha256, source_file, first_run_id, ingested_at) "
                         "VALUES (?,?,?,?)", (sha, source_file, run_id, now_iso))
            _add_run_file(conn, run_id, source_file, sha, "success", stats)
            conn.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001 - any failure must roll back this file only
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            reason = f"{type(exc).__name__}: {exc}"
            _add_run_file(conn, run_id, source_file, sha, "failed", reason=reason)
            totals["files_failed"] += 1
            failures.append(f"{source_file}: {reason}")
            per_file.append({"source_file": source_file, "status": "failed", "failure_reason": reason})
            log.error("file failed and was rolled back file=%s reason=%s", source_file, reason)
            continue

        totals["files_succeeded"] += 1
        for key in ("rows_read", "rows_loaded", "rows_duplicate", "rows_replayed", "rows_rejected", "blank_lines"):
            totals[key] += getattr(stats, key)
        reject_reasons.update(stats.reject_reasons)
        ratio = stats.rows_rejected / stats.rows_read if stats.rows_read else 0
        if ratio > cfg.schema_drift_reject_ratio:
            log.warning("possible schema change: %.0f%% of rows rejected file=%s", ratio * 100, source_file)
        per_file.append({
            "source_file": source_file, "status": "success", "rows_read": stats.rows_read,
            "rows_loaded": stats.rows_loaded, "rows_duplicate": stats.rows_duplicate,
            "rows_replayed": stats.rows_replayed, "rows_rejected": stats.rows_rejected, "blank_lines": stats.blank_lines,
            "out_of_order_rows": stats.out_of_order, "rows_with_unknown_fields": stats.unknown_field_rows,
            "reject_reasons": dict(stats.reject_reasons)})
        log.info("file loaded file=%s read=%d loaded=%d duplicate=%d rejected=%d",
                 source_file, stats.rows_read, stats.rows_loaded, stats.rows_duplicate, stats.rows_rejected)
    ingest_seconds = time.perf_counter() - t_ingest

    t_sum = time.perf_counter()
    checks = []
    try:
        hourly, overall = build_summaries(conn)
        refresh_summary_table(conn, hourly)
        write_csv(out_dir / "service_hourly_summary.csv", HOURLY_COLUMNS, hourly)
        write_csv(out_dir / "service_overall_summary.csv", OVERALL_COLUMNS, overall)
        _export_rejects(conn, out_dir / "rejects.jsonl")
        exports = {
            "clean_events": export_query_to_csv(
                conn, out_dir / "clean_events.csv",
                "SELECT %s FROM log_events ORDER BY event_ts, event_id" % ", ".join(EVENT_EXPORT_COLUMNS),
                EVENT_EXPORT_COLUMNS),
            "rejected_records": export_query_to_csv(
                conn, out_dir / "rejected_records.csv",
                "SELECT %s FROM rejected_records ORDER BY source_file, line_no, reject_type" % ", ".join(REJECT_EXPORT_COLUMNS),
                REJECT_EXPORT_COLUMNS),
        }
        checks = run_reconciliation(conn, labels, exports)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"summary_or_reconciliation_failed: {type(exc).__name__}: {exc}")
        log.error("summary step failed: %s", exc)
    summarize_seconds = time.perf_counter() - t_sum

    ok_files = totals["files_succeeded"] + totals["files_skipped"]
    reconciliation_failed = bool(checks) and not all(c["passed"] for c in checks)
    if input_missing or (totals["files_failed"] and not ok_files) or (failures and not totals["files_failed"]) or reconciliation_failed:
        status = "failed"
    elif totals["files_failed"]:
        status = "partial_failure"
    else:
        status = "success"
    failure_reason = "; ".join(failures) if failures else None

    ended = utc_now()
    conn.execute(
        "UPDATE runs SET ended_at=?, status=?, files_total=?, files_succeeded=?, files_skipped=?, files_failed=?, "
        "rows_read=?, rows_loaded=?, rows_duplicate=?, rows_replayed=?, rows_rejected=?, blank_lines=?, failure_reason=? "
        "WHERE run_id=?",
        (iso(ended), status, totals["files_total"], totals["files_succeeded"], totals["files_skipped"],
         totals["files_failed"], totals["rows_read"], totals["rows_loaded"], totals["rows_duplicate"],
         totals["rows_replayed"], totals["rows_rejected"], totals["blank_lines"], failure_reason, run_id))

    reject_breakdown = dict(conn.execute(
        "SELECT reject_reason, COUNT(*) FROM rejected_records GROUP BY reject_reason").fetchall())
    store_rows = conn.execute("SELECT COUNT(*) FROM log_events").fetchone()[0]
    total_seconds = time.perf_counter() - t_start
    metrics = {
        "run_id": run_id, "status": status, "failure_reason": failure_reason,
        "started_at": now_iso, "ended_at": iso(ended), "duration_seconds": round(total_seconds, 3),
        "input_path": str(input_path), "as_of": iso(as_of), "force": force,
        "files_total": totals["files_total"], "files_succeeded": totals["files_succeeded"],
        "files_skipped": totals["files_skipped"], "files_failed": totals["files_failed"],
        "rows_read": totals["rows_read"], "rows_loaded": totals["rows_loaded"],
        "rows_duplicate": totals["rows_duplicate"], "rows_replayed": totals["rows_replayed"],
        "rows_rejected": totals["rows_rejected"], "blank_lines": totals["blank_lines"], "reject_reasons_this_run": dict(reject_reasons),
        "store_total_events": store_rows,
        "timings_seconds": {"ingest": round(ingest_seconds, 3), "summarize_and_reconcile": round(summarize_seconds, 3)},
        "rows_per_second_ingest": round(totals["rows_read"] / ingest_seconds) if ingest_seconds > 0 else None,
        "files": per_file,
        "reconciliation": {"passed": all(c["passed"] for c in checks) if checks else False, "checks": checks},
    }
    (out_dir / "run_report.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    (out_dir / "reconciliation_report.md").write_text(
        render_markdown(checks, metrics, reject_breakdown), encoding="utf-8")
    log.info("run finished status=%s read=%d loaded=%d duplicate=%d rejected=%d failed_files=%d seconds=%.2f",
             status, totals["rows_read"], totals["rows_loaded"], totals["rows_duplicate"],
             totals["rows_rejected"], totals["files_failed"], total_seconds)
    conn.close()
    return RunResult(run_id, status, metrics, checks, out_dir)
