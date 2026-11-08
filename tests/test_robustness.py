"""Robustness tests.

1. Seeded fuzzing: hostile or mangled input may be rejected, but must never crash the parser/validator, and a whole
   pipeline run over hostile input must still balance (rows_read = loaded + duplicate + rejected).
2. The uncommon error paths (unreadable file, output folder inside the input folder, bad offsets, rollback, CLI errors),
   each checked against the behaviour documented in docs/design_notes.md.
"""
import contextlib
import io
import json
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from logpipe import db, pipeline
from logpipe.adapters import json_adapter
from logpipe.cli import main
from logpipe.config import Config
from logpipe.errors import RejectError
from logpipe.normalize import normalize_timestamp
from logpipe.parser import parse_line
from logpipe.report import refresh_summary_table
from logpipe.validate import validate_event
from tests.helpers import AS_OF, SERVICES_CSV, PipelineTestCase, jl

SEED = 20261107
KNOWN_SERVICES = {"auth", "payments", "search", "nasa_web", "nova-api", "nova-compute"}
SEED_LINES = [
    jl(request_id="r1", status=200, latency_ms=12, endpoint="/login"),
    jl(ts=1793492990242, level="warn", service="payments", status=503, latency_ms="45ms"),
    '2026-11-01 08:36:31,164 INFO [auth] request_id=r1 endpoint=/x status=201 latency_ms=50 msg="ok"',
    '2026-11-01T08:36:31Z ERROR [search] request_id=r2 status=500 latency=1.5s msg="boom"',
    'nova-api.log.1 2017-05-16 00:00:00.008 25746 INFO nova.osapi_compute.wsgi.server [req-38101a0b-2096] '
    '10.11.10.1 "GET /v2/servers/detail HTTP/1.1" status: 200 len: 1893 time: 0.2477829',
    '2017-05-16 00:00:04.500 2931 AUDIT nova.compute.manager [req-3ea4052c] [instance: abc] VM Started',
    '199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /history/apollo/ HTTP/1.0" 200 6245',
]
NOISE = list("{}[]\"'\\,:;=()<>|&%$#@!~`^*+-_/ \t\x00\x1b") + ["\u00e9", "\u4e2d", "\U0001f600", "null", "NaN", "9" * 30, "-1"]


def mutate(rng: random.Random, line: str) -> str:
    """Apply 1-4 random edits to a valid line."""
    for _ in range(rng.randint(1, 4)):
        if not line:
            line = rng.choice(NOISE)
        pos = rng.randrange(len(line))
        kind = rng.randrange(7)
        if kind == 0:
            line = line[:pos] + line[pos + rng.randint(1, 8):]                       # delete a span
        elif kind == 1:
            line = line[:pos] + "".join(rng.choice(NOISE) for _ in range(rng.randint(1, 5))) + line[pos:]
        elif kind == 2:
            line = line[:pos] + rng.choice(NOISE) + line[pos + 1:]                    # replace a character
        elif kind == 3:
            line = line[:pos]                                                         # truncate
        elif kind == 4:
            line = line[:pos] + line[pos:pos + rng.randint(1, 10)] * 2 + line[pos:]   # duplicate a span
        elif kind == 5 and len(line) > 2:
            i, j = sorted(rng.sample(range(len(line)), 2))
            line = line[:i] + line[j] + line[i + 1:j] + line[i] + line[j + 1:]       # swap two characters
        else:
            line = "".join(rng.choice("0123456789") if c.isdigit() else c for c in line)  # scramble digits
    return line


def random_text(rng: random.Random) -> str:
    return "".join(rng.choice(NOISE + list("abcXYZ019 ")) for _ in range(rng.randint(0, 60)))


def hostile_lines(rng: random.Random, n: int) -> list:
    lines = []
    for _ in range(n):
        lines.append(random_text(rng) if rng.random() < 0.2 else mutate(rng, rng.choice(SEED_LINES)))
    return lines


