"""0.2.17: the Host allowlist (A-055), the kernel check (#44) and the password eye (#70)."""

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "hub"), str(ROOT / "reporter")]

import mindful_fleet_hub as hub  # noqa: E402
import mindful_fleet_reporter as reporter  # noqa: E402


class HostRuleTests(unittest.TestCase):
    def test_local_addresses_allowed(self):
        for host in ("192.0.2.10", "192.0.2.10:8080", "127.0.0.1", "[::1]", "[fe80::1]:8080", "localhost:8080",
                     "hub", "hub:8080", "hub.local", "HUB.LOCAL.", "hub.lan", "hub.home.arpa", "hub.internal",
                     "hub.tail1234.ts.net", None, ""):
            self.assertTrue(hub.host_allowed(host), host)

    def test_other_websites_refused(self):
        for host in ("evil.example", "evil.example:8080", "192.0.2.10.evil.example", "local.evil.example",
                     "hub.local.evil.example", "[not-an-ip]", "[]", ":8080"):
            self.assertFalse(hub.host_allowed(host), host)

    def test_owner_can_add_names(self):
        extra = hub.allowed_hosts_setting(" Fleet.Example.com. , ,other.example ")
        self.assertEqual(extra, ("fleet.example.com", "other.example"))
        self.assertTrue(hub.host_allowed("fleet.example.com:8080", extra))
        self.assertFalse(hub.host_allowed("evil.example", extra))
        self.assertEqual(hub.allowed_hosts_setting(None), ())
        tunnel = hub.allowed_hosts_setting(".trycloudflare.com")
        self.assertTrue(hub.host_allowed("calm-river-1234.trycloudflare.com", tunnel))
        self.assertFalse(hub.host_allowed("trycloudflare.com.evil.example", tunnel))


class HostGuardServerTests(unittest.TestCase):
    def setUp(self):
        self.fleet = hub.Fleet()
        handler = hub.make_handler(self.fleet, "secret", allowed_hosts=("fleet.example.com",))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, host, data=None, method="GET", headers=None):
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Host": host, **(headers or {})})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_dashboard_and_api_refuse_a_foreign_name(self):
        for path in ("/", "/app.js", "/api/fleet", "/api/layout"):
            status, body = self.call(path, "evil.example")
            self.assertEqual(status, 403, path)
            self.assertIn(b"MINDFUL_FLEET_ALLOWED_HOSTS", body)
        status, _ = self.call("/api/layout", "evil.example", data=b"{}", method="PUT",
                              headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _ = self.call("/api/sign-in", "evil.example", data=b"{}", method="POST",
                              headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)

    def test_own_addresses_still_work(self):
        for host in (f"127.0.0.1:{self.server.server_port}", "hub.local", "fleet.example.com"):
            self.assertEqual(self.call("/api/fleet", host)[0], 200, host)

    def test_reports_from_the_pis_still_land(self):
        # The Pis' reports are checked by the fleet token, whatever name they use for the hub.
        report = {"id": "abcdef0123456789", "hostname": "test-pi", "reporter_version": "0.2.17", "temp_c": 50.0,
                  "cpu_per_core": [10.0], "page_kb": 16, "huge_pages": 0, "thp": "madvise"}
        status, _ = self.call("/api/report", "hub.example.org", data=json.dumps(report).encode(), method="POST",
                              headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/healthz", "hub.example.org")[0], 200)


class KernelCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, path, text):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def test_reads_huge_pages_and_the_automatic_setting(self):
        self.write("/proc/meminfo", "MemTotal:  8000000 kB\nHugePages_Total:     128\nHugePages_Free: 128\n")
        self.write("/sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never\n")
        info = reporter.read_kernel_memory(str(self.root))
        self.assertEqual((info["huge_pages"], info["thp"]), (128, "madvise"))
        self.assertIsNone(info["page_kb"])  # Page size is read only on the real system.

    def test_missing_files_mean_unknown(self):
        self.assertEqual(reporter.read_kernel_memory(str(self.root)), {"page_kb": None, "huge_pages": None, "thp": None})

    def test_this_computer_reports_a_page_size(self):
        self.assertGreaterEqual(reporter.read_kernel_memory("/")["page_kb"], 4)

    def test_hub_keeps_only_known_values(self):
        base = {"id": "abcdef0123456789", "hostname": "test-pi"}
        kept = hub.clean_report({**base, "page_kb": 16, "huge_pages": 0, "thp": "never"})
        self.assertEqual((kept["page_kb"], kept["huge_pages"], kept["thp"]), (16, 0, "never"))
        self.assertIsNone(hub.clean_report({**base, "thp": "<b>yes</b>"})["thp"])
        with self.assertRaises(hub.BadReport):  # Like every other reading, a number out of range refuses the report.
            hub.clean_report({**base, "page_kb": -4})


class PasswordEyeTests(unittest.TestCase):
    def test_every_password_box_gets_an_eye(self):
        app = (ROOT / "hub" / "dashboard" / "app.js").read_text()
        eyes = app[app.index("function setUpPasswordEyes("):app.index('/* "How do I set it?"')]
        self.assertIn("document.querySelectorAll('input[type=\"password\"]')", eyes)
        self.assertIn('input.type = visible ? "text" : "password"', eyes)
        self.assertIn('addEventListener("submit", () => show(false))', eyes)
        self.assertIn("setUpPasswordEyes();", app)


if __name__ == "__main__":
    unittest.main()
