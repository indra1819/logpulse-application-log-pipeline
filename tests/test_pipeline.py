import csv
import json
import sqlite3
import unittest
from unittest import mock

from logpipe import pipeline
from logpipe.reconcile import run_reconciliation
from logpipe.report import EVENT_EXPORT_COLUMNS, REJECT_EXPORT_COLUMNS
from tests.helpers import SERVICES_CSV, PipelineTestCase, jl


class HappyPathTests(PipelineTestCase):
    def test_loads_json_and_text_lines_and_summarizes(self):
        self.write_log("a.log", [
            jl("2026-11-01T10:05:00Z", "INFO", "auth", status=200, latency_ms=100, request_id="r1"),
            '2026-11-01 10:06:00,500 ERROR [auth] request_id=r2 status=500 latency_ms=300 msg="boom"',
            jl("2026-11-01T11:00:00Z", "warn", "payments", latency_ms=50, request_id="r3"),
        ])
        result = self.run_pipeline()
        m = result.metrics
        self.assertEqual((result.status, m["rows_read"], m["rows_loaded"], m["rows_rejected"]), ("success", 3, 3, 0))
        self.assertTrue(result.checks_passed)
        rows = self.query("SELECT service, event_hour, event_count, error_count, error_rate, latency_avg_ms "
                          "FROM service_hourly_summary ORDER BY service, event_hour")
        self.assertEqual(rows, [("auth", "2026-11-01T10:00:00Z", 2, 1, 0.5, 200.0),
                                ("payments", "2026-11-01T11:00:00Z", 1, 0, 0.0, 50.0)])
        for name in ("run_report.json", "reconciliation_report.md", "service_hourly_summary.csv",
                     "service_overall_summary.csv", "rejects.jsonl"):
            self.assertTrue((self.out / name).exists(), name)

    def test_run_metrics_are_recorded(self):
        self.write_log("a.log", [jl(request_id="r1")])
        result = self.run_pipeline(run_id="run_fixed_1")
        row = self.query("SELECT run_id, status, rows_read, rows_loaded, ended_at FROM runs")[0]
        self.assertEqual(row[:4], ("run_fixed_1", "success", 1, 1))
        self.assertIsNotNone(row[4])
        report = json.loads((self.out / "run_report.json").read_text())
        self.assertEqual(report["run_id"], "run_fixed_1")
        self.assertIn("rows_per_second_ingest", report)


class EmptyAndMissingInputTests(PipelineTestCase):
    def test_database_parent_directory_is_created(self):
        self.db = self.db.parent / "nested" / "dir" / "db.sqlite"
        self.write_log("a.log", [jl(request_id="r1")])
        self.assertEqual(self.run_pipeline().status, "success")
        self.assertTrue(self.db.exists())

    def test_empty_directory_is_a_successful_empty_batch(self):
        result = self.run_pipeline()
        self.assertEqual((result.status, result.metrics["files_total"], result.metrics["rows_read"]), ("success", 0, 0))
        self.assertTrue(result.checks_passed)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM service_hourly_summary"), 0)

    def test_empty_file_and_blank_lines_only(self):
        self.write_log("empty.log", [])
        self.write_log("blank.log", ["", "   ", ""])
        result = self.run_pipeline()
        self.assertEqual((result.status, result.metrics["rows_read"], result.metrics["blank_lines"]), ("success", 0, 3))

    def test_missing_input_path_fails_visibly(self):
        result = self.run_pipeline(input_path=self.input / "does_not_exist")
        self.assertEqual(result.status, "failed")
        self.assertIn("input_path_not_found", result.metrics["failure_reason"])
        self.assertEqual(self.scalar("SELECT status FROM runs"), "failed")


