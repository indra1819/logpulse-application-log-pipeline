"""Source-to-target reconciliation checks and a Markdown validation report."""
import json


def _scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _check(name, passed, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


def run_reconciliation(conn, labels=None, exports=None) -> list:
    checks = []

    bad = _scalar(conn, "SELECT COUNT(*) FROM run_files WHERE status='success' "
                        "AND rows_read != rows_loaded + rows_duplicate + rows_replayed + rows_rejected")
    checks.append(_check("file_row_balance", bad == 0,
                         "every loaded file: rows_read = loaded + duplicate + replayed + rejected "
                         f"({bad} files out of balance)"))

    stored = _scalar(conn, "SELECT COUNT(*) FROM log_events")
    ledger = _scalar(conn, "SELECT COALESCE(SUM(rows_loaded),0) FROM run_files WHERE status='success'")
    checks.append(_check("store_matches_run_ledger", stored == ledger,
                         f"log_events rows={stored}, sum of rows_loaded across runs={ledger}"))

    distinct = _scalar(conn, "SELECT COUNT(DISTINCT event_id) FROM log_events")
    checks.append(_check("event_id_unique", distinct == stored, f"{distinct} distinct ids for {stored} rows"))

    orphans = _scalar(conn, "SELECT COUNT(*) FROM log_events e LEFT JOIN dim_service s "
                            "ON s.service = e.service WHERE s.service IS NULL")
    checks.append(_check("referential_integrity_service", orphans == 0,
                         f"{orphans} events reference a service missing from dim_service"))

    untraceable = _scalar(conn, "SELECT COUNT(*) FROM log_events WHERE source_file IS NULL OR source_file='' "
                                "OR line_no IS NULL")
    checks.append(_check("events_traceable_to_source", untraceable == 0,
                         f"{untraceable} events lack source_file/line_no"))

    s_events = _scalar(conn, "SELECT COALESCE(SUM(event_count),0) FROM service_hourly_summary")
    checks.append(_check("summary_event_count_matches_store", s_events == stored,
                         f"summary events={s_events}, store rows={stored}"))

    s_err = _scalar(conn, "SELECT COALESCE(SUM(error_count),0) FROM service_hourly_summary")
    e_err = _scalar(conn, "SELECT COALESCE(SUM(is_error),0) FROM log_events")
    checks.append(_check("summary_error_count_matches_store", s_err == e_err,
                         f"summary errors={s_err}, store errors={e_err}"))

    if exports is not None:
        rejected = _scalar(conn, "SELECT COUNT(*) FROM rejected_records")
        checks.append(_check("published_csv_matches_store",
                             exports.get("clean_events") == stored and exports.get("rejected_records") == rejected,
                             f"clean_events.csv rows={exports.get('clean_events')} (store {stored}), "
                             f"rejected_records.csv rows={exports.get('rejected_records')} (store {rejected})"))

    if labels:
        checks.append(_check("labels_loaded_count", stored == labels["expected_loaded"],
                             f"expected={labels['expected_loaded']}, actual={stored}"))
        dups = _scalar(conn, "SELECT COUNT(*) FROM rejected_records WHERE reject_type='duplicate'")
        checks.append(_check("labels_duplicate_count", dups == labels["expected_duplicates"],
                             f"expected={labels['expected_duplicates']}, actual={dups}"))
        actual = dict(conn.execute("SELECT reject_reason, COUNT(*) FROM rejected_records "
                                   "WHERE reject_type='invalid' GROUP BY reject_reason").fetchall())
        expected = labels["expected_rejects"]
        diffs = {k: (expected.get(k, 0), actual.get(k, 0))
                 for k in sorted(set(expected) | set(actual)) if expected.get(k, 0) != actual.get(k, 0)}
        checks.append(_check("labels_reject_reasons", not diffs,
                             "all reject reasons match" if not diffs
                             else "mismatches (expected, actual): " + json.dumps(diffs)))
    return checks


def render_markdown(checks, run_metrics: dict, reject_breakdown: dict) -> str:
    lines = ["# Validation / reconciliation report", "",
             f"- run_id: `{run_metrics['run_id']}`",
             f"- status: **{run_metrics['status']}**",
             f"- rows read / loaded / duplicate / rejected: "
             f"{run_metrics['rows_read']} / {run_metrics['rows_loaded']} / "
             f"{run_metrics['rows_duplicate']} / {run_metrics['rows_rejected']}", "",
             "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['name']} | {'PASS' if c['passed'] else '**FAIL**'} | {c['detail']} |")
    lines += ["", "## Rejects by reason (whole store)", "", "| reason | count |", "|---|---|"]
    for reason, n in sorted(reject_breakdown.items()):
        lines.append(f"| {reason} | {n} |")
    if not reject_breakdown:
        lines.append("| (none) | 0 |")
    return "\n".join(lines) + "\n"
