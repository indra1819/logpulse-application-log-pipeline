"""OpenStack / oslo log layout (e.g. the Loghub OpenStack dataset).

Sample lines are short excerpts of real OpenStack nova logs quoted in public bug reports.
"""
import unittest

from logpipe.errors import RejectError
from logpipe.parser import parse_line
from tests.helpers import SERVICES_CSV, PipelineTestCase

API_1 = ('2017-06-15 02:42:01.342 2382 INFO nova.osapi_compute.wsgi.server '
         '[req-9733fa84-670f-4ff3-9e2e-8628c82d60cd 392094dd64934698af5da203277a2449 '
         'e22e05b259104aea879f57a9e197b210 - - -] "GET /v2/e22e05b259104aea879f57a9e197b210 HTTP/1.1" '
         'status: 404 len: 264 time: 19.5361440')
API_2 = ('2017-06-29 12:59:37.444 76685 INFO nova.osapi_compute.wsgi.server [-] 172.17.1.18 '
         '"OPTIONS / HTTP/1.0" status: 200 len: 499 time: 0.0013390')
API_500 = ('2017-06-29 12:59:37.662 76698 INFO nova.metadata.wsgi.server [req-4ab78206-2e39-484f-8935-a16fce4a3c22 - - - - -] '
           '10.100.0.7,172.17.1.18 "GET /2009-04-04/meta-data/instance-id HTTP/1.1" status: 500 len: 229 time: 1.1885862')
ERR = '2017-06-29 12:59:37.658 76698 ERROR nova.api.metadata.handler     return self._set("set", key, val, time, min_compress_len)'
WITH_PREFIX = "nova-api.log.1.2017-05-16_13:53:08 " + API_2


class OpenStackParserTests(unittest.TestCase):
    def test_http_request_line(self):
        f = parse_line(API_1)
        self.assertEqual((f["service"], f["component"], f["level"], f["status"], f["endpoint"]),
                         ("nova", "nova.osapi_compute.wsgi.server", "INFO", "404", "/v2/e22e05b259104aea879f57a9e197b210"))
        self.assertEqual((f["request_id"], f["latency_s"]), ("req-9733fa84-670f-4ff3-9e2e-8628c82d60cd", "19.5361440"))
        self.assertNotIn("client_host", f)

    def test_client_ip_and_forwarded_list(self):
        self.assertEqual(parse_line(API_2)["client_host"], "172.17.1.18")
        self.assertEqual(parse_line(API_500)["client_host"], "10.100.0.7,172.17.1.18")

    def test_non_http_line_keeps_message_only(self):
        f = parse_line(ERR)
        self.assertEqual((f["level"], f["service"], f["component"]), ("ERROR", "nova", "nova.api.metadata.handler"))
        self.assertTrue(f["message"].startswith("return self._set"))
        self.assertNotIn("latency_s", f)

    def test_loghub_style_file_name_prefix_gives_the_service(self):
        f = parse_line(WITH_PREFIX)
        self.assertEqual((f["timestamp"], f["service"], f["component"]),
                         ("2017-06-29 12:59:37.444", "nova-api", "nova.osapi_compute.wsgi.server"))
        compute = "nova-compute.log.1.2017-05-16_13:55:31 " + ERR
        self.assertEqual(parse_line(compute)["service"], "nova-compute")

    def test_traceback_continuation_line_is_malformed(self):
        with self.assertRaises(RejectError):
            parse_line("    raise exception.NovaException(msg)")


class OpenStackPipelineTests(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.services.write_text(SERVICES_CSV + "nova,openstack-compute\nnova-api,openstack-compute\n")

    def test_latency_error_rate_and_units(self):
        self.write_log("nova-api.log", [API_1, API_2, API_500, ERR, WITH_PREFIX.replace("12:59:37.444", "12:59:38.000")])
        result = self.run_pipeline()
        self.assertEqual((result.status, result.metrics["rows_loaded"]), ("success", 5))
        self.assertTrue(result.checks_passed)
        self.assertEqual(self.scalar("SELECT latency_ms FROM log_events WHERE http_status=404"), 19536.144)  # seconds -> ms
        row = self.query("SELECT SUM(event_count), SUM(error_count), SUM(latency_count) FROM service_hourly_summary")[0]
        self.assertEqual(row, (5, 2, 4))  # ERROR line and the 500 are errors; ERR line has no latency
        self.assertEqual(self.scalar("SELECT component FROM log_events WHERE http_status=404"), "nova.osapi_compute.wsgi.server")

    def test_unknown_openstack_service_is_quarantined_with_reason(self):
        self.write_log("x.log", [API_2.replace("nova.", "neutron.")])
        result = self.run_pipeline()
        self.assertEqual(result.metrics["rows_loaded"], 0)
        self.assertEqual(self.scalar("SELECT reject_reason FROM rejected_records"), "unknown_service")


if __name__ == "__main__":
    unittest.main()
