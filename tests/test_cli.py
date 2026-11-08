"""Command-line entry point tests."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from logpipe.cli import main, split_sql


class CliTests(unittest.TestCase):
    def test_run_command_executes_pipeline_and_returns_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            line = {
                "timestamp": "2026-11-01T10:00:00Z",
                "level": "INFO",
                "service": "auth",
                "request_id": "cli-test-1",
            }
            (source / "events.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "run", "--input", str(source), "--db", str(root / "db.sqlite"),
                    "--out", str(root / "out"), "--as-of", "2026-11-07T00:00:00Z",
                ])
            self.assertEqual(code, 0)
            report = json.loads((root / "out" / "run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["rows_loaded"], 1)
            self.assertTrue(report["reconciliation"]["passed"])
            self.assertIn("status=success", stdout.getvalue())

    def test_generate_command_writes_separate_input_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "generate", "--rows", "10", "--seed", "3", "--files", "2",
                    "--out-dir", str(root / "input"), "--labels-dir", str(root / "labels"),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(len(list((root / "input").glob("*.log"))), 2)
            self.assertTrue((root / "labels" / "expected_labels.json").is_file())

    def _run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_missing_services_file_gives_clear_error_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            code, _, err = self._run_cli(["run", "--input", str(root / "input"), "--db", str(root / "db.sqlite"),
                                          "--out", str(root / "out"), "--services", str(root / "nope.csv")])
            self.assertEqual(code, 2)
            self.assertIn("services file not found", err)
            self.assertFalse((root / "db.sqlite").exists())  # nothing was created

    def test_unreadable_labels_file_gives_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            services = root / "svc.csv"
            services.write_text("service,owner_team\nauth,identity\n", encoding="utf-8")
            bad = root / "labels.json"
            bad.write_text("{not json", encoding="utf-8")
            for labels in (bad, root / "missing.json"):
                code, _, err = self._run_cli(["run", "--input", str(root / "input"), "--db", str(root / "db.sqlite"),
                                              "--out", str(root / "out"), "--services", str(services),
                                              "--labels", str(labels)])
                self.assertEqual(code, 2)
                self.assertIn("cannot read --labels file", err)

    def test_split_sql_ignores_semicolons_in_strings_and_comments(self):
        script = ("-- header; with a semicolon\nSELECT 'a;b' AS x;\n-- only a comment;\n"
                  "SELECT 1 AS y\nUNION ALL\nSELECT 2;\n")
        parts = split_sql(script)
        self.assertEqual(len(parts), 2)
        self.assertIn("'a;b'", parts[0])
        self.assertIn("UNION ALL", parts[1])

    def test_query_rejects_writes_and_reports_sql_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            import sqlite3
            conn = sqlite3.connect(db)
            try:
                conn.executescript("CREATE TABLE t (a INTEGER); INSERT INTO t VALUES (1);")
            finally:
                conn.close()
            code, out, _ = self._run_cli(["query", "--db", str(db), "--sql", "SELECT a FROM t"])
            self.assertEqual(code, 0)
            self.assertIn("1", out)
            code, _, err = self._run_cli(["query", "--db", str(db), "--sql", "DELETE FROM t"])  # read-only connection
            self.assertEqual(code, 2)
            self.assertIn("error:", err)
            code, _, err = self._run_cli(["query", "--db", str(db), "--sql", "SELECT nope FROM t"])
            self.assertEqual(code, 2)
            self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
