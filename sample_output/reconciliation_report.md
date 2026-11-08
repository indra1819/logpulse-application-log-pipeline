# Validation / reconciliation report

- run_id: `run_20261107T144732Z_f203997b`
- status: **success**
- rows read / loaded / duplicate / rejected: 319 / 300 / 6 / 13

## Checks

| check | result | detail |
|---|---|---|
| file_row_balance | PASS | every loaded file: rows_read = loaded + duplicate + replayed + rejected (0 files out of balance) |
| store_matches_run_ledger | PASS | log_events rows=300, sum of rows_loaded across runs=300 |
| event_id_unique | PASS | 300 distinct ids for 300 rows |
| referential_integrity_service | PASS | 0 events reference a service missing from dim_service |
| events_traceable_to_source | PASS | 0 events lack source_file/line_no |
| summary_event_count_matches_store | PASS | summary events=300, store rows=300 |
| summary_error_count_matches_store | PASS | summary errors=43, store errors=43 |
| published_csv_matches_store | PASS | clean_events.csv rows=300 (store 300), rejected_records.csv rows=19 (store 19) |
| labels_loaded_count | PASS | expected=300, actual=300 |
| labels_duplicate_count | PASS | expected=6, actual=6 |
| labels_reject_reasons | PASS | all reject reasons match |

## Rejects by reason (whole store)

| reason | count |
|---|---|
| duplicate_event | 6 |
| future_timestamp | 1 |
| invalid_encoding | 1 |
| invalid_latency | 1 |
| invalid_severity | 1 |
| invalid_status | 1 |
| invalid_timestamp | 1 |
| latency_out_of_range | 1 |
| malformed_json | 1 |
| malformed_line | 1 |
| missing_required_field:level | 1 |
| missing_required_field:service | 1 |
| missing_required_field:timestamp | 1 |
| unknown_service | 1 |