class RejectTests(PipelineTestCase):
    def test_malformed_and_invalid_records_are_quarantined_with_traceability(self):
        self.write_log("bad.log", [
            jl(request_id="ok1"),                                    # line 1 ok
            '{"timestamp": "2026-11-01T10:00:00Z", "level": ',        # 2 malformed json
            "this is not a log line",                                  # 3 malformed line
            jl(service=None),                                          # 4 null required
            jl(service="ghost"),                                       # 5 invalid reference key
            jl(latency_ms=-5),                                         # 6 negative latency
            jl("2030-01-01T00:00:00Z"),                                # 7 future
            '{"event": "heartbeat", "host": "x"}',                     # 8 unexpected schema
        ])
        result = self.run_pipeline()
        self.assertEqual((result.metrics["rows_loaded"], result.metrics["rows_rejected"]), (1, 7))
        rows = dict(self.query("SELECT line_no, reject_reason FROM rejected_records WHERE reject_type='invalid'"))
        self.assertEqual(rows, {2: "malformed_json", 3: "malformed_line", 4: "missing_required_field:service",
                                5: "unknown_service", 6: "invalid_latency", 7: "future_timestamp",
                                8: "missing_required_field:timestamp"})
        src, raw = self.query("SELECT source_file, raw_line FROM rejected_records WHERE line_no=3")[0]
        self.assertEqual((src, raw), ("bad.log", "this is not a log line"))
        exported = [json.loads(x) for x in (self.out / "rejects.jsonl").read_text().splitlines()]
        self.assertEqual(len(exported), 7)
        self.assertTrue(result.checks_passed)

    def test_invalid_utf8_line_is_rejected_not_fatal(self):
        path = self.write_log("enc.log", [jl(request_id="ok")])
        path.write_bytes(path.read_bytes() + b"\xff\xfe broken bytes\n")
        result = self.run_pipeline()
        self.assertEqual(result.status, "success")
        self.assertEqual(self.scalar("SELECT reject_reason FROM rejected_records"), "invalid_encoding")

    def test_windows_line_endings_and_bom(self):
        path = self.input / "win.log"
        path.write_bytes(("\ufeff" + jl(request_id="a") + "\r\n" + jl(request_id="b") + "\r\n").encode("utf-8"))
        result = self.run_pipeline()
        self.assertEqual((result.metrics["rows_loaded"], result.metrics["rows_rejected"]), (2, 0))


