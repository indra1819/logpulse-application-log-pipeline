# Data dictionary

All names are `snake_case`. All timestamps are ISO-8601 UTC strings with millisecond precision
(`2026-11-01T10:00:00.123Z`); hour buckets look like `2026-11-01T10:00:00Z`.

## Source (input)

Plain-text or gzip files (`.log`, `.jsonl`, `.json`, `.txt`, `.gz`) with one record per line. Four layouts may be mixed in one file:

| layout | example |
|---|---|
| JSON object | `{"timestamp":"2026-11-01T10:00:00Z","level":"ERROR","service":"auth","status":500,"latency_ms":120,"request_id":"r1","message":"boom"}` |
| text | `2026-11-01 10:00:00,123 ERROR [auth] request_id=r1 endpoint=/login status=500 latency_ms=120 msg="boom"` |
| OpenStack / oslo (e.g. Loghub) | `2017-06-29 12:59:37.444 76685 INFO nova.osapi_compute.wsgi.server [req-4ab7... - - -] 10.0.0.7 "GET /v2/ HTTP/1.1" status: 200 len: 499 time: 0.0013` |
| Common Log Format (Apache / NASA-HTTP) | `199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /history/apollo/ HTTP/1.0" 200 6245` |

OpenStack lines: `service` = the source-file prefix when present (Loghub: `nova-api.log.1.2017-05-16_13:53:08 ...` gives `nova-api`), otherwise the first segment of the logger name (lower-cased); `component` = full logger name, `request_id` = the `req-...` id, `time:` is seconds and is converted to `latency_ms`, the process id is ignored. Lines without a timestamp (stack-trace continuations) are rejected as `malformed_line`.

Common Log Format has no level or service. With `--clf-service <name>` the service is set to that name (it must exist in the
services file), `level` is derived from the status (5xx -> ERROR, 4xx -> WARNING, otherwise INFO), `client_host` is the first column,
`endpoint` is the path of the request, `message` is the request line, and the response size is ignored. CLF has no latency, so
latency columns are empty for such data. Without `--clf-service` these lines are rejected as `missing_required_field:service`.

Accepted spellings (schema drift tolerance; keys are case-insensitive, first match wins):

| canonical field | accepted names | required |
|---|---|---|
| timestamp | `timestamp`, `ts`, `time`, `@timestamp`, `datetime` | yes |
| level | `level`, `severity`, `lvl`, `log_level`, `loglevel` | yes |
| service | `service`, `svc`, `service_name`, `app`, `application` | yes |
| endpoint | `endpoint`, `path`, `route`, `url` | no |
| status | `status`, `status_code`, `http_status`, `http_code` | no |
| latency | `latency_ms`, `duration_ms`, `elapsed_ms`, `response_time_ms` (ms); `latency_s`, `duration_s` (seconds, converted to ms) | no |
| request_id | `request_id`, `req_id`, `trace_id`, `correlation_id` | no |
| client_host | `client_host`, `remote_addr`, `client_ip`, `remote_host` | no |
| component | `component`, `logger`, `logger_name` | no |
| message | `message`, `msg` | no |

Timestamps may be ISO-8601 (`T` or space, `.`/`,` fraction, `Z`/offset), epoch seconds, epoch milliseconds
(values >= 1e11 are milliseconds), or Apache style `01/Nov/2026:10:00:00 +0530`. No offset = UTC.
Years outside 1990-2100 are invalid. Severity spellings are mapped to `DEBUG, INFO, WARNING, ERROR, CRITICAL`
(e.g. `warn`->WARNING, `err`/`severe`->ERROR, `fatal`/`crit`->CRITICAL, `trace`->DEBUG, `audit`->INFO).

## Target (SQLite database `logpipe.db`)

### `log_events` - one row per unique valid event. Grain: one log event.
| column | type | null | notes |
|---|---|---|---|
| event_id | TEXT | no | **PK.** `evt_` + first 24 hex chars of SHA-256 over the normalized content `[event_ts, level, service, endpoint, http_status, latency_ms, request_id, client_host, component, message]`. Same event in any layout/timezone gives the same id |
| event_ts | TEXT | no | normalized UTC timestamp |
| event_hour | TEXT | no | derived: UTC hour bucket |
| level | TEXT | no | one of DEBUG/INFO/WARNING/ERROR/CRITICAL (CHECK constraint) |
| service | TEXT | no | lower-cased; **FK** to `dim_service.service` |
| endpoint | TEXT | yes | |
| http_status | INTEGER | yes | 100-599 (CHECK) |
| latency_ms | REAL | yes | >= 0 and <= `max_latency_ms` (3,600,000); seconds inputs converted and rounded to 3 decimals |
| request_id | TEXT | yes | |
| client_host | TEXT | yes | client address/host (CLF/OpenStack logs; part of `event_id`) |
| component | TEXT | yes | full logger name (OpenStack logs; part of `event_id`) |
| message | TEXT | yes | trimmed, max 2000 chars |
| is_error | INTEGER | no | derived: `1` if level is ERROR/CRITICAL **or** http_status >= 500 |
| is_out_of_order | INTEGER | no | derived: `1` if the line's timestamp was more than 300 s older than the newest timestamp already seen earlier in the same file. Informational only, row is still loaded |
| source_file | TEXT | no | path relative to the input directory |
| line_no | INTEGER | no | 1-based line number in `source_file` |
| run_id | TEXT | no | run that first loaded the row |
| ingested_at | TEXT | no | |

