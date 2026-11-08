# LogPulse — Application Log Processing & Analytics Pipeline

LogPulse is a locally executable application log processing and analytics pipeline. It parses structured JSON and semi-structured text logs, normalizes timestamps and severity levels, extracts important fields, and generates error-rate and latency summaries.

The pipeline supports multiple log formats, detects duplicate events, validates records, quarantines invalid data with source-file and line-number traceability, and produces measurable processing and data-quality reports.

**Built for the YottaFlex.ai AI Data Forward Deployed Engineer 72-hour challenge.**

Standard library only — no third-party dependencies required.

---

## Quick Start

**Requirements:** Python 3.9+

The project was locally tested on Windows using Python 3.14. Commands below use standard Python module execution.

If `python` is not recognized, use `py` on Windows or `python3` on macOS/Linux.

### 1. Run the automated tests

```bash
python -m unittest discover -s tests -t . -v
```
**Latest recorded Windows test result: 97 tests passed, with zero failures and zero errors, in 7.267 seconds using Python 3.14.3.**

### 2. Run the pipeline on the committed sample

```bash
python -m logpipe run --input data/sample/input --db out/logpipe.db --out out --as-of 2026-11-07T00:00:00Z --labels data/sample/labels/expected_labels.json
```

### 3. Query the results

```bash
python -m logpipe query --db out/logpipe.db --file docs/sample_queries.sql
```

Run the integrity verification queries:

```bash
python -m logpipe query --db out/logpipe.db --file docs/verification_queries.sql
```

The verification queries contain 10 integrity checks. Each check should return 0 on the validated sample.

Generated outputs are written to `out/`, including:

- `run_report.json`
- `reconciliation_report.md`
- `clean_events.csv`
- `rejected_records.csv`
- `service_hourly_summary.csv`
- `service_overall_summary.csv`
- `rejects.jsonl`
- `pipeline.log`

Committed sample outputs are available in `sample_output/`.

The `--as-of` option fixes the reference time used by future-timestamp validation, making results reproducible across different execution dates. Omit it to use the current time.

---

## Larger Synthetic Dataset

Generate a reproducible dataset containing 100,000 valid events:

```bash
python -m logpipe generate --rows 100000 --seed 7 --files 4 --out-dir data/generated/input --labels-dir data/generated/labels
```

Run the pipeline:

```bash
python -m logpipe run --input data/generated/input --db out/big.db --out out_big --as-of 2026-11-07T00:00:00Z --labels data/generated/labels/expected_labels.json
```

Run the performance benchmark:

```bash
python scripts/benchmark.py --rows 100000 --repeat 3
```

The generator creates controlled data-quality scenarios, including:

- Approximately 2% duplicate records.
- Invalid records across 13 data-quality categories.
- Blank lines and mixed log formats.
- Different timestamp formats.
- Schema-drifted field names.
- Out-of-order and late-arriving records.
- Windows line endings.

A fixed random seed produces reproducible output.

**Expected-answer labels are written to a separate directory** and are never mixed with input data.

---

## Real-World Dataset Validation

Two public real-world datasets are supported. The raw datasets are not committed to this repository. Download them separately before running these examples.

### A. OpenStack Infrastructure Logs — Loghub

Loghub publishes real-world OpenStack infrastructure logs containing service events, severity levels, and HTTP request information.

