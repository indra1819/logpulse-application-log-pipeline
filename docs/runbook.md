# Runbook

## Prerequisites

- Python 3.9 or newer.
- No runtime third-party dependencies; SQLite is provided by Python's standard library.

## Run tests

```bash
python -m unittest discover -s tests -t . -v
```

## Run the committed sample

```bash
python -m logpipe run --input data/sample/input --db out/logpipe.db --out out --as-of 2026-11-07T00:00:00Z --labels data/sample/labels/expected_labels.json
```

## Run a real OpenStack dataset

Download the OpenStack logs from Loghub, extract the three `.log` files, and point `--input` at the extracted directory. Do not commit the raw dataset unless its current distribution terms have been reviewed.

```bash
python -m logpipe run --input data/real/openstack --db out/openstack.db --out out_openstack
```

## Retry and rerun behavior

- Re-running an unchanged source file skips it using its SHA-256 content hash.
- If a file fails during ingestion, its transaction is rolled back and the run report records the failure. Fix the underlying issue and rerun the file.
- If a source file changes, it has a new content hash and is eligible for ingestion. Event-level deterministic IDs still prevent duplicate event rows.
- `--force` re-reads a file even if its content hash is already known. It does not bypass event-level uniqueness.
- Summaries are refreshed from all stored events on every run, allowing late-arriving files to update previous time buckets.

## Verify a run

Check `run_report.json` for status, file counts, row counts, reject reasons, stage timings, throughput and reconciliation checks. A run is successful only when its status is `success` and every reconciliation check passes. Exit code 1 indicates a failed or partial run; exit code 2 indicates invalid CLI usage or arguments.

## Recovery notes

- Preserve the SQLite database and run reports before manual repair.
- Do not edit event or ledger tables directly during normal operation.
- For a corrupted database, stop writers, make a copy for diagnosis, and restore from a known-good backup or re-ingest source files into a fresh database.
- Rejected rows are retained for diagnosis. Correct the source or parser/validation rule, then re-run the source file; there is not yet a dedicated reject-replay command.
