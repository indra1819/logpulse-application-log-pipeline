"""SQLite target store: schema and connection helper (standard library only)."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_service (
    service     TEXT PRIMARY KEY,
    owner_team  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    status           TEXT NOT NULL CHECK (status IN ('running','success','partial_failure','failed')),
    input_path       TEXT,
    as_of            TEXT,
    files_total      INTEGER DEFAULT 0,
    files_succeeded  INTEGER DEFAULT 0,
    files_skipped    INTEGER DEFAULT 0,
    files_failed     INTEGER DEFAULT 0,
    rows_read        INTEGER DEFAULT 0,
    rows_loaded      INTEGER DEFAULT 0,
    rows_duplicate   INTEGER DEFAULT 0,
    rows_replayed    INTEGER DEFAULT 0,
    rows_rejected    INTEGER DEFAULT 0,
    blank_lines      INTEGER DEFAULT 0,
    failure_reason   TEXT
);

CREATE TABLE IF NOT EXISTS run_files (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    source_file     TEXT NOT NULL,
    sha256          TEXT,
    status          TEXT NOT NULL CHECK (status IN ('success','skipped_already_ingested','failed')),
    rows_read       INTEGER DEFAULT 0,
    rows_loaded     INTEGER DEFAULT 0,
    rows_duplicate  INTEGER DEFAULT 0,
    rows_replayed   INTEGER DEFAULT 0,
    rows_rejected   INTEGER DEFAULT 0,
    blank_lines     INTEGER DEFAULT 0,
    failure_reason  TEXT,
    PRIMARY KEY (run_id, source_file)
);

-- A file's content hash is recorded only after the file was fully loaded.
CREATE TABLE IF NOT EXISTS ingested_files (
    sha256        TEXT PRIMARY KEY,
    source_file   TEXT NOT NULL,
    first_run_id  TEXT NOT NULL,
    ingested_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log_events (
    event_id         TEXT PRIMARY KEY,
    event_ts         TEXT NOT NULL,
    event_hour       TEXT NOT NULL,
    level            TEXT NOT NULL CHECK (level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
    service          TEXT NOT NULL REFERENCES dim_service(service),
    endpoint         TEXT,
    http_status      INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    latency_ms       REAL CHECK (latency_ms IS NULL OR latency_ms >= 0),
    request_id       TEXT,
    client_host      TEXT,
    component        TEXT,
    message          TEXT,
    is_error         INTEGER NOT NULL CHECK (is_error IN (0,1)),
    is_out_of_order  INTEGER NOT NULL DEFAULT 0,
    source_file      TEXT NOT NULL,
    line_no          INTEGER NOT NULL,
    run_id           TEXT NOT NULL,
    ingested_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_service_hour ON log_events (service, event_hour);

CREATE TABLE IF NOT EXISTS rejected_records (
    source_file   TEXT NOT NULL,
    line_no       INTEGER NOT NULL,
    raw_sha256    TEXT NOT NULL,
    reject_type   TEXT NOT NULL CHECK (reject_type IN ('invalid','duplicate')),
    reject_reason TEXT NOT NULL,
    raw_line      TEXT,
    run_id        TEXT NOT NULL,
    rejected_at   TEXT NOT NULL,
    PRIMARY KEY (source_file, line_no, raw_sha256)
);

CREATE TABLE IF NOT EXISTS service_hourly_summary (
    service         TEXT NOT NULL,
    event_hour      TEXT NOT NULL,
    event_count     INTEGER NOT NULL,
    error_count     INTEGER NOT NULL,
    error_rate      REAL NOT NULL,
    latency_count   INTEGER NOT NULL,
    latency_avg_ms  REAL,
    latency_min_ms  REAL,
    latency_p50_ms  REAL,
    latency_p95_ms  REAL,
    latency_p99_ms  REAL,
    latency_max_ms  REAL,
    PRIMARY KEY (service, event_hour)
);
"""


def connect(db_path) -> sqlite3.Connection:
    """Open the store. Transactions are explicit (isolation_level=None)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
