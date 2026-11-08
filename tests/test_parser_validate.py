import json
import unittest

from logpipe.config import Config
from logpipe.errors import RejectError
from logpipe.parser import parse_line
from logpipe.validate import validate_event
from tests.helpers import AS_OF

CFG = Config()
SERVICES = {"auth", "payments", "search"}


def validate(fields, cfg=CFG):
    return validate_event(fields, SERVICES, AS_OF, cfg)


def reason_of(fields):
    try:
        validate(fields)
    except RejectError as exc:
        return exc.reason
    return None


class ParserTests(unittest.TestCase):
    def test_json_line(self):
        self.assertEqual(parse_line('{"a": 1}'), {"a": 1})

    def test_text_line_with_quoted_message_containing_equals_and_quotes(self):
        line = '2026-11-01 10:00:00,123 ERROR [auth] request_id=r1 status=500 msg="retry=3 said \\"no\\""'
        f = parse_line(line)
        self.assertEqual((f["timestamp"], f["level"], f["service"]), ("2026-11-01 10:00:00,123", "ERROR", "auth"))
        self.assertEqual(f["status"], "500")
        self.assertEqual(f["msg"], 'retry=3 said "no"')

    def test_text_line_free_text_becomes_message(self):
        f = parse_line("2026-11-01T10:00:00Z INFO [auth] user logged in request_id=r9")
        self.assertEqual(f["message"], "user logged in")
        self.assertEqual(f["request_id"], "r9")

    def test_malformed_inputs(self):
        for line, reason in [('{"timestamp": "x"', "malformed_json"), ("[1, 2]", "malformed_line"),
                             ("{not json}", "malformed_json"), ("[1]", "malformed_line"),
                             ("Traceback (most recent call last):", "malformed_line"), ("garbage", "malformed_line"),
                             ("2026-11-01 10:00:00 ERROR no brackets", "malformed_line")]:
            with self.subTest(line=line):
                with self.assertRaises(RejectError) as ctx:
                    parse_line(line)
                self.assertEqual(ctx.exception.reason, reason)

    def test_json_that_is_not_an_object(self):
        with self.assertRaises(RejectError):
            parse_line('{"a":1}}')


class ValidateTests(unittest.TestCase):
    base = {"timestamp": "2026-11-01T10:00:00Z", "level": "info", "service": "auth"}

    def test_happy_path_normalizes(self):
        ev = validate({**self.base, "status": "200", "latency_ms": "12.5", "endpoint": " /login ", "request_id": "r1"})
        self.assertEqual((ev["level"], ev["http_status"], ev["latency_ms"], ev["endpoint"]), ("INFO", 200, 12.5, "/login"))
        self.assertEqual(ev["event_hour"], "2026-11-01T10:00:00Z")
        self.assertEqual(ev["is_error"], 0)

    def test_schema_drift_aliases_and_unknown_fields(self):
        ev = validate({"TS": 1793527800123, "Severity": "WARN", "SVC": "Payments", "duration_s": 0.29,
                       "status_code": 504, "host": "n1", "trace": {"x": 1}})
        self.assertEqual(ev["event_ts"], "2026-11-01T10:10:00.123Z")
        self.assertEqual((ev["level"], ev["service"], ev["latency_ms"]), ("WARNING", "payments", 290.0))
        self.assertEqual(ev["unknown_fields"], ["host", "trace"])
        self.assertEqual(ev["is_error"], 1)  # 5xx counts as an error even at WARNING

    def test_missing_and_null_required_fields(self):
        for name in ("timestamp", "level", "service"):
            for broken in ({k: v for k, v in self.base.items() if k != name}, {**self.base, name: None},
                           {**self.base, name: "  "}):
                self.assertEqual(reason_of(broken), f"missing_required_field:{name}")

    def test_other_reject_reasons(self):
        cases = [({"timestamp": "nope"}, "invalid_timestamp"), ({"timestamp": "2027-12-31T00:00:00Z"}, "future_timestamp"),
                 ({"level": "LOUD"}, "invalid_severity"), ({"service": "ghost"}, "unknown_service"),
                 ({"status": 99}, "invalid_status"), ({"status": 600}, "invalid_status"), ({"status": "OK"}, "invalid_status"),
                 ({"status": 200.5}, "invalid_status"), ({"status": True}, "invalid_status"),
                 ({"latency_ms": -1}, "invalid_latency"), ({"latency_ms": "12ms"}, "invalid_latency"),
                 ({"latency_ms": 3_600_001}, "latency_out_of_range")]
        for patch, reason in cases:
            with self.subTest(patch=patch):
                self.assertEqual(reason_of({**self.base, **patch}), reason)

    def test_boundary_values_are_accepted(self):
        for patch in ({"status": 100}, {"status": 599}, {"latency_ms": 0}, {"latency_ms": 3_600_000}):
            self.assertIsNone(reason_of({**self.base, **patch}), patch)
        # exactly at the future-skew boundary is still accepted
        self.assertIsNone(reason_of({**self.base, "timestamp": "2026-11-08T00:00:00Z"}))

    def test_event_id_is_deterministic_and_layout_independent(self):
        from_json = validate(parse_line(json.dumps({
            "timestamp": 1793527800123, "level": "error", "service": "AUTH", "status": 500, "latency_ms": "120",
            "request_id": "r1", "message": "boom"})))
        from_text = validate(parse_line(
            '2026-11-01T15:40:00.123+05:30 ERR [auth] request_id=r1 status=500 latency_ms=120 msg="boom"'))
        self.assertEqual(from_json["event_id"], from_text["event_id"])
        self.assertTrue(from_json["event_id"].startswith("evt_"))
        other = validate(parse_line('2026-11-01T15:40:00.123+05:30 ERR [auth] request_id=r2 status=500 latency_ms=120 msg="boom"'))
        self.assertNotEqual(from_json["event_id"], other["event_id"])


if __name__ == "__main__":
    unittest.main()
