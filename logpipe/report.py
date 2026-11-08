"""Error-rate and latency summaries, rebuilt in full from the event store."""

import csv
import math
from itertools import groupby


HOURLY_COLUMNS = [
    "service", "event_hour", "event_count", "error_count", "error_rate",
    "latency_count", "latency_avg_ms", "latency_min_ms",
    "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
    "latency_max_ms",
]

OVERALL_COLUMNS = [c for c in HOURLY_COLUMNS if c != "event_hour"]


def percentile(sorted_values, pct: float):
    """Nearest-rank percentile of an already sorted list (None if empty)."""
    if not sorted_values:
        return None
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")

    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize_group(rows) -> dict:
    """rows: iterable of (is_error, latency_ms|None). Returns the metric columns."""
    events = errors = 0
    latencies = []

    for is_error, latency in rows:
        events += 1
        errors += is_error
        if latency is not None:
            latencies.append(latency)

    latencies.sort()
    n = len(latencies)
    r = lambda v: None if v is None else round(v, 3)  # noqa: E731

    return {
        "event_count": events,
        "error_count": errors,
        "error_rate": round(errors / events, 4) if events else 0.0,
        "latency_count": n,
        "latency_avg_ms": r(sum(latencies) / n) if n else None,
        "latency_min_ms": r(latencies[0]) if n else None,
        "latency_p50_ms": r(percentile(latencies, 50)),
        "latency_p95_ms": r(percentile(latencies, 95)),
        "latency_p99_ms": r(percentile(latencies, 99)),
        "latency_max_ms": r(latencies[-1]) if n else None,
    }


def build_summaries(conn):
    """Return (hourly_rows, overall_rows) as lists of dicts, ordered by service[, hour]."""
    hourly = []
    cur = conn.execute(
        "SELECT service, event_hour, is_error, latency_ms "
        "FROM log_events ORDER BY service, event_hour"
    )

    for (service, hour), grp in groupby(cur, key=lambda r: (r[0], r[1])):
        row = {"service": service, "event_hour": hour}
        row.update(summarize_group((g[2], g[3]) for g in grp))
        hourly.append(row)

    overall = []
    cur = conn.execute(
        "SELECT service, is_error, latency_ms FROM log_events ORDER BY service"
    )

    for service, grp in groupby(cur, key=lambda r: r[0]):
        row = {"service": service}
        row.update(summarize_group((g[1], g[2]) for g in grp))
        overall.append(row)

    return hourly, overall


def refresh_summary_table(conn, hourly_rows) -> None:
    """Replace service_hourly_summary atomically using a savepoint."""
    savepoint = "refresh_summary_table"
    conn.execute(f"SAVEPOINT {savepoint}")

    try:
        conn.execute("DELETE FROM service_hourly_summary")
        conn.executemany(
            "INSERT INTO service_hourly_summary (%s) VALUES (%s)"
            % (
                ",".join(HOURLY_COLUMNS),
                ",".join("?" * len(HOURLY_COLUMNS)),
            ),
            [[row[c] for c in HOURLY_COLUMNS] for row in hourly_rows],
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def write_csv(path, columns, rows) -> None:
    """Write dictionary rows to CSV with explicit escaping."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=columns,
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writeheader()
        writer.writerows(rows)


# Published accepted / rejected record files.
# The column order is part of the output contract (see docs/data_dictionary.md).

EVENT_EXPORT_COLUMNS = [
    "event_id", "event_ts", "event_hour", "level", "service",
    "endpoint", "http_status", "latency_ms", "request_id",
    "client_host", "component", "message", "is_error",
    "is_out_of_order", "source_file", "line_no", "run_id",
    "ingested_at",
]

REJECT_EXPORT_COLUMNS = [
    "source_file", "line_no", "reject_type", "reject_reason",
    "raw_line", "raw_sha256", "run_id", "rejected_at",
]


def export_query_to_csv(conn, path, sql, columns) -> int:
    """
    Stream a query to a CSV file.

    Writes a header and one row per record.
    None values become empty CSV cells.
    Returns the data row count.
    """
    n = 0

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(
            fh,
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writerow(columns)

        for row in conn.execute(sql):
            writer.writerow(row)
            n += 1

    return n