class DuplicateTests(PipelineTestCase):
    def test_duplicate_within_a_file_and_across_files_in_different_layouts(self):
        original = jl("2026-11-01T10:00:00Z", "ERROR", "auth", status=500, latency_ms=120, request_id="r1", message="boom")
        same_as_text = '2026-11-01T15:30:00+05:30 ERR [AUTH] request_id=r1 status=500 latency_ms=120 msg="boom"'
        self.write_log("a.log", [original, original])
        self.write_log("b.log", [same_as_text])
        result = self.run_pipeline()
        self.assertEqual((result.metrics["rows_loaded"], result.metrics["rows_duplicate"]), (1, 2))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM rejected_records WHERE reject_type='duplicate'"), 2)
        self.assertEqual(self.scalar("SELECT source_file FROM log_events"), "a.log")  # first copy wins
        self.assertTrue(result.checks_passed)

    def test_same_content_different_request_id_is_not_a_duplicate(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2")])
        self.assertEqual(self.run_pipeline().metrics["rows_loaded"], 2)


class IdempotencyTests(PipelineTestCase):
    def test_rerun_of_same_input_changes_nothing(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2", level="ERROR")])
        first = self.run_pipeline()
        before = self.query("SELECT * FROM log_events ORDER BY event_id")
        summary_before = (self.out / "service_hourly_summary.csv").read_text()
        second = self.run_pipeline()
        self.assertEqual(second.metrics["files_skipped"], 1)
        self.assertEqual(second.metrics["rows_loaded"], 0)
        self.assertEqual(self.query("SELECT * FROM log_events ORDER BY event_id"), before)
        self.assertEqual((self.out / "service_hourly_summary.csv").read_text(), summary_before)
        self.assertTrue(first.checks_passed and second.checks_passed)

    def test_forced_reprocessing_does_not_duplicate_or_misreport(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2"), "garbage"])
        self.run_pipeline()
        forced = self.run_pipeline(force=True)
        self.assertEqual(forced.metrics["rows_loaded"], 0)
        self.assertEqual(forced.metrics["rows_duplicate"], 0)  # same source lines are replays, not duplicates
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM rejected_records"), 1)
        self.assertTrue(forced.checks_passed)

    def test_published_csvs_match_store_and_awkward_raw_lines_round_trip(self):
        awkward = 'oops, "quoted", {not json'   # comma + quotes would break a naive CSV writer
        good = jl(request_id="r1", latency_ms=12)
        self.write_log("a.log", [good, jl(request_id="r2", level="ERROR", status=503), good, awkward, jl(ts="yesterday")])
        result = self.run_pipeline()
        self.assertTrue(result.checks_passed)
        with open(self.out / "clean_events.csv", newline="", encoding="utf-8") as fh:
            events = list(csv.DictReader(fh))
        with open(self.out / "rejected_records.csv", newline="", encoding="utf-8") as fh:
            rejects = list(csv.DictReader(fh))
        self.assertEqual(len(events), self.scalar("SELECT COUNT(*) FROM log_events"))
        self.assertEqual(len(rejects), self.scalar("SELECT COUNT(*) FROM rejected_records"))
        self.assertEqual(list(events[0].keys()), EVENT_EXPORT_COLUMNS)
        self.assertEqual(list(rejects[0].keys()), REJECT_EXPORT_COLUMNS)
        self.assertEqual({e["service"] for e in events}, {"auth"})
        self.assertEqual(sorted(e["is_error"] for e in events), ["0", "1"])       # the 503 is the error
        by_reason = {r["reject_reason"]: r for r in rejects}
        self.assertEqual(set(by_reason), {"duplicate_event", "malformed_line", "invalid_timestamp"})
        self.assertEqual(by_reason["malformed_line"]["raw_line"], awkward)         # survives CSV quoting exactly
        self.assertEqual((by_reason["duplicate_event"]["source_file"], by_reason["duplicate_event"]["line_no"]), ("a.log", "3"))
        self.assertIn("published_csv_matches_store", {c["name"] for c in result.checks})

    def test_empty_run_still_publishes_header_only_csvs(self):
        self.write_log("empty.log", [])
        self.run_pipeline()
        for name in ("clean_events.csv", "rejected_records.csv"):
            lines = (self.out / name).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1, name)  # header only

    def test_reconciliation_flags_a_published_csv_that_disagrees_with_the_store(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2"), "garbage"])
        self.run_pipeline()
        conn = sqlite3.connect(self.db)
        try:
            ok = run_reconciliation(conn, None, {"clean_events": 2, "rejected_records": 1})
            bad = run_reconciliation(conn, None, {"clean_events": 1, "rejected_records": 1})
        finally:
            conn.close()
        status = lambda checks: next(c["passed"] for c in checks if c["name"] == "published_csv_matches_store")
        self.assertTrue(status(ok))
        self.assertFalse(status(bad))

    def test_reject_is_removed_once_the_line_loads_after_reference_fix(self):
        # newsvc is unknown at first (rejected), then added to the reference list and the file re-read with --force
        self.write_log("a.log", [jl(request_id="r1"), jl(service="newsvc", request_id="r2")])
        first = self.run_pipeline()
        self.assertEqual(first.metrics["rows_rejected"], 1)
        self.services.write_text(SERVICES_CSV + "newsvc,platform\n")
        skipped = self.run_pipeline()  # same file content -> skipped; needs --force to re-read
        self.assertEqual(skipped.metrics["files_skipped"], 1)
        forced = self.run_pipeline(force=True)
        self.assertEqual(forced.metrics["rows_loaded"], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM rejected_records"), 0)  # no stale reject left
        self.assertNotIn("newsvc", (self.out / "rejects.jsonl").read_text())
        self.assertTrue(forced.checks_passed)

    def test_appended_file_is_picked_up_and_old_files_skipped(self):
        self.write_log("a.log", [jl(request_id="r1")])
        self.run_pipeline()
        self.write_log("b.log", [jl(request_id="r2")])
        second = self.run_pipeline()
        self.assertEqual((second.metrics["files_skipped"], second.metrics["files_succeeded"], second.metrics["rows_loaded"]), (1, 1, 1))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)


class PartialFailureTests(PipelineTestCase):
    def test_failed_file_is_isolated_then_loaded_on_retry(self):
        self.write_log("a.log", [jl(request_id="r1")])
        self.write_log("b.log", [jl(request_id="r2")])
        real = pipeline.ingest_file

        def flaky(conn, run_id, path, source_file, *args, **kwargs):
            if source_file == "b.log":
                raise OSError("disk went away")
            return real(conn, run_id, path, source_file, *args, **kwargs)

        with mock.patch.object(pipeline, "ingest_file", flaky):
            first = self.run_pipeline()
        self.assertEqual(first.status, "partial_failure")
        self.assertIn("b.log", first.metrics["failure_reason"])
        self.assertIn("disk went away", first.metrics["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 1)

        retry = self.run_pipeline()  # no code change: a.log skipped, b.log loaded
        self.assertEqual(retry.status, "success")
        self.assertEqual((retry.metrics["files_skipped"], retry.metrics["files_succeeded"]), (1, 1))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)
        self.assertTrue(retry.checks_passed)

    def test_mid_file_crash_rolls_back_the_whole_file(self):
        self.write_log("a.log", [jl(request_id=f"r{i}") for i in range(10)])
        real = pipeline.validate_event
        calls = {"n": 0}

        def crash_on_fifth(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 5:
                raise RuntimeError("worker killed")
            return real(*args, **kwargs)

        with mock.patch.object(pipeline, "validate_event", crash_on_fifth):
            result = self.run_pipeline()
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ingested_files"), 0)
        self.assertEqual(self.scalar("SELECT status FROM run_files"), "failed")
        self.assertEqual(self.run_pipeline().metrics["rows_loaded"], 10)  # clean restart


class OrderAndLatenessTests(PipelineTestCase):
    LINES = [jl(f"2026-11-01T10:{m:02d}:00Z", "ERROR" if m % 4 == 0 else "INFO", "auth", latency_ms=10 * m,
                request_id=f"r{m}") for m in range(0, 40, 2)]

    def _summary(self):
        return self.query("SELECT * FROM service_hourly_summary ORDER BY service, event_hour")

    def test_out_of_order_input_gives_identical_summaries_and_is_flagged(self):
        self.write_log("ordered.log", self.LINES)
        self.run_pipeline()
        ordered = self._summary()

        self.setUp()  # fresh workspace
        shuffled = list(reversed(self.LINES))
        self.write_log("shuffled.log", shuffled)
        self.run_pipeline()
        self.assertEqual(self._summary(), ordered)
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM log_events WHERE is_out_of_order=1"), 0)

    def test_late_arriving_file_corrects_earlier_hour_summary(self):
        self.write_log("day1.log", [jl("2026-11-01T10:00:00Z", latency_ms=100, request_id="r1"),
                                    jl("2026-11-01T12:00:00Z", latency_ms=100, request_id="r2")])
        self.run_pipeline()
        self.assertEqual(self.scalar("SELECT event_count FROM service_hourly_summary WHERE event_hour='2026-11-01T10:00:00Z'"), 1)
        self.write_log("late.log", [jl("2026-11-01T10:30:00Z", "ERROR", latency_ms=900, request_id="r3")])
        self.run_pipeline()
        row = self.query("SELECT event_count, error_count, latency_max_ms FROM service_hourly_summary "
                         "WHERE event_hour='2026-11-01T10:00:00Z'")[0]
        self.assertEqual(row, (2, 1, 900.0))

    def test_offset_timestamps_land_in_the_correct_utc_hour(self):
        self.write_log("tz.log", [jl("2026-11-02T00:30:00+05:30", request_id="r1")])  # = 2026-11-01 19:00 UTC
        self.run_pipeline()
        self.assertEqual(self.scalar("SELECT event_hour FROM log_events"), "2026-11-01T19:00:00Z")


class SchemaDriftTests(PipelineTestCase):
    def test_renamed_fields_load_and_unexpected_fields_are_counted(self):
        self.write_log("drift.log", [
            json.dumps({"ts": 1793527800123, "severity": "ERR", "svc": "search", "duration_s": 1.5, "req_id": "r1",
                        "host": "n1"}),
            json.dumps({"time": "2026-11-01T10:11:00Z", "lvl": "info", "app": "auth"}),
        ])
        result = self.run_pipeline()
        self.assertEqual(result.metrics["rows_loaded"], 2)
        self.assertEqual(result.metrics["files"][0]["rows_with_unknown_fields"], 1)
        self.assertEqual(self.scalar("SELECT latency_ms FROM log_events WHERE service='search'"), 1500.0)

    def test_file_with_mostly_rejected_rows_is_flagged_in_the_log(self):
        self.write_log("bad_schema.log", [json.dumps({"event": "x", "i": i}) for i in range(5)] + [jl(request_id="ok")])
        with self.assertLogs("logpipe.pipeline", level="WARNING") as captured:
            self.run_pipeline()
        self.assertTrue(any("possible schema change" in line for line in captured.output))


class LatencyEdgeCaseTests(PipelineTestCase):
    def test_boundary_latencies_load_and_just_over_is_rejected(self):
        self.write_log("lat.log", [jl(latency_ms=0, request_id="a"), jl(latency_ms=3_600_000, request_id="b"),
                                   jl(latency_ms=3_600_001, request_id="c")])
        result = self.run_pipeline()
        self.assertEqual((result.metrics["rows_loaded"], result.metrics["rows_rejected"]), (2, 1))
        self.assertEqual(self.scalar("SELECT reject_reason FROM rejected_records"), "latency_out_of_range")


if __name__ == "__main__":
    unittest.main()

class SafetyRegressionTests(PipelineTestCase):
    def test_file_mutated_during_ingestion_is_rolled_back_and_retryable(self):
        path = self.write_log("a.log", [jl(request_id="before")])
        real = pipeline.ingest_file

        def mutate_after_read(conn, run_id, file_path, source_file, *args, **kwargs):
            stats = real(conn, run_id, file_path, source_file, *args, **kwargs)
            with open(file_path, "ab") as fh:
                fh.write((jl(request_id="after") + "\n").encode("utf-8"))
            return stats

        with mock.patch.object(pipeline, "ingest_file", mutate_after_read):
            first = self.run_pipeline()

        self.assertEqual(first.status, "failed")
        self.assertIn("input_file_changed_during_ingestion", first.metrics["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ingested_files"), 0)

        retry = self.run_pipeline()
        self.assertEqual(retry.status, "success")
        self.assertEqual(retry.metrics["rows_loaded"], 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)

    def test_output_directory_inside_input_is_never_reingested(self):
        self.write_log("app.log", [jl(request_id="safe-1")])
        first = self.run_pipeline()
        self.assertEqual(first.metrics["rows_loaded"], 1)
        second = self.run_pipeline()
        self.assertEqual(second.metrics["files_total"], 1)
        self.assertEqual(second.metrics["files_skipped"], 1)
        self.assertEqual(second.metrics["rows_read"], 0)

    def test_failed_reconciliation_sets_run_status_failed(self):
        from unittest import mock
        from logpipe import pipeline
        self.write_log("app.log", [jl(request_id="reconcile-1")])
        with mock.patch.object(pipeline, "run_reconciliation", return_value=[
            {"name": "deliberate_failure", "passed": False, "detail": "tamper detected"}
        ]):
            result = self.run_pipeline()
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.checks_passed)
        self.assertEqual(self.scalar("SELECT status FROM runs ORDER BY started_at DESC LIMIT 1"), "failed")
