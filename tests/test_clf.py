"""Common Log Format (Apache / NASA-HTTP style) support.

The first two lines are genuine lines from the public NASA-HTTP traces (as quoted in public tutorials);
the rest follow the same documented layout.
"""
import gzip
import unittest

from logpipe.config import Config
from logpipe.errors import RejectError
from logpipe.parser import parse_line
from tests.helpers import SERVICES_CSV, PipelineTestCase

REAL_1 = '198.214.42.14 - - [21/Jul/1995:14:31:46 -0400] "GET /images/ HTTP/1.0" 200 17688'
REAL_2 = 'lahal.ksc.nasa.gov - - [24/Jul/1995:12:42:40 -0400] "GET /images/USA-logosmall.gif HTTP/1.0" 200 234'
CFG = Config(clf_service="nasa_web")


class ClfParserTests(unittest.TestCase):
    def test_fields_and_derived_level(self):
        f = parse_line(REAL_1, "nasa_web")
        self.assertEqual((f["timestamp"], f["client_host"], f["endpoint"], f["status"], f["level"], f["service"]),
                         ("21/Jul/1995:14:31:46 -0400", "198.214.42.14", "/images/", "200", "INFO", "nasa_web"))
        self.assertEqual(f["message"], "GET /images/ HTTP/1.0")
        for status, level in (("304", "INFO"), ("404", "WARNING"), ("500", "ERROR"), ("503", "ERROR")):
            line = REAL_1.replace(" 200 ", f" {status} ")
            self.assertEqual(parse_line(line, "nasa_web")["level"], level)

    def test_dash_bytes_and_embedded_quote_and_odd_request(self):
        self.assertEqual(parse_line(REAL_1.replace("17688", "-"), "nasa_web")["status"], "200")
        weird = 'h.example.com - - [01/Jul/1995:00:00:12 -0400] "GET /a"b HTTP/1.0" 404 -'
        self.assertEqual(parse_line(weird, "nasa_web")["endpoint"], '/a"b')
        self.assertEqual(parse_line(REAL_1.replace("GET /images/ HTTP/1.0", "GET"), "nasa_web")["endpoint"], "GET")

    def test_truncated_line_is_malformed(self):
        with self.assertRaises(RejectError) as ctx:
            parse_line('199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /history/apollo/ HTTP/1.0" 200', "nasa_web")
        self.assertEqual(ctx.exception.reason, "malformed_line")


class ClfPipelineTests(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.services.write_text(SERVICES_CSV + "nasa_web,public-web\n")

    def test_real_lines_load_with_utc_conversion_and_error_rules(self):
        self.write_log("NASA_sample.log", [
            REAL_1, REAL_2,
            '199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /a HTTP/1.0" 404 0',
            '199.72.81.55 - - [01/Jul/1995:00:00:02 -0400] "GET /b HTTP/1.0" 500 0',
        ])
        result = self.run_pipeline(cfg=CFG)
        self.assertEqual((result.status, result.metrics["rows_loaded"]), ("success", 4))
        self.assertTrue(result.checks_passed)
        self.assertEqual(self.scalar("SELECT event_ts FROM log_events WHERE client_host='198.214.42.14'"),
                         "1995-07-21T18:31:46.000Z")  # 14:31:46 at -0400
        rows = dict(self.query("SELECT http_status, is_error FROM log_events"))
        self.assertEqual((rows[404], rows[500], rows[200]), (0, 1, 0))  # only 5xx counts as an error
        self.assertEqual(self.scalar("SELECT latency_count FROM service_hourly_summary LIMIT 1"), 0)  # CLF has no latency

    def test_without_clf_service_lines_are_rejected_not_guessed(self):
        self.write_log("a.log", [REAL_1])
        result = self.run_pipeline()
        self.assertEqual(result.metrics["rows_loaded"], 0)
        self.assertEqual(self.scalar("SELECT reject_reason FROM rejected_records"), "missing_required_field:service")

    def test_dash_status_and_bad_bytes_and_truncated_lines_are_quarantined(self):
        path = self.write_log("a.log", [
            REAL_1.replace(" 200 ", " - "),                       # status "-"
            '199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /x HTTP/1.0" 200',   # truncated
            REAL_2,
        ])
        path.write_bytes(path.read_bytes() + b'h - - [01/Jul/1995:00:00:09 -0400] "GET /\xff\xfe HTTP/1.0" 200 5\n')
        result = self.run_pipeline(cfg=CFG)
        reasons = sorted(r for (r,) in self.query("SELECT reject_reason FROM rejected_records"))
        self.assertEqual(reasons, ["invalid_encoding", "invalid_status", "malformed_line"])
        self.assertEqual(result.metrics["rows_loaded"], 1)

    def test_same_host_url_and_second_is_a_duplicate(self):
        self.write_log("a.log", [REAL_1, REAL_1])
        result = self.run_pipeline(cfg=CFG)
        self.assertEqual((result.metrics["rows_loaded"], result.metrics["rows_duplicate"]), (1, 1))

    def test_different_hosts_same_second_are_different_events(self):
        self.write_log("a.log", [REAL_1, REAL_1.replace("198.214.42.14", "10.0.0.1")])
        self.assertEqual(self.run_pipeline(cfg=CFG).metrics["rows_loaded"], 2)

    def test_gzip_input_is_read_directly(self):
        gz = self.input / "NASA_access_log_Jul95.gz"
        gz.write_bytes(gzip.compress((REAL_1 + "\n" + REAL_2 + "\n").encode()))
        result = self.run_pipeline(cfg=CFG)
        self.assertEqual((result.status, result.metrics["rows_loaded"]), ("success", 2))
        # a single file path (not a directory) works too, even without a known extension
        plain = self.input.parent / "NASA_access_log_Aug95"
        plain.write_text(REAL_1.replace("21/Jul", "21/Aug") + "\n")
        self.assertEqual(self.run_pipeline(input_path=plain, cfg=CFG).metrics["rows_loaded"], 1)

    def test_corrupt_gzip_fails_that_file_only(self):
        (self.input / "bad.gz").write_bytes(b"this is not gzip data")
        self.write_log("ok.log", [REAL_1])
        result = self.run_pipeline(cfg=CFG)
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM log_events"), 1)


if __name__ == "__main__":
    unittest.main()
