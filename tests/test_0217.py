"""0.2.17: the Host allowlist (A-055), the kernel check (#44) and the password eye (#70)."""

import json
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock
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
                     "hub.tail1234.ts.net", "raspberrypi.home", "hub.fritz.box", "hub.attlocal.net", None):
            self.assertTrue(hub.host_allowed(host), host)

    def test_other_websites_refused(self):
        for host in ("evil.example", "evil.example:8080", "192.0.2.10.evil.example", "local.evil.example",
                     "hub.local.evil.example", "[not-an-ip]", "[]", ":8080", "", "[::1]evil.com", "[::1]:80:evil",
                     "hub.local:evil", "evil.com, hub.local", "evil:x:y", "hub\x00.local", "hub.local:99999x"):
            self.assertFalse(hub.host_allowed(host), host)

    def test_owner_can_add_names(self):
        extra = hub.allowed_hosts_setting(" Fleet.Example.com. , ,other.example ")
        self.assertEqual(extra, ("fleet.example.com", "other.example"))
        self.assertTrue(hub.host_allowed("fleet.example.com:8080", extra))
        self.assertFalse(hub.host_allowed("evil.example", extra))
        self.assertEqual(hub.allowed_hosts_setting(None), ())
        self.assertEqual(hub.allowed_hosts_setting("fleet.example.com:8443, *.trycloudflare.com, bad name, <x>"),
                         ("fleet.example.com", ".trycloudflare.com"))
        self.assertIn(hub.socket.gethostname().lower(), hub.own_names())
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
            self.assertIn(b'"evil.example"', body)
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
        self.assertEqual(self.call("/healthz", "hub.example.org")[0], 403)  # The version stays private too.


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
        for bad in (-4, 16.5, "16", True, 1 << 40):
            self.assertIsNone(hub.clean_report({**base, "page_kb": bad, "huge_pages": bad})["page_kb"], bad)


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


class UpsTests(unittest.TestCase):
    """UPS through NUT (#58)."""

    UPSC = "battery.charge: 84\nbattery.runtime: 1500\nups.load: 13\nups.status: OB DISCHRG\ndevice.mfr: Tripp Lite\n"

    def fake_run(self, outputs):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            out, code = outputs[" ".join(cmd)]
            return type("Done", (), {"stdout": out, "returncode": code})()
        return run, calls

    def test_reads_the_first_ups_nut_lists(self):
        run, calls = self.fake_run({"upsc -l": ("tripplite\n", 0), "upsc tripplite@localhost": (self.UPSC, 0)})
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/upsc"):
            ups = reporter.read_ups(run)
        self.assertEqual(ups, {"name": "tripplite", "status": "OB DISCHRG", "charge": 84.0, "runtime_s": 1500.0, "load": 13.0})

    def test_no_nut_means_no_ups(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            self.assertIsNone(reporter.read_ups(lambda *a, **k: None))
        run, _ = self.fake_run({"upsc -l": ("", 1)})
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/upsc"):
            self.assertIsNone(reporter.read_ups(run))

    def test_hub_checks_it(self):
        base = {"id": "abcdef0123456789", "hostname": "test-pi"}
        kept = hub.clean_report({**base, "ups": {"name": "tripplite", "status": "OL CHRG", "charge": 100, "load": 13}})
        self.assertEqual(kept["ups"]["status"], "OL CHRG")
        for bad in ({"status": "<script>"}, {"status": "OL", "charge": 400}, "OL", None):
            self.assertIsNone(hub.clean_report({**base, "ups": bad})["ups"], bad)

    def test_card_warns_on_battery(self):
        import health
        on_battery = health.assess({"ups": {"status": "OB DISCHRG", "charge": 84, "runtime_s": 1500}}, 1)
        self.assertEqual(on_battery["status"], "warn")
        self.assertIn("running on battery: 84% left, about 25 minutes", on_battery["issues"][0]["text"])
        low = health.assess({"ups": {"status": "OB LB", "charge": 9}}, 1)
        self.assertEqual(low["status"], "bad")
        self.assertEqual(health.assess({"ups": {"status": "OL CHRG"}}, 1)["status"], "ok")


class PoolTestTests(unittest.TestCase):
    """Pool connection test (#36): a local listener stands in for the pool."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import mindful_fleet_tools
        self.tools = mindful_fleet_tools
        self.lines = []

    def test_plain_port_answers_without_tls(self):
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        self.addCleanup(server.close)

        def accept():
            for _ in range(2):
                try:
                    conn, _ = server.accept()
                    conn.close()
                except OSError:
                    return
        threading.Thread(target=accept, daemon=True).start()
        port = server.getsockname()[1]
        self.assertEqual(self.tools.pool_test(f"127.0.0.1:{port}", timeout=2, say=self.lines.append), 0)
        self.assertIn("✓ The pool answers", "\n".join(self.lines))
        self.assertIn("Not encrypted", "\n".join(self.lines))

    def test_closed_port_and_bad_names(self):
        import socket
        spare = socket.socket()
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
        spare.close()
        self.assertEqual(self.tools.pool_test(f"127.0.0.1:{port}", timeout=1, say=self.lines.append), 1)
        self.assertIn("Nothing answers", self.lines[-1])
        with unittest.mock.patch("socket.getaddrinfo", side_effect=socket.gaierror):
            self.assertEqual(self.tools.pool_test("no-such-pool.example:3333", say=self.lines.append), 1)
        self.assertIn("doesn't resolve", self.lines[-1])
        self.assertEqual(self.tools.pool_test("not a pool; rm", say=self.lines.append), 2)
        with unittest.mock.patch.object(self.tools, "xmrig_pool", return_value=None):
            self.assertEqual(self.tools.pool_test(None, say=self.lines.append), 2)


class HubInstallerKeepsSettingsTests(unittest.TestCase):
    """The review's blocking finding: an update rewrote hub.env and erased MINDFUL_FLEET_ALLOWED_HOSTS."""

    def test_extra_lines_survive_a_reinstall(self):
        import subprocess
        script = (ROOT / "install" / "install-hub.sh").read_text()
        block = script[script.index('EXTRA=""'):script.index("chown root:mindful-fleet /etc/mindful-fleet/hub.env")]
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        block = block.replace("/etc/mindful-fleet", str(folder))
        env = folder / "hub.env"
        env.write_text("MINDFUL_FLEET_TOKEN=keepme\nMINDFUL_FLEET_PORT=8080\nMINDFUL_FLEET_STATE=/old\n"
                       "MINDFUL_FLEET_ALLOWED_HOSTS=.trycloudflare.com,fleet.example.com\n")
        for _ in range(2):
            subprocess.run(["bash", "-c", "set -euo pipefail; PORT=8080\n" + block], check=True, timeout=30)
        lines = env.read_text().splitlines()
        self.assertEqual(lines, ["MINDFUL_FLEET_TOKEN=keepme", "MINDFUL_FLEET_PORT=8080",
                                 "MINDFUL_FLEET_STATE=/var/lib/mindful-fleet/fleet.json",
                                 "MINDFUL_FLEET_ALLOWED_HOSTS=.trycloudflare.com,fleet.example.com"])
