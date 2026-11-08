import unittest

from logpipe.normalize import (hour_bucket, normalize_severity, normalize_timestamp, to_iso_utc)


def n(value):
    return to_iso_utc(normalize_timestamp(value))


class TimestampTests(unittest.TestCase):
    def test_iso_variants_normalize_to_utc(self):
        self.assertEqual(n("2026-11-01T10:00:00Z"), "2026-11-01T10:00:00.000Z")
        self.assertEqual(n("2026-11-01T15:30:00.123+05:30"), "2026-11-01T10:00:00.123Z")
        self.assertEqual(n("2026-11-01 10:00:00,123"), "2026-11-01T10:00:00.123Z")  # comma fraction, naive = UTC
        self.assertEqual(n("2026-11-01T02:00:00-08:00"), "2026-11-01T10:00:00.000Z")
        self.assertEqual(n("2026-11-01T10:00:00.123456789Z"), "2026-11-01T10:00:00.123Z")  # truncated to ms

    def test_epoch_seconds_and_milliseconds(self):
        self.assertEqual(n(1793527800), "2026-11-01T10:10:00.000Z")
        self.assertEqual(n(1793527800123), "2026-11-01T10:10:00.123Z")
        self.assertEqual(n("1793527800123"), "2026-11-01T10:10:00.123Z")

    def test_apache_clf_format(self):
        self.assertEqual(n("01/Nov/2026:15:30:00 +0530"), "2026-11-01T10:00:00.000Z")

    def test_offset_can_cross_midnight(self):
        self.assertEqual(n("2026-11-02T01:00:00+05:30"), "2026-11-01T19:30:00.000Z")

    def test_invalid_values_raise(self):
        for bad in ["yesterday", "", "2026-13-45 99:99:99", "2026-02-30T10:00:00Z", None, True, [], {},
                    0, "0", -5, float("nan"), "2026-11-01", "2026-11-01T10:00:00+99:00", 99999999999999999999]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_timestamp(bad)

    def test_hour_bucket(self):
        self.assertEqual(hour_bucket(normalize_timestamp("2026-11-01T10:59:59.999Z")), "2026-11-01T10:00:00Z")


class SeverityTests(unittest.TestCase):
    def test_synonyms(self):
        cases = {"warn": "WARNING", "WARNING": "WARNING", " Err ": "ERROR", "fatal": "CRITICAL",
                 "crit": "CRITICAL", "trace": "DEBUG", "Info": "INFO", "severe": "ERROR"}
        for raw, expected in cases.items():
            self.assertEqual(normalize_severity(raw), expected)

    def test_unknown_or_non_string_raises(self):
        for bad in ["LOUD", "", "42", None, 3, ["ERROR"]]:
            with self.assertRaises(ValueError):
                normalize_severity(bad)


if __name__ == "__main__":
    unittest.main()
