import json
import os
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

import health  # noqa: E402
import mindful_fleet_hub as hub  # noqa: E402
import mindful_fleet_reporter as reporter  # noqa: E402

GIB = 1024**3


def report(**changes):
    base = {
        "id": "abcdef0123456789", "hostname": "test-pi", "ip": "192.168.1.5", "model": "x",
        "reporter_version": "0.2.0", "temp_c": 50.0, "throttled": 0, "fan_rpm": 2000,
        "cpu_per_core": [100.0] * 4, "mem_total": 8 * GIB, "mem_used": 2 * GIB,
        "disk_total": 64 * GIB, "disk_used": 10 * GIB, "uptime_s": 100,
    }
    base.update(changes)
    return base


class HealthTests(unittest.TestCase):
    def status(self, **changes):
        return health.assess(report(**changes), 5)["status"]

    def test_healthy(self):
        self.assertEqual(self.status(), "ok")

    def test_full_cpu_alone_is_fine_because_miners_run_flat_out(self):
        self.assertEqual(self.status(cpu_per_core=[100.0] * 4), "ok")

    def test_temperature_bands(self):
        self.assertEqual(self.status(temp_c=69.9), "ok")
        self.assertEqual(self.status(temp_c=70), "warn")
        self.assertEqual(self.status(temp_c=80), "bad")

    def test_warning_text_rounds_like_the_dashboard(self):
        issue = health.assess(report(temp_c=76.5), 5)["issues"][0]["text"]
        self.assertEqual(issue, "It's running warm at 77°C.")

    def test_fan_resting_while_cool_is_fine(self):
        result = health.assess(report(temp_c=45.0, fan_rpm=0), 5, fan_max_rpm=5000)
        self.assertEqual((result["status"], result["fan"]["text"]), ("ok", "Resting"))

    def test_stopped_fan_while_warm_is_flagged(self):
        result = health.assess(report(temp_c=65.0, fan_rpm=0), 5, fan_max_rpm=5000)
        self.assertEqual((result["status"], result["fan"]["text"]), ("warn", "Not spinning"))

    def test_zero_rpm_on_a_board_never_seen_spinning_is_not_a_warning(self):
        result = health.assess(report(temp_c=75.0, fan_rpm=0), 5)
        self.assertEqual(result["fan"]["text"], "No fan detected")
        self.assertFalse([i for i in result["issues"] if "fan" in i["text"]])

    def test_fan_percent_uses_board_value_then_fastest_seen(self):
        self.assertEqual(health.assess(report(fan_percent=70), 5, 5000)["fan"]["percent"], 70)
        self.assertEqual(health.assess(report(fan_rpm=2500), 5, 5000)["fan"]["percent"], 50)
        self.assertIsNone(health.assess(report(fan_rpm=None), 5)["fan"]["percent"])

    def test_cores_in_use(self):
        cpu = health.assess(report(cpu_per_core=[100.0, 99.0, 98.0, 3.0]), 5)["cpu"]
        self.assertEqual((cpu["busy"], cpu["total"], cpu["average"]), (3, 4, 75.0))

    def test_no_fan_reading_is_not_alarming(self):
        self.assertEqual(self.status(fan_rpm=None), "ok")

    def test_throttle_bits(self):
        cases = {
            0x0: ("ok", "Full speed", "Steady"),
            0x1: ("bad", "Full speed", "Low voltage right now"),
            0x4: ("bad", "Slowed down right now", "Steady"),
            0x2: ("warn", "Holding back to stay cool", "Steady"),
            0x8: ("warn", "Holding back to stay cool", "Steady"),
            0x10000: ("warn", "Full speed", "Low voltage earlier"),
            0x40000: ("warn", "Slowed down earlier", "Steady"),
            0x50005: ("bad", "Slowed down right now", "Low voltage right now"),
        }
        for bits, (status, speed, power) in cases.items():
            with self.subTest(bits=hex(bits)):
                result = health.assess(report(throttled=bits), 5)
                self.assertEqual((result["status"], result["speed"]["text"], result["power"]["text"]),
                                 (status, speed, power))

    def test_missing_vcgencmd_is_not_alarming(self):
        result = health.assess(report(throttled=None), 5)
        self.assertEqual((result["status"], result["power"]["text"]), ("ok", "Not available"))

    def test_disk_and_memory_bands(self):
        self.assertEqual(self.status(disk_used=52 * GIB), "warn")
        self.assertEqual(self.status(disk_used=60 * GIB), "bad")
        self.assertEqual(self.status(mem_used=7 * GIB), "warn")
        self.assertEqual(self.status(mem_used=int(7.8 * GIB)), "bad")

    def test_offline_beats_everything(self):
        result = health.assess(report(temp_c=90.0), 660)
        self.assertEqual(result["status"], "offline")
        self.assertEqual(result["issues"][0]["text"], "Hasn't checked in for 11 minutes.")

    def test_all_missing_numbers_do_not_crash(self):
        blank = {"id": "abcdef01", "hostname": "x"}
        self.assertEqual(health.assess(hub.clean_report(blank), 5)["status"], "ok")


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        files = {
            "sys/class/thermal/thermal_zone0/temp": "61250\n",
            "sys/class/hwmon/hwmon2/fan1_input": "3120\n",
            "proc/uptime": "12345.67 999.0\n",
            "proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
            "etc/machine-id": "aaaa\n",
            "proc/meminfo": "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n",
            "proc/stat": "cpu  1 1 1 1\ncpu0 10 0 10 60 20 0 0 0 0 0\ncpu1 0 0 0 100 0 0 0 0 0 0\nintr 1\n",
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def test_reads_pi_files(self):
        root = str(self.root)
        self.assertEqual(reporter.read_temperature_c(root), 61.2)
        self.assertEqual(reporter.read_fan_rpm(root), 3120)
        self.assertEqual(reporter.read_uptime(root), 12345)
        self.assertEqual(reporter.read_model(root), "Raspberry Pi 5 Model B Rev 1.0")
        self.assertEqual(reporter.read_memory(root), (1000 * 1024, 600 * 1024))

    def test_cpu_percent_treats_iowait_as_idle(self):
        before = {0: (0, 0), 1: (0, 0)}
        after = reporter.read_cpu_times(str(self.root))
        self.assertEqual(reporter.cpu_percent(before, after), [20.0, 0.0])

    def test_fan_percent_and_volts(self):
        (self.root / "sys/class/hwmon/hwmon2/pwm1").write_text("255\n")
        self.assertEqual(reporter.read_fan_percent(str(self.root)), 100)

        class Result:
            returncode = 0
            stdout = "     EXT5V_V volt(24)=5.13600000V\n"
        self.assertEqual(reporter.read_input_volts(lambda *a, **k: Result()), 5.14)

        class Old:
            returncode = 0
            stdout = "error=2 error_msg=\"Invalid arguments\"\n"
        self.assertIsNone(reporter.read_input_volts(lambda *a, **k: Old()))

    def test_missing_files_give_none(self):
        empty = tempfile.mkdtemp(dir=self.dir.name)
        self.assertIsNone(reporter.read_temperature_c(empty))
        self.assertIsNone(reporter.read_fan_rpm(empty))
        self.assertEqual(reporter.read_memory(empty), (None, None))

    def test_throttle_parsing(self):
        class Result:
            returncode = 0
            stdout = "throttled=0x50005\n"
        self.assertEqual(reporter.read_throttled(lambda *a, **k: Result()), 0x50005)

        def missing(*a, **k):
            raise FileNotFoundError
        self.assertIsNone(reporter.read_throttled(missing))

    def test_identity_separates_clones_and_same_names(self):
        root = str(self.root)
        self.assertNotEqual(reporter.machine_identity("one", root), reporter.machine_identity("two", root))
        first = reporter.machine_identity("raspberrypi", root)
        (self.root / "etc/machine-id").write_text("bbbb\n")
        self.assertNotEqual(first, reporter.machine_identity("raspberrypi", root))


class HubTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.fleet = hub.Fleet(clock=lambda: self.now[0])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(self.fleet, "secret"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def post(self, body, token="secret"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = urllib.request.Request(self.base + "/api/report", data=data, method="POST",
                                         headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def fleet_json(self):
        with urllib.request.urlopen(self.base + "/api/fleet") as response:
            return json.load(response)

    def test_report_appears_on_fleet(self):
        self.assertEqual(self.post(report()), 200)
        pi = self.fleet_json()["pis"][0]
        self.assertEqual((pi["hostname"], pi["health"]["status"]), ("test-pi", "ok"))

    def test_wrong_token_refused(self):
        self.assertEqual(self.post(report(), token="wrong"), 401)
        self.assertEqual(self.fleet_json()["pis"], [])

    def test_malformed_reports_refused(self):
        bad = [b"not json", report(hostname="<img src=x onerror=alert(1)>"), report(id="../etc"),
               report(temp_c="hot"), report(temp_c=True), report(cpu_per_core=[500]), [1, 2]]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.post(body), 400)
        self.assertEqual(self.fleet_json()["pis"], [])

    def test_infinity_refused(self):
        self.assertEqual(self.post(json.dumps(report()).replace("50.0", "Infinity").encode()), 400)

    def test_oversized_report_refused(self):
        self.assertEqual(self.post(b"x" * (hub.MAX_BODY_BYTES + 1)), 413)

    def test_extra_fields_dropped(self):
        self.post(report(surprise="data"))
        self.assertNotIn("surprise", self.fleet_json()["pis"][0])

    def test_goes_offline_then_recovers(self):
        self.post(report())
        self.now[0] += 61
        self.assertEqual(self.fleet_json()["pis"][0]["health"]["status"], "offline")
        self.post(report())
        self.assertEqual(self.fleet_json()["pis"][0]["health"]["status"], "ok")

    def test_dashboard_files_served_with_security_headers(self):
        for path in ("/", "/app.js", "/style.css"):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_no_path_traversal(self):
        for path in ("/../health.py", "/health.py", "/dashboard/app.js"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(caught.exception.code, 404)


class OrderTests(unittest.TestCase):
    def test_spelled_and_written_numbers_sort_in_order(self):
        names = ["BrainTwo", "BrainFive", "BrainOne", "pi10", "pi2"]
        self.assertEqual(sorted(names, key=hub._natural_key), ["BrainOne", "BrainTwo", "BrainFive", "pi2", "pi10"])

    def test_core_averages(self):
        self.assertEqual(hub.core_averages([[100, 0], [50, 0]]), [75.0, 0.0])
        self.assertEqual(hub.core_averages([]), [])


class StateTests(unittest.TestCase):
    def test_dead_pi_is_remembered_across_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "fleet.json")
            first = hub.Fleet(path, clock=lambda: 1000.0)
            first.record(hub.clean_report(report()))
            first.save()
            second = hub.Fleet(path, clock=lambda: 2000.0)
            pi = second.snapshot()["pis"][0]
            self.assertEqual((pi["hostname"], pi["health"]["status"], pi["history"]), ("test-pi", "offline", [50.0]))

    def test_corrupt_state_starts_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fleet.json"
            path.write_text("{broken")
            self.assertEqual(hub.Fleet(path).snapshot()["pis"], [])


if __name__ == "__main__":
    unittest.main()
