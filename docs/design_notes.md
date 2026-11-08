# Design and trade-off notes

## Shape of the solution
A single-process Python pipeline using only the standard library, with SQLite as the target store. This is a deliberately
small, complete vertical slice: it runs anywhere with `python -m logpipe ...`, needs no credentials, and every number it
produces can be checked with SQL.

```
input files --> parse_line --> validate_event --> INSERT OR IGNORE log_events   (one transaction per file)
 (.log/.jsonl)   (json|text)   (aliases, types,        |  duplicate -> rejected_records (type duplicate)
                                 rules, reference)     +- invalid   -> rejected_records (type invalid)
                                                   |
                          build_summaries (full refresh from log_events) --> service_hourly_summary (+ CSV)
                                                   |
                          run_reconciliation --> reconciliation_report.md, run_report.json
```
Modules: `adapters/` (format-specific parsers), `parser.py` (line -> adapter dispatch), `normalize.py` (timestamps/severity), `validate.py` (aliases + business rules +
`event_id`), `pipeline.py` (orchestration, transactions, run tracking), `report.py` (summaries), `reconcile.py` (checks),
`generator.py` (synthetic data + labels), `cli.py`.

## Key decisions and why
| decision | reason | trade-off |
|---|---|---|
| `event_id` = hash of normalized content | deterministic, layout/timezone independent, makes the load idempotent through the primary key | two genuinely distinct events with identical content (same ms, service, request_id, message...) collapse into one. Real logs normally carry a `request_id`; documented limitation |
| Keep the **first** copy of a duplicate, quarantine the rest | simple, deterministic given file order, nothing is silently lost | "first" is defined by processing order (files sorted by name, then line number) |
| One transaction per file | a crash or error leaves no half-loaded file; retry is just "run again" | a very large file is one big transaction |
| Skip files by SHA-256 of content | cheap restart/retry; unchanged files cost nothing | an edited file has a new hash, is re-read, and its already-loaded events are ignored by the PK (counted as `rows_replayed`/`rows_duplicate`) |
| Summaries rebuilt from the whole store each run | late and out-of-order data automatically corrects earlier hours; no incremental-state bugs; re-running gives identical output | cost grows with total history (fine at this scale; see next steps) |
| Nearest-rank percentiles computed in Python | exact, easy to explain and to test by hand; SQLite has no percentile function | needs all latencies of a group in memory (one hour of one service) |
| Timestamps without offset are assumed UTC | logs rarely say; assumption is explicit and documented | wrong if a source logs local time without an offset |
| `--as-of` for the future-timestamp rule | tests and the sample are reproducible regardless of the day they run | default is the current time |
| Out-of-order rows are loaded and flagged, not rejected | event time is what matters for the summaries, arrival order is not | `is_out_of_order` is relative to file order only |
| CLF level is derived from the HTTP status; only 5xx counts as an error | the source has no severity, and 4xx (for example 404) is a client problem rather than a server failure | a different definition of "error" would change error rates; it is a rule in the CLF adapter |
| `client_host` is part of the event identity | in access logs two different clients can hit the same URL in the same second | the same client repeating the same request within one second is treated as one event |
| Unknown extra fields are ignored but counted | tolerant to producers adding fields | extra data is not kept in the target |

## Accepted and rejected record files
Each run publishes `clean_events.csv` (accepted) and `rejected_records.csv` (rejected + duplicates) next to the summaries, so a reviewer or analyst can open both in a spreadsheet without SQL, and every rejected line is visible with its reason and `source_file`/`line_no`. `rejects.jsonl` is kept because it preserves raw text exactly. The files are written from the store (not from the current run), so reruns and `--force` produce identical files; the check `published_csv_matches_store` compares their row counts with the database after writing.
Trade-offs, stated plainly: the files are rewritten in full each run (about 0.8 s and 22 MB for the 100,000-event run, i.e. cost grows with the store; at millions of rows write per-run or partitioned files instead), and cells are not neutralized against spreadsheet formula injection - a rejected `raw_line` that starts with `=` would be interpreted as a formula by Excel, so treat `rejected_records.csv` as untrusted input and import it as text.

