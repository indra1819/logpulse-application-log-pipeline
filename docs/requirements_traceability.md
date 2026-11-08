# Challenge requirements traceability

| Requirement area | Implementation | Evidence / verification |
|---|---|---|
| Structured and semi-structured log ingestion | `logpipe/adapters/`, `logpipe/parser.py`, `logpipe/pipeline.py` | `tests/test_parser_validate.py`, `tests/test_openstack.py`, `tests/test_clf.py` |
| Timestamp and severity normalization | `logpipe/normalize.py` | `tests/test_normalize.py` |
| Field extraction and validation | `logpipe/adapters/`, `logpipe/parser.py`, `logpipe/validate.py` | `tests/test_parser_validate.py` |
| Error-rate and latency summaries | `logpipe/report.py` | `tests/test_report.py`, `tests/test_openstack.py` |
| Durable target storage | `logpipe/db.py` | `tests/test_pipeline.py` |
| Invalid-record traceability | `logpipe/pipeline.py`, `logpipe/db.py` | `tests/test_pipeline.py`, `tests/test_clf.py` |
| Deterministic IDs and duplicate handling | `logpipe/validate.py`, `logpipe/db.py` | `test_event_id_is_deterministic_and_layout_independent`, duplicate tests |
| Idempotent reruns | `logpipe/pipeline.py` | `test_rerun_of_same_input_changes_nothing`, `test_forced_reprocessing_does_not_duplicate_or_misreport` |
| Partial failure and retry | `logpipe/pipeline.py` | `test_failed_file_is_isolated_then_loaded_on_retry`, `test_mid_file_crash_rolls_back_the_whole_file` |
| Late/out-of-order data | `logpipe/pipeline.py`, `logpipe/report.py` | `OrderAndLatenessTests` |
| Reconciliation and integrity | `logpipe/reconcile.py` | reconciliation tests and real/synthetic run reports |
| CLI and read-only query | `logpipe/cli.py` | `tests/test_cli.py`; CLI help check |
| Cross-platform automated CI | `.github/workflows/tests.yml` | GitHub Actions matrix; remote workflow result must be checked after push |
| Reproducible synthetic edge-case data | `logpipe/generator.py` | `tests/test_generator.py`; 100K labelled run |
| Real-world data | Loghub OpenStack logs | `docs/real_data_validation.md`; raw dataset excluded from repository |

Items not yet implemented: continuous file watching/streaming, a dedicated reject-replay command, run locking for concurrent writers, and a configurable reject-rate hard-fail threshold. The current product is a command-driven local batch pipeline.


| Additional reliability guard | Detect source-file mutation between initial hash and post-ingestion verification | `logpipe/pipeline.py` | `SafetyRegressionTests.test_file_mutated_during_ingestion_is_rolled_back_and_retryable` |