Dataset source: [Loghub](https://github.com/logpai/loghub)

The OpenStack dataset contains approximately 207,820 log lines.

Citation:

Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023.

Download the OpenStack dataset from the Loghub repository and extract the files.

Example execution:

```bash
python -m logpipe run --input data/real/openstack_normal1.log --db out/openstack.db --out out_openstack
```

Query the highest-latency service-hour combinations:

```bash
python -m logpipe query --db out/openstack.db --sql "SELECT * FROM service_hourly_summary ORDER BY latency_p95_ms DESC LIMIT 10"
```

Use the actual extracted filename. The pipeline accepts either individual files or directories.

Example OpenStack log line:

```text
2017-06-29 12:59:37.444 76685 INFO nova.osapi_compute.wsgi.server [req-...] 10.0.0.7 "GET /v2/ HTTP/1.1" status: 200 len: 499 time: 0.0013
```

The adapter extracts:

| Source information | Normalized field |
|---|---|
| Log severity | `level` |
| Loghub filename prefix | `service` |
| Full logger name | `component` |
| Request ID | `request_id` |
| Request path | `endpoint` |
| HTTP response status | `http_status` |
| Client address | `client_host` |
| Request time in seconds | `latency_ms` |

The filename prefix identifies services such as `nova-api`, `nova-compute`, and `nova-scheduler`.

Only HTTP request lines contain latency information. Other service events still contribute to error-rate calculations.

Stack-trace continuation lines without timestamps are quarantined as malformed records.

Services not present in `data/reference/services.csv` are rejected as `unknown_service` until the reference data is updated.

### Real OpenStack 2K Sample Result

A 2,000-line real-world OpenStack sample was validated. The raw file is not included, but the processed outputs are committed in `real_data_runs/openstack_loghub_2k/`.

| Metric | Result |
|---|---:|
| Records read | 2,000 |
| Records loaded | 2,000 |
| Duplicate records | 0 |
| Rejected records | 0 |
| Reconciliation | PASS |

**Service-level results:**

| Service | Events | Events with latency | Average latency |
|---|---:|---:|---:|
| nova-api | 1,060 | 1,017 | 234.5 ms |
| nova-compute | 933 | N/A | N/A |
| nova-scheduler | 7 | N/A | N/A |

**Additional observations:**

- 1,969 INFO events.
- 31 WARNING events.
- Error rate: 0.0 for this sample.
- All reconciliation checks passed.

**Latency availability:** Latency metrics are calculated only for events containing valid request-duration values. In this dataset, HTTP request lines contain the `time:` field, while the displayed `nova-compute` and `nova-scheduler` events do not contain latency measurements. Therefore, their latency values are reported as **N/A (Not Available)** rather than zero. N/A does not mean zero latency.

This sample represents normal operation and contains no ERROR-level events or HTTP 5xx responses.

For non-zero error rates and additional failure scenarios, use the larger OpenStack dataset, including `openstack_abnormal.log`.

---

### B. NASA Kennedy Space Center Web Server Logs

The pipeline also supports Apache Common Log Format (CLF) web-server traces.

Dataset source: [NASA HTTP Dataset — Internet Traffic Archive](https://ita.lbl.gov/html/contrib/NASA-HTTP.html)

Example dataset: `NASA_access_log_Jul95.gz`

The dataset contains approximately 1.9 million lines.

Run the pipeline:

```bash
python -m logpipe run --input data/real/NASA_access_log_Jul95.gz --clf-service nasa_web --db out/nasa.db --out out_nasa
```

The `.gz` file is read directly.

Common Log Format does not contain an explicit severity, service, or latency field. Therefore:

- HTTP 5xx status codes are classified as ERROR.
- HTTP 4xx status codes are classified as WARNING.
- Other successful responses are classified as INFO.
- The service name is supplied through `--clf-service`.
- Latency fields remain N/A because the source does not provide request duration.

Damaged or truncated lines are quarantined with their source filename and line number.

---

## Reliability and Verification

The pipeline includes safeguards to maintain data integrity during ingestion and repeated execution.

### Input and Output Isolation

- Configured output directories and database paths are excluded from recursive input discovery.
- Generated reports are not accidentally re-ingested during subsequent runs.

### Reconciliation

- Every run produces a reconciliation report.
- Failed reconciliation marks the run as `failed`.
- A failed run returns a non-zero CLI exit code.

### Idempotency

- Every normalized event receives a deterministic `event_id`.
- The event ID is used as the primary key.
- Duplicate events are not inserted again.
- Repeated runs do not create duplicate database records.

### Transaction Safety

- Each file is processed in a transaction.
- If a file fails during ingestion, its transaction is rolled back.
- Other files can continue processing.
- Failed files are recorded with their failure reasons.

### Retry and Recovery

- Previously processed files are tracked using content hashes.
- Unchanged files can be skipped during subsequent runs.
- Failed files can be retried.
- The `--force` option reprocesses input files.
- Late-arriving data can be added and processed through a subsequent run.

For operational guidance, see `docs/runbook.md`.

---

## What the Pipeline Does

### Supported Log Formats

- JSON Lines.
- Structured key-value text logs.
- OpenStack/oslo logs.
- Apache Common Log Format.
- Gzip-compressed input files.

Multiple supported formats can be processed through the adapter-based parsing architecture.

### Timestamp Normalization

Supported timestamp formats include:

- ISO-8601 timestamps.
- Timestamps with timezone offsets.
- Epoch seconds.
- Epoch milliseconds.
- Apache-style timestamps.

All timestamps are normalized to UTC with millisecond precision.

Timestamps without an explicit timezone are treated as UTC.

### Severity Normalization

Different severity spellings are mapped to standard levels:

| Input values | Normalized severity |
|---|---|
| TRACE, DEBUG, DBG | DEBUG |
| INFO, INFORMATION, NOTICE, AUDIT | INFO |
| WARN, WARNING | WARNING |
| ERR, ERROR, SEVERE | ERROR |
| CRIT, CRITICAL, FATAL, EMERG, ALERT | CRITICAL |

### Field Extraction

The pipeline extracts and normalizes fields such as:

- `timestamp`
- `level`
- `service`
- `component`
- `endpoint`
- `http_status`
- `latency_ms`
- `request_id`
- `message`
- `source_file`
- `line_no`

Aliases such as `ts`, `severity`, `svc`, and `duration_s` are supported.

Duration values supplied in seconds are converted into milliseconds.

### Validation Rules

The pipeline validates:

- Required timestamp, severity, and service fields.
- Service names against the reference dataset.
- HTTP status codes between 100 and 599.
- Latency values between 0 and 3,600,000 milliseconds.
- Timestamps that are not more than 24 hours in the future.

Invalid records are rejected with a specific reason.

---

## Analytics

### Error Rate

An event is considered an error when:

- Its normalized severity is ERROR or CRITICAL, or
- Its HTTP status code is 500 or higher.

Error rate:

`Error Rate = Error Events / Total Events`

The pipeline calculates error rates per service per UTC hour and overall.

### Latency Analysis

Latency summaries include:

- Count
- Average
- Minimum
- Maximum
- P50
- P95
- P99

Percentiles use the nearest-rank method.

Latency summaries are calculated only for events that contain valid latency values. If a service has no latency measurements, its latency metrics are reported as **N/A**, not zero.

---

## Reliability: Idempotency, Retry, Restart and Backfill

The pipeline uses deterministic event identifiers to prevent duplicate database records.

- Reprocessing the same event does not create another event.
- Duplicate records are recorded separately.
- Files are tracked through content hashes.
- File-level transactions protect previously committed data.
- Failed files can be retried.
- The `--force` option reprocesses previously processed files.
- Summaries are rebuilt from the stored events, allowing late-arriving data to correct earlier time periods.

If a previously rejected record becomes valid after a reference-data correction, use `--force` to reprocess the unchanged source file.

The pipeline also removes quarantine entries for records that are successfully loaded after reprocessing.

Relevant test coverage includes `IdempotencyTests`, `PartialFailureTests`, and `OrderAndLatenessTests`.

---

## Observability

Each run records operational metrics, including:

- `run_id`
- Start and end time.
- Input files processed.
- Records read.
- Records loaded.
- Duplicate records.
- Rejected records.
- Rejection reasons.
- Failed files and failure reasons.
- Stage execution timings.
- Processing throughput.

These metrics are stored in the `runs` and `run_files` database tables and published through:

- `run_report.json`
- `pipeline.log`
- `reconciliation_report.md`

---

## Validation and Reconciliation

The pipeline validates its processing results using row-count reconciliation and database integrity checks.

### Sample Dataset Result

For the committed synthetic sample generated with seed 42:

```text
status=success
files=3
read=319
loaded=300
duplicate=6
rejected=13
reconciliation=PASS
```

### Reconciliation Checks

| Check | Expected result |
|---|---|
| Rows read = loaded + duplicate + replayed + rejected | PASS |
| Database event count matches run ledger | PASS |
| Event IDs are unique | PASS |
| Every service exists in the reference table | PASS |
| Every event has source-file and line-number traceability | PASS |
| Summary event and error counts match stored events | PASS |
| Loaded, duplicate and rejected counts match expected labels | PASS |

The sample contains:

- 300 loaded events.
- 6 duplicate records.
- 13 rejected records.

Integrity checks are available in `docs/verification_queries.sql`.

The SQL checks cover orphan keys, repeated event IDs, duplicate quarantine records, unexplained rows, summary totals, error rates, latency percentiles, and timestamp formatting.

All ten checks are expected to return 0 on the validated sample and 100,000-event synthetic run.

---

## Published Outputs

| File | Description |
|---|---|
| `clean_events.csv` | Accepted normalized events with lineage information |
| `rejected_records.csv` | Invalid and duplicate records with rejection details |
| `service_hourly_summary.csv` | Service-wise hourly analytics |
| `service_overall_summary.csv` | Overall service analytics |
| `rejects.jsonl` | Rejected records in JSON Lines format |
| `run_report.json` | Run-level metrics and processing details |
| `reconciliation_report.md` | Data-integrity and reconciliation results |
| `pipeline.log` | Operational execution log |

Rejected records retain their source filename, line number, rejection reason, and raw input.

The CSV outputs describe the complete stored dataset, rather than only the latest run.

---

## Testing

Run the complete test suite:

```bash
python -m unittest discover -s tests -t . -v
```

**Latest recorded Windows verification: 97 tests passed with zero failures and zero errors in 7.267 seconds on Python 3.14.3.**

The test suite covers:

- Successful ingestion and analytics.
- Empty input and missing files.
- Malformed JSON and text records.
- Invalid UTF-8 and Windows line endings.
- Missing required fields and null values.
- Duplicate detection.
- Schema drift and field aliases.
- Unknown services and invalid reference keys.
- Partial failures and retry behaviour.
- File-level transaction rollback.
- Repeated runs and forced reprocessing.
- Out-of-order and late-arriving records.
- Timestamp offsets and epoch conversions.
- Latency boundaries and percentile calculations.
- Deterministic synthetic data generation.
- OpenStack and Common Log Format adapters.
- Gzip input and corrupt gzip handling.
- CLI commands and error handling.

`tests/test_robustness.py` also includes seeded fuzz testing to verify that unexpected and malformed input is handled without crashing the pipeline.

### Test Coverage by Requirement

| Scenario | Test coverage |
|---|---|
| Successful processing | `HappyPathTests` |
| Empty and missing input | `EmptyAndMissingInputTests` |
| Malformed records | `RejectTests`, `ParserTests` |
| Missing fields and nulls | `ValidateTests` |
| Duplicate events | `DuplicateTests` |
| Schema drift | `SchemaDriftTests` |
| Unknown services | `RejectTests`, `ValidateTests` |
| Partial failure and retry | `PartialFailureTests` |
| Repeated runs | `IdempotencyTests` |
| Late-arriving data | `OrderAndLatenessTests` |
| Timestamp and latency edge cases | `TimestampTests`, `LatencyEdgeCaseTests` |
| Synthetic data generation | `GeneratorTests` |
| OpenStack logs | `tests/test_openstack.py` |
| Apache CLF and gzip logs | `tests/test_clf.py` |
| CLI behaviour | `tests/test_cli.py` |
| Robustness and fuzz testing | `tests/test_robustness.py` |

Optional coverage reporting requires the `coverage` package:

```bash
coverage run --source=logpipe -m unittest discover -s tests -t .
coverage report -m
```

---

## Performance

The following execution measurements were recorded during local validation.

| Dataset | Records read | Execution time | Approx. throughput |
|---|---:|---:|---:|
| Full OpenStack dataset | 207,820 | 32.126 seconds | 6,469 records/sec |
| Synthetic dataset | 107,200 | 15.059 seconds | 7,119 records/sec |

These measurements are environment-specific and are not performance guarantees.

Run `scripts/benchmark.py` to measure performance on another machine.

---

## Full OpenStack Dataset Execution

The full OpenStack dataset was processed using three input files. A separate fresh package verification on Linux/Python 3.13.5 is recorded in `docs/package_verification.md`.

| Metric | Result |
|---|---:|
| Input files | 3 |
| Records read | 207,820 |
| Records loaded | 207,819 |
| Duplicate records | 1 |
| Rejected records | 0 |
| Failed files | 0 |
| Execution time (earlier Windows run) | 32.126 seconds |
| Reconciliation | PASS |

### Data Integrity Validation

| Validation | Result |
|---|---|
| Store matches run ledger | PASS |
| Event ID uniqueness | PASS |
| Referential integrity | PASS |
| Source traceability | PASS |
| Summary event count | PASS |
| Summary error count | PASS |
| Published CSV matches store | PASS |

### Service-wise Error Analysis

| Service | Events | Errors | Error Rate |
|---|---:|---:|---:|
| nova-api | 102,868 | 6 | 0.01% |
| nova-compute | 104,085 | 192 | 0.18% |
| nova-scheduler | 866 | 0 | 0.00% |

### Latency Analysis

Service: `nova-api`

| Metric | Latency |
|---|---:|
| Average | 234.051 ms |
| Minimum | 0.537 ms |
| P50 | 259.578 ms |
| P95 | 381.820 ms |
| P99 | 514.391 ms |

Other services without request-duration measurements have latency metrics reported as N/A.

### Failure Handling

- Failed files: 0
- Duplicate records handled: 1
- Rejected records: 0
- Reconciliation: PASS
- Data integrity checks: All PASS

---

## Synthetic Dataset Testing

A controlled synthetic dataset was generated to validate duplicate detection, malformed-record handling, validation rules, and rejection handling.

| Metric | Result |
|---|---:|
| Valid events generated | 100,000 |
| Total records read | 107,200 |
| Records loaded | 100,000 |
| Duplicate records | 2,000 |
| Rejected records | 5,200 |
| Failed files | 0 |
| Execution time | 15.059 seconds |
| Reconciliation | PASS |

**Validation:** The pipeline processed valid records, detected duplicates, rejected invalid records, and passed reconciliation.

### Synthetic Dataset Latency Analysis

| Service | Average (ms) | P50 (ms) | P95 (ms) | P99 (ms) |
|---|---:|---:|---:|---:|
| auth | 9560.588 | 67 | 331 | 672 |
| inventory | 6880.428 | 90 | 419 | 850 |
| notifications | 6853.758 | 205 | 997 | 1943 |
| orders | 7451.643 | 158 | 783 | 1489 |
| payments | 8472.004 | 251 | 1208 | 2368 |
| search | 7629.378 | 102 | 484 | 944 |

### Testing Approach

1. Validate ingestion and analytics using real-world OpenStack logs.
2. Generate synthetic data with controlled data-quality issues.
3. Test duplicate detection and invalid-record handling.
4. Verify record counts and data integrity through reconciliation.

---

## Commands

| Command | Purpose |
|---|---|
| `python -m logpipe run --input PATH` | Process a file or directory |
| `python -m logpipe generate --rows N --seed S --out-dir D --labels-dir L` | Generate synthetic input and separate expected labels |
| `python -m logpipe query --db DB --sql "..."` | Execute read-only SQL |
| `python -m logpipe query --db DB --file F.sql` | Execute SQL from a file |

The `run` command supports options including:

`--db`, `--out`, `--services`, `--clf-service`, `--run-id`, `--as-of`, `--force`, `--labels`, and `-v`.

### Exit Codes

| Exit code | Meaning |
|---|---|
| `0` | Successful run with reconciliation checks passing |
| `1` | Partial failure, failed run, or failed reconciliation |
| `2` | Invalid command-line arguments |

---

## Architecture

```text
                 INPUT LOG FILES
                        |
                        v
                 PARSER ADAPTERS
           JSON / Text / OpenStack / CLF
                        |
                        v
                  VALIDATION
            Field aliases and validation
                Service reference
                        |
                        v
                 SQLITE STORAGE
              log_events database
                        |
             +----------+----------+
             |                     |
             v                     v
       Duplicate events       Invalid events
             |                     |
             +----------+----------+
                        |
                        v
                ANALYTICS ENGINE
              build_summaries()
                        |
                        v
              SERVICE SUMMARIES
          Hourly and overall summaries
                        |
                        v
                RECONCILIATION
                        |
                        v
                 PUBLISHED OUTPUTS
        CSV / JSON / Markdown / Pipeline logs
```

Detailed architecture: `docs/architecture.md`

Operational guidance: `docs/runbook.md`

Requirements mapping: `docs/requirements_traceability.md`

Real-data validation: `docs/real_data_validation.md`

Design decisions and limitations: `docs/design_notes.md`

Database schemas and rejection reasons: `docs/data_dictionary.md`

---

## Known Limitations and Future Improvements

Current design limitations:

- Batch processing rather than real-time streaming.
- SQLite single-writer architecture.
- Full-refresh summary generation.
- Raw rejected records may contain sensitive information.

Potential future improvements:

- Incremental summary updates.
- Parallel log parsing.
- Data warehouse integration.
- PII redaction.
- Automated rejection-rate alerts.

---

## Repository Structure

```text
logpipe/
    adapters/
        json_adapter.py
        text_adapter.py
        openstack_adapter.py
        clf_adapter.py
    cli.py
    config.py
    db.py
    errors.py
    generator.py
    normalize.py
    parser.py
    pipeline.py
    reconcile.py
    report.py
    validate.py

tests/
    test_cli.py
    test_clf.py
    test_generator.py
    test_normalize.py
    test_openstack.py
    test_parser_validate.py
    test_pipeline.py
    test_report.py
    test_robustness.py

data/
    reference/
        services.csv
    sample/
        input/
        labels/

sample_output/
real_data_runs/
docs/
scripts/
ai_usage/
.github/
    workflows/
```

---

## Challenge and Project Information

**Project:** LogPulse — Application Log Processing & Analytics Pipeline

**Challenge:** YottaFlex.ai — AI Data Forward Deployed Engineer, 72-hour challenge.

The project focuses on reliable ingestion, normalization, validation, analytics, data-quality handling, reconciliation, and measurable outputs using real-world and synthetic application logs.