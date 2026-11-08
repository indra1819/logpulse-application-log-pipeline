-- 1. Which services have the highest error rate overall?
SELECT service, event_count, error_count, error_rate, latency_p95_ms
FROM (SELECT service, COUNT(*) AS event_count, SUM(is_error) AS error_count,
             ROUND(1.0 * SUM(is_error) / COUNT(*), 4) AS error_rate
      FROM log_events GROUP BY service) e
JOIN (SELECT service AS s2, MAX(latency_p95_ms) AS latency_p95_ms FROM service_hourly_summary GROUP BY service) h
  ON h.s2 = e.service
ORDER BY error_rate DESC;

-- 2. Worst hours by p95 latency (from the published hourly summary)
SELECT service, event_hour, event_count, error_rate, latency_p50_ms, latency_p95_ms, latency_p99_ms
FROM service_hourly_summary
ORDER BY latency_p95_ms DESC
LIMIT 5;

-- 3. Rejects by reason (quarantine contents)
SELECT reject_type, reject_reason, COUNT(*) AS records
FROM rejected_records
GROUP BY reject_type, reject_reason
ORDER BY records DESC, reject_reason;

-- 4. Source-to-target reconciliation per input file (rows_read must equal the sum of the outcome columns)
SELECT source_file, rows_read, rows_loaded, rows_duplicate, rows_replayed, rows_rejected, blank_lines,
       rows_read - (rows_loaded + rows_duplicate + rows_replayed + rows_rejected) AS unexplained
FROM run_files
WHERE status = 'success'
ORDER BY run_id, source_file;

-- 5. Out-of-order / late-arriving rows that were still loaded, and the proof that no event_id repeats
SELECT SUM(is_out_of_order) AS out_of_order_rows,
       COUNT(*) AS total_events,
       COUNT(*) - COUNT(DISTINCT event_id) AS repeated_event_ids
FROM log_events;