### `rejected_records` - quarantine. Grain: one rejected/duplicate source line.
PK `(source_file, line_no, raw_sha256)`. Columns: `reject_type` (`invalid` or `duplicate`), `reject_reason`,
`raw_line` (original text, undecodable bytes replaced), `run_id`, `rejected_at`.

Reject reasons (first failing rule wins, in this order):

| reason | meaning |
|---|---|
| `invalid_encoding` | line is not valid UTF-8 |
| `malformed_json` | line starts with `{` but is not a valid JSON object |
| `malformed_line` | neither JSON nor the text layout |
| `missing_required_field:<name>` | `timestamp`, `level` or `service` absent, null or blank |
| `invalid_timestamp` | unparseable, or year outside 1990-2100 |
| `future_timestamp` | later than `as_of` + 24 h |
| `invalid_severity` | not a known level spelling |
| `unknown_service` | service is not in `dim_service` (referential check) |
| `invalid_status` | not an integer in 100-599 |
| `invalid_latency` | non-numeric, NaN or negative |
| `latency_out_of_range` | greater than 3,600,000 ms |
| `duplicate_event` (type `duplicate`) | an event with the same `event_id` was already loaded from a different source line |

### `dim_service` - reference list loaded from `data/reference/services.csv`
`service` (PK), `owner_team`.

### `service_hourly_summary` - published report. Grain: service x UTC hour. Fully rebuilt every run.
| column | definition |
|---|---|
| service, event_hour | PK |
| event_count | events in the group |
| error_count / error_rate | events with `is_error = 1`; `error_rate = error_count / event_count` (4 decimals) |
| latency_count | events that have a latency value |
| latency_avg_ms, latency_min_ms, latency_max_ms | over events with latency |
| latency_p50_ms, latency_p95_ms, latency_p99_ms | **nearest-rank** percentile: sorted values, rank = ceil(p/100 * n) |

`service_overall_summary.csv` has the same columns without `event_hour` (one row per service).

### Run tracking
* `runs` - one row per run: `run_id` (PK), `started_at`, `ended_at`, `status` (`running`, `success`, `partial_failure`, `failed`), file/row counters, `failure_reason`. A row left in `running` means the process was killed.
* `run_files` - one row per file per run: `status` (`success`, `skipped_already_ingested`, `failed`), `rows_read`, `rows_loaded`, `rows_duplicate`, `rows_replayed`, `rows_rejected`, `blank_lines`, `failure_reason`.
  Invariant: `rows_read = rows_loaded + rows_duplicate + rows_replayed + rows_rejected` (blank lines are not counted as rows).
* `ingested_files` - `sha256` (PK) of every fully loaded file; used to skip unchanged files.

### Output files (`--out`, default `out/`)
`run_report.json` (metrics + per-file results + reconciliation), `reconciliation_report.md`, `service_hourly_summary.csv`,
`service_overall_summary.csv`, `rejects.jsonl` (the whole quarantine), `pipeline.log` (every line carries `run_id`).
`clean_events.csv` and `rejected_records.csv` are the accepted / rejected record files:

* `clean_events.csv` - the whole `log_events` table, ordered by `event_ts, event_id`. Columns: `event_id, event_ts, event_hour, level, service, endpoint, http_status, latency_ms, request_id, client_host, component, message, is_error, is_out_of_order, source_file, line_no, run_id, ingested_at` (empty cell = NULL).
* `rejected_records.csv` - the whole `rejected_records` table, ordered by `source_file, line_no, reject_type`. Columns: `source_file, line_no, reject_type` (`invalid` or `duplicate`)`, reject_reason, raw_line, raw_sha256, run_id, rejected_at`. For a duplicate, `source_file`/`line_no` point at the repeated line; the surviving event is the one in `clean_events.csv` with the same content hash.
* Both are written with the standard CSV quoting rules (commas, quotes and line breaks inside `raw_line` or `message` are quoted), UTF-8, `\n` line ends.

## Naming conventions
`snake_case` tables/columns; `run_id` = `run_<UTC yyyymmddThhmmssZ>_<8 hex>` (or `--run-id`); `event_id` = `evt_<24 hex>`;
`source_file` + `line_no` on every row for traceability; counters are `rows_*`; booleans are `is_*` stored as 0/1.