class FuzzTests(PipelineTestCase):
    def test_parser_and_validator_only_ever_accept_or_reject_never_crash(self):
        rng = random.Random(SEED)
        accepted = rejected = 0
        for line in hostile_lines(rng, 6000):
            try:
                event = validate_event(parse_line(line, "nasa_web"), KNOWN_SERVICES, AS_OF, Config())
            except RejectError as exc:
                self.assertTrue(exc.reason, f"empty reject reason for {line!r}")
                rejected += 1
                continue
            except Exception as exc:  # noqa: BLE001 - the whole point of the test
                self.fail(f"{type(exc).__name__}: {exc} on input {line!r}")
            accepted += 1
            # anything that is accepted must satisfy the documented rules
            self.assertIn(event["level"], {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}, line)
            self.assertIn(event["service"], KNOWN_SERVICES, line)
            self.assertRegex(event["event_ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", line)
            self.assertIn(event["is_error"], (0, 1))
            if event["latency_ms"] is not None:
                self.assertTrue(0 <= event["latency_ms"] <= Config().max_latency_ms, line)
            if event["http_status"] is not None:
                self.assertTrue(100 <= event["http_status"] <= 599, line)
        # the corpus must exercise both outcomes, otherwise the test proves nothing
        self.assertGreater(accepted, 200, f"accepted={accepted} rejected={rejected}")
        self.assertGreater(rejected, 200, f"accepted={accepted} rejected={rejected}")

    def test_pipeline_balances_on_hostile_files_including_invalid_utf8(self):
        rng = random.Random(SEED + 1)
        text_lines = hostile_lines(rng, 1500)
        self.write_log("hostile.log", text_lines)
        binary = b"\n".join(bytes(rng.randrange(256) for _ in range(rng.randint(0, 80))).replace(b"\n", b" ")
                            for _ in range(300)) + b"\n"
        (self.input / "binary.log").write_bytes(binary)
        (self.input / "lines_with_nul.log").write_bytes(b'{"timestamp": "2026-11-01T10:00:00Z"\x00}\n\x00\n')
        result = self.run_pipeline()
        self.assertNotEqual(result.status, "failed", result.metrics.get("failure_reason"))
        self.assertTrue(result.checks_passed, [c for c in result.checks if not c["passed"]])
        m = result.metrics
        self.assertEqual(m["rows_read"], m["rows_loaded"] + m["rows_duplicate"] + m["rows_replayed"] + m["rows_rejected"])
        self.assertGreater(m["rows_loaded"], 0)
        self.assertGreater(m["rows_rejected"], 0)
        # a rerun of the same hostile files changes nothing
        again = self.run_pipeline()
        self.assertEqual(again.metrics["rows_loaded"], 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), m["rows_loaded"])


