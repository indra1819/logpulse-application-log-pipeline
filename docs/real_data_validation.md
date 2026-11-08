# Real and synthetic validation evidence

The historical figures below are retained where they describe earlier Windows/cloud runs. A fresh package verification was also performed on 2026-11-08 in Linux with Python 3.13.5. Timings are environment-specific and are not universal performance guarantees.

## Full real-world OpenStack dataset (Loghub)

Source archive supplied for this validation contained three files: `openstack_abnormal.log`, `openstack_normal1.log`, and `openstack_normal2.log`. The raw files are not copied into this repository.

Command:

```bash
python -m logpipe run --input PATH_TO_EXTRACTED_OPENSTACK --db out/openstack.db --out out_openstack
```

| Metric | Observed result |
|---|---:|
| Files discovered | 3 |
| Nonblank records read | 207,820 |
| Valid unique events inserted | 207,819 |
| Duplicate events quarantined | 1 |
| Invalid records rejected | 0 |
| Failed files | 0 |
| Pipeline status | `success` |
| Reconciliation | 7 / 7 passed |
| Earlier recorded pipeline duration | 15.685 seconds |
| Latest Windows Python 3.14.3 run (2026-11-08) | 33.198 seconds |
| Ingestion throughput | 13,246 records/second |

The single duplicate was detected by deterministic event identity. Zero invalid rejects means none of the nonblank records violated the configured validation rules; it does not mean the dataset contains no operational errors. The dataset includes abnormal/failure-injection logs and the analytics classify error-level and HTTP 5xx events separately from validation rejects.

### Idempotency rerun

The same three files were run again against the same database. All three files were skipped by their SHA-256 content hashes; 0 additional records were read or inserted. Reconciliation still passed. This verifies unchanged-file rerun behavior for this dataset. The independent build repeated this check successfully (0 records re-read; 3 files skipped).

## Synthetic edge-case / scale validation

Synthetic data is explicitly test-only and was generated with seed `7`; it is not presented as real production data.

```bash
python -m logpipe generate --rows 100000 --seed 7 --files 4 --out-dir synthetic_input --labels-dir synthetic_labels
python -m logpipe run --input synthetic_input --db synthetic.db --out synthetic_out --as-of 2026-11-07T00:00:00Z --labels synthetic_labels/expected_labels.json
```

| Metric | Observed result |
|---|---:|
| Files | 4 |
| Records read | 107,200 |
| Valid events loaded | 100,000 |
| Duplicates | 2,000 |
| Invalid records rejected | 5,200 |
| Pipeline status | `success` |
| Reconciliation | 10 / 10 passed |
| Earlier recorded pipeline duration | 8.673 seconds |
| Latest Windows Python 3.14.3 run (2026-11-08) | 14.94 seconds |
| Ingestion throughput | 13,131 records/second |
| Latest Windows Python 3.14.3 ingestion throughput (2026-11-08) | 8,273 records/second |

All expected labels matched actual loaded, duplicate and reject-reason counts. The synthetic run report measured 12,000+ records/second at the time of execution; exact ingestion throughput is recorded in the generated `run_report.json`.

## Automated test suite

```bash
python -m unittest discover -s tests -t . -v
```

Fresh package verification on 2026-11-08: **97 tests passed**, with zero failures and zero errors, on Linux with Python 3.13.5. The complete verbose output is recorded in `docs/test_results.txt`.

## Interpretation and limitations

- The full Loghub OpenStack dataset is the primary real-world validation source.
- Synthetic data adds controlled invalid/duplicate/late/out-of-order/schema-drift cases that are not reliably labelled in the real logs.
- NASA/Apache Common Log Format parsing has unit and integration test coverage, but a full NASA archive run has not been executed in this validation session. Do not claim otherwise.
- Performance values above are environment-specific observations, not production SLAs.

### Independent review build: input mutation guard

The independent review build verifies the input file SHA-256 both before and after ingestion, before committing the file transaction. If the source changes while it is being read, the file is rolled back and is not marked as ingested. A regression test mutates a file during ingestion, confirms zero partial rows and no ingested-file ledger entry, then retries and loads the complete updated file successfully.
