import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from logpipe.report import (
    HOURLY_COLUMNS,
    export_query_to_csv,
    percentile,
    refresh_summary_table,
    summarize_group,
    write_csv,
)


class ReportMathTests(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        values = [100 * i for i in range(1, 11)]
        self.assertEqual(percentile(values, 50), 500)
        self.assertEqual(percentile(values, 95), 1000)
        self.assertEqual(percentile(values, 99), 1000)
        self.assertEqual(percentile([7], 99), 7)
        self.assertIsNone(percentile([], 50))

    def test_percentile_boundary_values(self):
        values = [10, 20, 30, 40]
        self.assertEqual(percentile(values, 0), 10)
        self.assertEqual(percentile(values, 100), 40)

    def test_percentile_rejects_invalid_percentage(self):
        for pct in (-1, 101):
            with self.subTest(pct=pct):
                with self.assertRaises(ValueError):
                    percentile([10, 20, 30], pct)

    def test_summary_group_known_numbers(self):
        rows = [(1 if i in (3, 8) else 0, 100 * i) for i in range(1, 11)]
        summary = summarize_group(rows)
        self.assertEqual(
            (summary["event_count"], summary["error_count"], summary["error_rate"]),
            (10, 2, 0.2),
        )
        self.assertEqual(
            (summary["latency_avg_ms"], summary["latency_p50_ms"], summary["latency_p95_ms"]),
            (550.0, 500, 1000),
        )

    def test_group_with_no_latency_values(self):
        summary = summarize_group([(0, None), (1, None)])
        self.assertEqual(
            (summary["event_count"], summary["error_rate"], summary["latency_count"]),
            (2, 0.5, 0),
        )
        self.assertIsNone(summary["latency_avg_ms"])
        self.assertIsNone(summary["latency_min_ms"])
        self.assertIsNone(summary["latency_p50_ms"])
        self.assertIsNone(summary["latency_p95_ms"])
        self.assertIsNone(summary["latency_p99_ms"])
        self.assertIsNone(summary["latency_max_ms"])


class ReportCsvTests(unittest.TestCase):
    def test_write_csv_handles_special_characters(self):
        rows = [
            {"service": "orders", "message": 'Error, "payment failed"\nRetry requested'},
            {"service": "search", "message": "Normal message"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.csv"
            write_csv(path, ["service", "message"], rows)
            with path.open("r", newline="", encoding="utf-8") as fh:
                actual = list(csv.DictReader(fh))

        self.assertEqual(len(actual), 2)
        self.assertEqual(actual[0]["service"], "orders")
        self.assertEqual(actual[0]["message"], 'Error, "payment failed"\nRetry requested')
        self.assertEqual(actual[1]["message"], "Normal message")

    def test_export_query_to_csv_handles_null_and_special_characters(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE test_events (message TEXT, optional_value TEXT)")
            expected_message = 'Failure, "retry"\nNext line'
            conn.execute(
                "INSERT INTO test_events VALUES (?, ?)",
                (expected_message, None),
            )

            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "events.csv"
                count = export_query_to_csv(
                    conn,
                    path,
                    "SELECT message, optional_value FROM test_events",
                    ["message", "optional_value"],
                )
                with path.open("r", newline="", encoding="utf-8") as fh:
                    actual = list(csv.DictReader(fh))

            self.assertEqual(count, 1)
            self.assertEqual(actual[0]["message"], expected_message)
            self.assertEqual(actual[0]["optional_value"], "")
        finally:
            conn.close()


class SummaryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        definitions = []
        for column in HOURLY_COLUMNS:
            if column in ("service", "event_hour"):
                definitions.append(f"{column} TEXT NOT NULL")
            else:
                definitions.append(f"{column} TEXT")
        self.conn.execute(
            "CREATE TABLE service_hourly_summary (%s)" % ", ".join(definitions)
        )

        self.old_row = {column: "old_value" for column in HOURLY_COLUMNS}
        self.conn.execute(
            "INSERT INTO service_hourly_summary (%s) VALUES (%s)"
            % (",".join(HOURLY_COLUMNS), ",".join("?" * len(HOURLY_COLUMNS))),
            [self.old_row[column] for column in HOURLY_COLUMNS],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_refresh_summary_table_replaces_existing_rows(self):
        new_row = {column: "new_value" for column in HOURLY_COLUMNS}
        refresh_summary_table(self.conn, [new_row])
        rows = self.conn.execute("SELECT * FROM service_hourly_summary").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "new_value")
        self.assertEqual(rows[0][1], "new_value")

    def test_refresh_works_inside_existing_transaction(self):
        self.conn.execute("CREATE TABLE transaction_marker (value TEXT)")
        self.conn.execute("INSERT INTO transaction_marker VALUES ('outer transaction')")
        new_row = {column: "new_value" for column in HOURLY_COLUMNS}
        refresh_summary_table(self.conn, [new_row])

        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()

        rows = self.conn.execute("SELECT * FROM service_hourly_summary").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "old_value")

    def test_failed_refresh_restores_previous_summary(self):
        invalid_row = {column: "new_value" for column in HOURLY_COLUMNS}
        invalid_row["service"] = None

        with self.assertRaises(sqlite3.IntegrityError):
            refresh_summary_table(self.conn, [invalid_row])

        rows = self.conn.execute("SELECT * FROM service_hourly_summary").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "old_value")


if __name__ == "__main__":
    unittest.main()
