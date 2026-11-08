-- Integrity / reconciliation checks as plain SQL. Every row must show violations = 0.
-- Run:  python -m logpipe query --db out/logpipe.db --file docs/verification_queries.sql
-- (These complement the automatic checks in reconciliation_report.md; a reviewer can run them without reading Python.)
SELECT 'orphan_service_keys' AS check_name, COUNT(*) AS violations
FROM log_events e LEFT JOIN dim_service d ON d.service = e.service WHERE d.service IS NULL
UNION ALL
SELECT 'repeated_event_ids', COUNT(*) - COUNT(DISTINCT event_id) FROM log_events
UNION ALL
SELECT 'line_both_loaded_and_rejected_as_invalid', COUNT(*)
FROM log_events e JOIN rejected_records r
  ON r.source_file = e.source_file AND r.line_no = e.line_no AND r.reject_type = 'invalid'
UNION ALL
SELECT 'run_files_unexplained_rows', COALESCE(SUM(ABS(rows_read - (rows_loaded + rows_duplicate + rows_replayed + rows_rejected))), 0)
FROM run_files WHERE status = 'success'
UNION ALL
SELECT 'hourly_summary_event_count_mismatch',
       ABS(COALESCE((SELECT SUM(event_count) FROM service_hourly_summary), 0) - (SELECT COUNT(*) FROM log_events))
UNION ALL
SELECT 'hourly_summary_error_count_mismatch',
       ABS(COALESCE((SELECT SUM(error_count) FROM service_hourly_summary), 0) - (SELECT COALESCE(SUM(is_error), 0) FROM log_events))
UNION ALL
SELECT 'error_rate_inconsistent', COUNT(*) FROM service_hourly_summary
WHERE error_rate < 0 OR error_rate > 1 OR ABS(error_rate - 1.0 * error_count / event_count) > 0.0001
UNION ALL
SELECT 'latency_percentiles_out_of_order', COUNT(*) FROM service_hourly_summary
WHERE latency_count > 0 AND NOT (latency_min_ms <= latency_p50_ms AND latency_p50_ms <= latency_p95_ms
                                 AND latency_p95_ms <= latency_p99_ms AND latency_p99_ms <= latency_max_ms)
UNION ALL
SELECT 'is_error_rule_violated', COUNT(*) FROM log_events
WHERE is_error <> (CASE WHEN level IN ('ERROR','CRITICAL') OR COALESCE(http_status, 0) >= 500 THEN 1 ELSE 0 END)
UNION ALL
SELECT 'timestamp_not_iso8601_utc', COUNT(*) FROM log_events
WHERE event_ts NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z';