class ErrorPathTests(PipelineTestCase):
    def test_unreadable_file_is_a_partial_failure_other_files_load_and_retry_recovers(self):
        self.write_log("a.log", [jl(request_id="a1")])
        self.write_log("b.log", [jl(request_id="b1"), jl(request_id="b2")])
        real = pipeline.sha256_file

        def flaky(path):
            if Path(path).name == "a.log":
                raise PermissionError("denied")
            return real(path)

        with mock.patch.object(pipeline, "sha256_file", side_effect=flaky):
            first = self.run_pipeline()
        self.assertEqual(first.status, "partial_failure")
        self.assertIn("PermissionError", first.metrics["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)               # only b.log
        self.assertEqual(self.scalar("SELECT status FROM run_files WHERE source_file='a.log'"), "failed")
        second = self.run_pipeline()                                                       # the problem is fixed
        self.assertEqual(second.status, "success")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 3)
        self.assertEqual(second.metrics["files_skipped"], 1)                               # b.log not re-read

    def test_output_folder_and_database_inside_the_input_folder_are_never_ingested(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2")])
        inside_out = self.input / "results"
        inside_db = self.input / "store.log"   # a database that even has a supported file extension
        for _ in range(2):
            result = pipeline.run_pipeline(self.input, inside_db, inside_out, self.services, as_of=AS_OF)
            self.assertEqual(result.metrics["files_total"], 1)    # run_report.json, rejects.jsonl, the db: all skipped
        self.assertTrue((inside_out / "run_report.json").exists())
        conn = sqlite3.connect(inside_db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM log_events").fetchone()[0], 2)
        finally:
            conn.close()

    def test_commit_every_batching_gives_the_same_result_as_one_transaction_per_file(self):
        lines = [jl(request_id=f"r{i}") for i in range(7)] + ["garbage", jl(request_id="r1")]
        self.write_log("a.log", lines)
        pipeline.run_pipeline(self.input, self.db, self.out, self.services, as_of=AS_OF)
        other_db = Path(self._tmp.name) / "batched.sqlite"
        pipeline.run_pipeline(self.input, other_db, Path(self._tmp.name) / "out2", self.services, as_of=AS_OF,
                              cfg=Config(commit_every=2))
        conn = sqlite3.connect(other_db)
        try:
            batched = conn.execute("SELECT event_id FROM log_events ORDER BY 1").fetchall()
        finally:
            conn.close()
        self.assertEqual(batched, self.query("SELECT event_id FROM log_events ORDER BY 1"))
        self.assertEqual(len(batched), 7)

    def test_summary_step_failure_fails_the_run_keeps_loaded_events_and_a_rerun_recovers(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2")])
        with mock.patch.object(pipeline, "build_summaries", side_effect=RuntimeError("boom")):
            first = self.run_pipeline()
        self.assertEqual(first.status, "failed")
        self.assertIn("summary_or_reconciliation_failed", first.metrics["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 2)          # ingested data is not lost
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM service_hourly_summary"), 0)
        second = self.run_pipeline()                                                   # files skipped, summaries rebuilt
        self.assertEqual(second.status, "success")
        self.assertTrue(second.checks_passed)
        self.assertEqual(second.metrics["files_skipped"], 1)
        self.assertEqual(self.scalar("SELECT SUM(event_count) FROM service_hourly_summary"), 2)

    def test_wrong_expected_labels_fail_reconciliation_and_name_the_difference(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2"), "garbage"])
        labels = {"expected_loaded": 99, "expected_duplicates": 0, "expected_rejects": {"invalid_timestamp": 1}}
        result = self.run_pipeline(labels=labels)
        self.assertFalse(result.checks_passed)
        self.assertEqual(result.status, "failed")
        details = {c["name"]: c for c in result.checks}
        self.assertFalse(details["labels_loaded_count"]["passed"])
        self.assertIn("expected=99, actual=2", details["labels_loaded_count"]["detail"])
        reasons = details["labels_reject_reasons"]
        self.assertFalse(reasons["passed"])
        self.assertIn("malformed_line", reasons["detail"])
        self.assertIn("invalid_timestamp", reasons["detail"])
        self.assertIn("FAIL", (self.out / "reconciliation_report.md").read_text(encoding="utf-8"))

    def test_bad_utc_offset_and_bad_month_are_value_errors_not_crashes(self):
        for bad in ("2026-11-01T10:00:00+05:75", "01/Foo/1995:00:00:01 -0400"):
            with self.assertRaises(ValueError, msg=bad):
                normalize_timestamp(bad)
        for line in ('{"timestamp": "2026-11-01T10:00:00+05:75", "level": "INFO", "service": "auth"}',
                     '1.2.3.4 - - [01/Foo/1995:00:00:01 -0400] "GET / HTTP/1.0" 200 5'):
            with self.assertRaises(RejectError) as ctx:
                validate_event(parse_line(line, "nasa_web"), KNOWN_SERVICES, AS_OF, Config())
            self.assertEqual(ctx.exception.reason, "invalid_timestamp")

    def test_json_adapter_rejects_values_that_are_not_objects(self):
        for text in ("[1, 2]", "{bad", '"just a string"', "42"):
            with self.assertRaises(RejectError) as ctx:
                json_adapter.parse(text)
            self.assertEqual(ctx.exception.reason, "malformed_json")

    def test_boolean_latency_is_rejected(self):
        raw = {"timestamp": "2026-11-01T10:00:00Z", "level": "INFO", "service": "auth", "latency_ms": True}
        with self.assertRaises(RejectError) as ctx:
            validate_event(raw, KNOWN_SERVICES, AS_OF, Config())
        self.assertEqual(ctx.exception.reason, "invalid_latency")

    def test_summary_refresh_is_atomic_when_it_fails_midway(self):
        self.write_log("a.log", [jl(request_id="r1"), jl(request_id="r2", service="search")])
        self.run_pipeline()
        before = self.query("SELECT * FROM service_hourly_summary ORDER BY service, event_hour")
        self.assertTrue(before)
        conn = db.connect(self.db)
        try:
            with self.assertRaises(KeyError):
                refresh_summary_table(conn, [{"service": "auth"}])    # a row missing required columns
            # checked on the SAME connection, before close(): closing would discard an open transaction by itself
            self.assertFalse(conn.in_transaction, "the failed refresh left a transaction open")
            self.assertEqual(conn.execute("SELECT * FROM service_hourly_summary ORDER BY service, event_hour").fetchall(),
                             before)
        finally:
            conn.close()
        self.assertEqual(self.query("SELECT * FROM service_hourly_summary ORDER BY service, event_hour"), before)


class CliErrorTests(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_query_on_a_missing_database_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self._run(["query", "--db", str(Path(tmp) / "nope.db"), "--sql", "SELECT 1"])
            self.assertEqual(code, 2)
            self.assertIn("database not found", err)

    def test_invalid_as_of_and_missing_input_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            services = root / "svc.csv"
            services.write_text(SERVICES_CSV, encoding="utf-8")
            base = ["run", "--db", str(root / "db.sqlite"), "--out", str(root / "out"), "--services", str(services)]
            code, _, err = self._run(base + ["--input", str(root), "--as-of", "not-a-date"])
            self.assertEqual(code, 2)
            self.assertIn("--as-of", err)
            code, out, err = self._run(base + ["--input", str(root / "missing")])
            self.assertEqual(code, 1)
            self.assertIn("status=failed", out)
            self.assertIn("failure_reason", err)
            report = json.loads((root / "out" / "run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
