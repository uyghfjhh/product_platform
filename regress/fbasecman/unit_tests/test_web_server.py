import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.cli import build_parser
from tools.web_server import (
    _discover_all_suites,
    _parse_report,
    _get_case_status_and_meta,
    ApiHandler,
    ThreadingHTTPServer,
    TaskManager,
)


class WebServerUnitTest(unittest.TestCase):
    def test_cli_web_parser(self):
        parser = build_parser()
        args = parser.parse_args(["web", "--host", "127.0.0.1", "--port", "9999"])
        self.assertEqual("web", args.command)
        self.assertEqual("127.0.0.1", args.host)
        self.assertEqual(9999, args.port)

    def test_discover_all_suites(self):
        suites = _discover_all_suites()
        self.assertEqual(10, len(suites))

        suite_ids = [s["id"] for s in suites]
        expected_ids = [
            "high_availability",
            "ha_commands",
            "outstanding",
            "global_cache",
            "handover",
            "sql_parse",
            "rw_toggle",
            "guc",
            "common",
            "tmp",
        ]
        self.assertEqual(set(expected_ids), set(suite_ids))

        total_cases = sum(s["total_cases"] for s in suites)
        self.assertGreater(total_cases, 100)

        for s in suites:
            self.assertTrue(s["title"])
            self.assertTrue(s["description"])
            self.assertGreater(len(s["cases"]), 0)
            for c in s["cases"]:
                self.assertTrue(c["id"])
                self.assertTrue(c["name"])
                self.assertTrue(c["target"])
                self.assertIn(c["status"], ("PASS", "FAIL", "RUNNING", "UNTESTED"))
                self.assertIn("duration", c)
                self.assertIn("has_report", c)
                self.assertIn("summary", c)

    def test_parse_report_existing_case(self):
        report = _parse_report("high_availability.core_13_monitor_confirm")
        self.assertTrue(report["found"])
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["start_time"])
        self.assertTrue(report["end_time"])
        self.assertTrue(report["purpose"])
        self.assertGreater(len(report["steps"]), 0)
        self.assertGreater(len(report["assertions"]), 0)
        self.assertTrue(any(s.get("state_table") for s in report["steps"]))
        self.assertIn("fbasecman.log", report["available_logs"])
        self.assertTrue(len(report["raw_text"]) > 0)

    def test_parse_report_nonexistent_case(self):
        report = _parse_report("nonexistent_suite.no_such_case")
        self.assertFalse(report["found"])
        self.assertIn("error", report)

    def test_task_manager_lifecycle(self):
        tm = TaskManager()
        self.assertFalse(tm.is_running)
        status = tm.get_status()
        self.assertEqual("idle", status["status"])
        self.assertFalse(status["active"])


class WebServerHttpIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bind to 127.0.0.1 on an OS-assigned ephemeral port (port 0)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        cls.host, cls.port = cls.server.server_address
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp.getheaders(), data

    def _post(self, path, body_dict):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        conn.request("POST", path, body=json.dumps(body_dict), headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp.getheaders(), data

    def test_static_index_html(self):
        status, headers, data = self._get("/")
        self.assertEqual(200, status)
        content = data.decode("utf-8")
        self.assertIn("fbasecman", content)
        self.assertIn("viewDeploy", content)
        self.assertIn("viewRegress", content)
        self.assertIn("viewStable", content)
        self.assertIn("cardFailedCases", content)
        self.assertIn("failedCasesModalOverlay", content)
        self.assertIn("filterBanner", content)

    def test_static_style_css(self):
        status, headers, data = self._get("/style.css")
        self.assertEqual(200, status)
        content = data.decode("utf-8")
        self.assertIn("--bg-primary", content)

    def test_static_app_js(self):
        status, headers, data = self._get("/app.js")
        self.assertEqual(200, status)
        content = data.decode("utf-8")
        self.assertIn("renderTree", content)

    def test_api_suites(self):
        status, headers, data = self._get("/api/suites")
        self.assertEqual(200, status)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual("ok", body["status"])
        self.assertEqual(10, len(body["suites"]))

    def test_api_stats(self):
        status, headers, data = self._get("/api/stats")
        self.assertEqual(200, status)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual("ok", body["status"])
        self.assertEqual(10, body["total_suites"])
        self.assertGreater(body["total_cases"], 100)
        self.assertIn("passed", body)
        self.assertIn("failed", body)
        self.assertIn("untested", body)

    def test_api_report(self):
        status, headers, data = self._get("/api/report?target=high_availability.core_13_monitor_confirm")
        self.assertEqual(200, status)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual("ok", body["status"])
        self.assertTrue(body["report"]["found"])
        self.assertEqual("PASS", body["report"]["status"])

    def test_api_task_status_idle(self):
        status, headers, data = self._get("/api/task")
        self.assertEqual(200, status)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual("ok", body["status"])
        self.assertFalse(body["task"]["active"])

    def test_api_run_missing_target(self):
        status, headers, data = self._post("/api/run", {})
        self.assertEqual(400, status)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual("error", body["status"])

    def test_api_junit_xml(self):
        status, headers, data = self._get("/api/junit.xml")
        self.assertEqual(200, status)
        header_dict = dict(headers)
        self.assertIn("application/xml", header_dict.get("Content-Type", ""))
        xml_str = data.decode("utf-8")
        self.assertIn("<testsuites", xml_str)
        self.assertIn("<testsuite", xml_str)

    def test_parse_report_topology(self):
        report = _parse_report("high_availability.core_13_monitor_confirm")
        self.assertTrue(report["found"])
        self.assertIn("topology", report)
        topo = report["topology"]
        if topo:
            self.assertTrue(topo["has_topology"])
            self.assertGreater(len(topo["clusters"]), 0)
            cl = topo["clusters"][0]
            self.assertIn("name", cl)
            self.assertIn("nodes", cl)

    def test_parse_report_topology_snapshots_and_proxy_state(self):
        report = _parse_report("high_availability.core_13_monitor_confirm")
        self.assertTrue(report["found"])
        topo = report.get("topology")
        self.assertIsNotNone(topo)
        snapshots = topo.get("step_snapshots", [])
        self.assertGreaterEqual(len(snapshots), 5)
        
        # Step 1: Init / Healthy
        s1 = snapshots[0]
        self.assertEqual(s1["proxy_state"]["status"], "HEALTHY")
        self.assertIn("探活就绪", s1["proxy_state"]["badge"])
        
        # Step 3: Debounce
        s3 = snapshots[2]
        self.assertEqual(s3["proxy_state"]["status"], "DEBOUNCING")
        self.assertIn("防抖", s3["proxy_state"]["badge"])
        
        # Step 4: Degraded
        s4 = snapshots[3]
        self.assertEqual(s4["proxy_state"]["status"], "DEGRADED")
        self.assertIn("降级", s4["proxy_state"]["badge"])
        self.assertIn("VALID_DEGRADED", s4["proxy_state"]["topology_status"])
        
        # Step 5: Recovered
        s5 = snapshots[4]
        self.assertEqual(s5["proxy_state"]["status"], "RECOVERED")
        self.assertIn("准入", s5["proxy_state"]["badge"])


if __name__ == "__main__":
    unittest.main()
