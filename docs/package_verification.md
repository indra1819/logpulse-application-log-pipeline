# Fresh Package Verification

Date: 2026-11-08
Environment: Linux x86_64, Python 3.13.5

This record documents commands run against the packaged project during final preparation. Execution times are environment-specific.

## Automated tests

Command:

```bash
python -m unittest discover -s tests -t . -v
```

Result: **97 tests passed, 0 failures, 0 errors**.
Duration: **0.858 seconds**.
Full verbose output: [`test_results.txt`](test_results.txt).

Report-specific tests:

```bash
python -m unittest tests.test_report -v
```

Result: **10 tests passed**.

## CLI

Command:

```bash
python -m logpipe --help
```

Result: Exit code 0; command usage and the `run`, `query`, and `generate` subcommands were displayed.

## Sample dataset

Command used the committed `data/sample/input` files, a fresh temporary SQLite database, and the committed expected labels.

Result: **319 read, 300 loaded, 6 duplicates, 13 rejected, 0 failed files; reconciliation PASS**.

## Synthetic 100,000-event dataset

Generated with seed 7 and four files, then processed with a fresh temporary database.

Result: **107,200 read, 100,000 loaded, 2,000 duplicates, 5,200 rejected, 0 failed files; reconciliation PASS**.
Observed duration: **7.359 seconds**.

## Real OpenStack dataset

Processed the three `.log` files in `data/openstack_logs_only` (the separate `anomaly_labels.txt` file was intentionally excluded from the input directory).

Result: **207,820 read, 207,819 loaded, 1 duplicate, 0 rejected, 0 failed files; reconciliation PASS**.
Observed duration: **15.479 seconds**.

## Environment note

The Python startup environment emitted a non-fatal spreadsheet-runtime warmup traceback before CLI output in this execution container. The CLI commands returned exit code 0 and the pipeline runs completed successfully. This startup message is specific to the execution environment and is not emitted by LogPulse itself.

## SQL integrity verification

Executed `docs/verification_queries.sql` against the fresh sample, synthetic, and OpenStack databases. All 10 checks returned zero violations for each database:

- orphan service keys
- repeated event IDs
- lines both loaded and rejected as invalid
- unexplained run-file rows
- hourly summary event-count mismatch
- hourly summary error-count mismatch
- inconsistent error rates
- out-of-order latency percentiles
- error-classification rule violations
- non-ISO-8601 UTC timestamps

Command:

```bash
python -m logpipe query --db PATH_TO_DATABASE --file docs/verification_queries.sql
```