## Idempotency, retry, restart, backfill
* **Rerun, same input:** every file whose hash is already in `ingested_files` is skipped; nothing is read or written except run tracking and the refreshed (identical) summaries.
* **Fixing the cause of rejects (e.g. a new service in the reference list):** a plain rerun skips the file (same hash); use `--force`. Lines that now load have their stale `invalid` reject row deleted (matched on file, line number and content hash), so the quarantine never contradicts the event store. Rejects of lines that still fail are kept.
* **Rerun, `--force`:** files are re-read; `INSERT OR IGNORE` on `event_id` means no new rows. A line that maps to the same stored `(source_file, line_no)` is counted as `rows_replayed`, so a forced rerun never inflates duplicates.
* **Retry after partial failure:** a failing file is rolled back and recorded as `failed` with the reason; other files continue (`partial_failure`). Run the same command again: finished files are skipped, the failed file is retried.
* **Restart after a crash:** committed files stay; the interrupted file is rolled back by SQLite; a leftover `runs.status = 'running'` row identifies the interrupted run. Run again.
* **Backfill / late data:** put the new or corrected file in the input directory and run. New events are inserted and the affected hours are recomputed because summaries are a full refresh.

## Observability
Every run records `run_id`, start/end time, files and rows in/out, duplicates, rejects by reason, failure reason, stage timings and throughput in the `runs`/`run_files` tables and in `run_report.json`; `pipeline.log` repeats the key events with `run_id`. A file with more than 50% rejected rows logs a "possible schema change" warning. The CLI exits non-zero on `partial_failure`, `failed`, or any failed reconciliation check.

## Known limitations
* Single process, single writer (SQLite); no concurrent runs against the same database.
* Not a streaming system; it processes files in batch.
* `rejected_records.raw_line` stores the original line, which may contain sensitive data in real logs; restrict access or hash it in production.
* The OpenStack adapter was verified on the real Loghub `OpenStack_2k.log` sample (2,000 of 2,000 lines parsed, 0 rejects) but not on the full 207,820-line files; layout variants there would land in the quarantine (visible by reason) and need a small parser tweak, and services not in the reference list are quarantined as `unknown_service`. The process id is not part of the event identity, so two otherwise identical lines logged in the same millisecond by different processes collapse into one event.
* Common Log Format files carry no latency, so latency summaries are empty for them; response size is ignored.
* Only UTF-8 text is supported; other encodings are rejected line by line as `invalid_encoding`.
* Percentiles are per (service, hour) in UTC only; no other grouping dimensions (endpoint, status class).

## Possible next steps
Incremental summary refresh for only the affected hours; parallel parsing (multiprocessing) per file; Parquet/warehouse target (Postgres, BigQuery) behind the same `INSERT` boundary; hash or redact PII in quarantined lines; schema registry / data contract for producers; alerting on reject-rate thresholds; property-based tests for the timestamp parser.


## Adapter separation and safety hardening

Format-specific parsing is separated into `logpipe/adapters/` modules for JSON, key/value text, OpenStack/oslo and CLF. `parser.py` remains the stable dispatcher used by ingestion. Recursive file discovery excludes the configured output directory and database path so generated `.jsonl` reports cannot become next-run input. A failed reconciliation check now sets the run status to `failed`, in addition to returning a non-zero CLI exit code.


## Input mutation during ingestion
The pipeline hashes a file before reading it and verifies the hash again before committing its transaction. If a producer changes the file while it is being processed, the file transaction is rolled back and the run records a visible failure. This prevents the ingestion ledger from marking one content hash as processed when the pipeline may have read a different byte sequence. Producers should still publish files atomically (write to a temporary name, then rename) for the strongest hand-off contract.
