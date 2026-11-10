import json
import subprocess
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
import averages  # noqa: E402

GIB = 1024**3


def report(**changes):
    base = {
        "id": "abcdef0123456789", "hostname": "test-pi", "ip": "192.168.1.5", "model": "x",
        "reporter_version": "0.2.9", "temp_c": 50.0, "throttled": 0, "fan_rpm": 2000,
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

    def test_clock_load_swap_network_os_and_board(self):
        root = self.root
        for core, khz in ((0, "2400000"), (1, "1500000"), (10, "2400000")):
            folder = root / f"sys/devices/system/cpu/cpu{core}/cpufreq"
            folder.mkdir(parents=True)
            (folder / "scaling_cur_freq").write_text(khz + "\n")
            (folder / "cpuinfo_max_freq").write_text("2400000\n")
        self.assertEqual(reporter.read_cpu_mhz(str(root)), ([2400, 1500, 2400], 2400))
        (root / "proc/loadavg").write_text("3.98 3.91 3.80 5/300 1234\n")
        self.assertEqual(reporter.read_load(str(root)), [3.98, 3.91, 3.8])
        (root / "proc/meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 512 kB\nSwapFree: 500 kB\n")
        self.assertEqual(reporter.read_swap(str(root)), (512 * 1024, 12 * 1024))
        (root / "proc/net").mkdir()
        (root / "proc/net/dev").write_text(
            "Inter-| Receive\n face |bytes\n"
            "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
            "  eth0: 1000 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n"
            " wlan0: 500 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n")
        received, sent, _ = reporter.read_net_bytes(str(root))
        self.assertEqual((received, sent), (1500, 300))
        self.assertEqual(reporter.net_rates((0, 0, 10.0), (3000, 600, 12.0)), (1500, 300))
        self.assertEqual(reporter.net_rates(None, (1, 1, 1.0)), (None, None))
        (root / "etc/os-release").write_text('NAME="Debian"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n')
        self.assertEqual(reporter.read_os_name(str(root)), "Debian GNU/Linux 13 (trixie)")
        for code, mb in (("d04170", 8192), ("c04170", 4096), ("e04171", 16384), ("a02082", 1024), ("0010", None)):
            (root / "proc/cpuinfo").write_text(f"Hardware\t: BCM2835\nRevision\t: {code}\n")
            self.assertEqual(reporter.read_board_ram_mb(str(root)), mb, code)

    def test_wifi_disk_and_busiest_program(self):
        root = self.root
        (root / "proc/net").mkdir(exist_ok=True)
        (root / "proc/net/wireless").write_text(
            "Inter-| sta-|   Quality        |\n face | tus | link level noise |\n"
            " wlan0: 0000   54.  -56.  -256        0      0      0      0     11        0\n")
        self.assertEqual(reporter.read_wifi_dbm(str(root)), -56)
        self.assertIsNone(reporter.read_wifi_dbm(tempfile.mkdtemp(dir=self.dir.name)))
        (root / "proc/diskstats").write_text(
            " 179       0 mmcblk0 100 0 9000 0 50 0 3000 0 0 0 0\n"
            " 179       2 mmcblk0p2 80 0 8000 0 40 0 2000 0 0 0 0\n")
        read, written, _ = reporter.read_disk_bytes((179, 2), str(root))
        self.assertEqual((read, written), (8000 * 512, 2000 * 512))
        self.assertIsNone(reporter.read_disk_bytes((8, 1), str(root)))
        for pid, name, ticks in ((10, "xmrig", 1000), (20, "my (odd) prog", 50)):
            folder = root / f"proc/{pid}"
            folder.mkdir()
            (folder / "stat").write_text(f"{pid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20\n")
        before = reporter.read_process_ticks(str(root))
        self.assertEqual(before[0][str(root / "proc/20/stat")], (50, "my (odd) prog"))
        after = ({**before[0], str(root / "proc/10/stat"): (1400, "xmrig")}, before[1] + 1.0)
        self.assertEqual(reporter.busiest_process(before, after, ticks_per_second=100), ("xmrig", 400.0))
        self.assertEqual(reporter.busiest_process(before, before), (None, None))

    def test_power_from_pmic(self):
        class Result:
            returncode = 0
            stdout = ("     VDD_CORE_A current(7)=2.00000000A\n"
                      "     3V3_SYS_A current(1)=0.10000000A\n"
                      "     VDD_CORE_V volt(15)=0.90000000V\n"
                      "     3V3_SYS_V volt(9)=3.30000000V\n"
                      "     EXT5V_V volt(24)=5.11000000V\n")
        self.assertEqual(reporter.read_pmic(lambda *a, **k: Result()), (5.11, 2.13))

        class Pi4:
            returncode = 0
            stdout = "error=2 error_msg=\"Invalid arguments\"\n"
        self.assertEqual(reporter.read_pmic(lambda *a, **k: Pi4()), (None, None))

    def test_power_sensors(self):
        folder = self.root / "sys/class/hwmon/hwmon5"
        folder.mkdir(parents=True)
        (folder / "name").write_text("ina219\n")
        (folder / "in1_input").write_text("12020\n")
        (folder / "curr1_input").write_text("410\n")
        (folder / "power1_input").write_text("4930000\n")
        other = self.root / "sys/class/hwmon/hwmon6"
        other.mkdir(parents=True)
        (other / "name").write_text("ina260\n")
        (other / "in1_input").write_text("5000\n")
        (other / "curr1_input").write_text("200\n")
        found = reporter.read_power_sensors(str(self.root), ["Fan supply"])
        self.assertEqual(found[0], {"label": "Fan supply", "chip": "ina219", "volts": 12.02, "amps": 0.41, "watts": 4.93})
        self.assertEqual((found[1]["label"], found[1]["watts"]), ("Power sensor 2", 1.0))

    def test_core_volts(self):
        class Result:
            returncode = 0
            stdout = "volt=0.8762V\n"
        self.assertEqual(reporter.read_core_volts(lambda *a, **k: Result()), 0.876)

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

    def test_new_fields_accepted_and_checked(self):
        self.assertEqual(self.post(report(cpu_mhz=[2400, 1500], cpu_max_mhz=2400, load_avg=[1, 2, 3],
                                          board_ram_mb=8192, os_name="Debian", net_rx_bps=10)), 200)
        pi = self.fleet_json()["pis"][0]
        self.assertEqual((pi["cpu_mhz"], pi["board_ram_mb"], pi["os_name"]), ([2400, 1500], 8192, "Debian"))
        self.assertEqual(self.post(report(load_avg=[1, 2, 3, 4])), 400)
        self.assertEqual(self.post(report(cpu_mhz="fast")), 400)

    def test_older_reporters_still_accepted(self):
        old = report()
        self.assertEqual(self.post(old), 200)
        self.assertEqual(self.fleet_json()["pis"][0]["cpu_mhz"], [])

    def test_layout_saved_and_cleaned(self):
        def put(body, content_type="application/json"):
            request = urllib.request.Request(self.base + "/api/layout", data=json.dumps(body).encode(), method="PUT",
                                             headers={"Content-Type": content_type})
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        layout = {"order": ["abcdef01", "<bad>"], "groups": [{"id": "gabc", "name": "Miners\u0007"}],
                  "members": {"abcdef01": "gabc", "abcdef02": "gnope"}, "hidden": ["swap"], "size": 60}
        self.assertEqual(put(layout), 200)
        with urllib.request.urlopen(self.base + "/api/layout") as response:
            saved = json.load(response)
        self.assertEqual(saved, {"order": ["abcdef01"], "groups": [{"id": "gabc", "name": "Miners"}],
                                 "members": {"abcdef01": "gabc"}, "hidden": ["swap"], "price": None})
        self.assertEqual(put(layout, "text/plain"), 404)
        self.assertEqual(put([1]), 400)

    def test_latest_version_check(self):
        folder = tempfile.mkdtemp()
        Path(folder, "VERSION").write_text("0.9.1\n")
        stop = threading.Event()
        stop.set()
        hub.check_latest_version(self.fleet, stop, url=Path(folder, "VERSION").as_uri())
        self.assertEqual(self.fleet_json()["latest_version"], "0.9.1")
        Path(folder, "VERSION").write_text("<html>")
        hub.check_latest_version(self.fleet, stop, url=Path(folder, "VERSION").as_uri())
        self.assertEqual(self.fleet.latest_version, "0.9.1")

    def test_history_endpoint(self):
        self.post(report())
        with urllib.request.urlopen(self.base + "/api/history?id=abcdef0123456789") as response:
            data = json.load(response)
        self.assertEqual(data["values"]["temp"], [50.0])
        for bad in ("nope", "../x", "ffffffffffffffff"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + "/api/history?id=" + bad)
            self.assertEqual(caught.exception.code, 404)

    def test_sensors_checked(self):
        self.assertEqual(self.post(report(sensors=[{"label": "Fan", "volts": 12.0, "amps": 0.4, "watts": 4.8}])), 200)
        self.assertEqual(self.fleet_json()["pis"][0]["sensors"][0]["watts"], 4.8)
        self.assertEqual(self.post(report(sensors=[{"watts": "lots"}])), 400)
        self.assertEqual(self.post(report(sensors="x")), 400)

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


class XmrigTests(unittest.TestCase):
    def serve(self, routes, token=None):
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if token and self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = routes.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                if body is not None:
                    self.wfile.write(json.dumps(body).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_reads_summary_and_threads(self):
        url = self.serve({
            "/2/summary": {"hashrate": {"total": [1210.5, 1198.2, 1201.0]}, "results": {"shares_good": 99, "shares_total": 100},
                           "connection": {"pool": "pool.example.com:10128", "tls": "TLSv1.3"}, "algo": "rx/0",
                           "version": "6.24.0", "worker_id": "rig01"},
            "/2/backends": [{"type": "cpu", "enabled": True, "threads": [{}, {}, {}]}, {"type": "opencl", "enabled": False}],
        }, token="secret")
        found = reporter.read_xmrig(url, "secret")
        self.assertEqual((found["hashrate"], found["hashrate_15m"], found["mining_threads"]), (1198.2, 1201.0, 3))
        self.assertEqual((found["shares_good"], found["shares_total"], found["pool"]), (99, 100, "pool.example.com:10128"))
        self.assertEqual((found["pool_tls"], found["rig_id"]), ("TLSv1.3", "rig01"))
        self.assertEqual(reporter.read_xmrig(url, "wrong"), {})

    def test_just_started_miner_and_no_miner(self):
        url = self.serve({"/2/summary": {"hashrate": {"total": [None, None, None]},
                                         "connection": {"pool": "pool.example.com:10004", "tls": None}}})
        found = reporter.read_xmrig(url)
        self.assertIsNone(found["hashrate"])
        self.assertEqual(found["pool_tls"], "none")
        self.assertEqual(reporter.read_xmrig("http://127.0.0.1:9"), {})
        self.assertEqual(reporter.read_xmrig("off"), {})

    def test_hub_keeps_best_15_minute_rate(self):
        fleet = hub.Fleet(clock=lambda: 1000.0)
        for rate in (1200.0, 1250.0, 1100.0):
            fleet.record(hub.clean_report(report(hashrate=rate, hashrate_15m=rate)))
        pi = fleet.snapshot()["pis"][0]
        self.assertEqual((pi["hashrate"], pi["hashrate_max"], pi["averages"]["windows"]["5m"]["hashrate"]),
                         (1100.0, 1250.0, 1183.33))


class InstallerTests(unittest.TestCase):
    def test_hub_installer_copies_every_module_the_hub_imports(self):
        script = (ROOT / "install" / "install-hub.sh").read_text()
        self.assertIn('"$SOURCE"/hub/*.py', script)
        installed = Path(tempfile.mkdtemp())
        for module in (ROOT / "hub").glob("*.py"):
            (installed / module.name).write_bytes(module.read_bytes())
        # Started the way the service starts it, from the installed folder alone.
        result = subprocess.run([sys.executable, "-I", str(installed / "mindful_fleet_hub.py"), "--version"],
                                cwd="/", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class XmrigSetupTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("xmrig_api", ROOT / "install" / "xmrig-api.py")
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "config.json").write_text("{}")

    def miner(self, *args):
        return {"args": [str(self.folder / "xmrig"), *args], "cwd": "/", "owner": 0, "exe": "/x"}

    def test_finds_config_next_to_program(self):
        self.assertEqual(self.api.find_config(self.miner()), str(self.folder / "config.json"))

    def test_named_config_wins(self):
        self.assertEqual(self.api.find_config(self.miner("--config=/etc/x.json")), "/etc/x.json")
        self.assertEqual(self.api.find_config(self.miner("-c", "/etc/y.json")), "/etc/y.json")

    def test_command_line_setup_has_no_config(self):
        self.assertIsNone(self.api.find_config(self.miner("-o", "pool:1", "-u", "wallet")))


class AverageTests(unittest.TestCase):
    def test_windows(self):
        history = averages.History()
        start = 1_000_000 * 60
        for minute in range(120):  # Two hours: temp 40 for the first, 60 for the second.
            history.add(start + minute * 60, report(temp_c=40.0 if minute < 60 else 60.0, cpu_per_core=[100.0, 0.0]))
        result = history.averages(start + 119 * 60)
        windows = result["windows"]
        self.assertEqual(windows["5m"]["temp"], 60.0)
        self.assertEqual(windows["1h"]["temp"], 60.0)
        self.assertEqual(windows["24h"]["temp"], 50.0)
        self.assertEqual((windows["5m"]["cpu"], windows["5m"]["core0"], windows["5m"]["core1"]), (50.0, 100.0, 0.0))
        self.assertEqual(result["minutes"], 120)

    def test_survives_save_and_old_buckets_fall_off(self):
        history = averages.History()
        history.add(0, report(temp_c=90.0))
        history.add(2 * 86400, report(temp_c=30.0))
        copy = averages.History(json.loads(json.dumps(history.to_saved())))
        self.assertEqual(copy.averages(2 * 86400)["windows"]["24h"]["temp"], 30.0)

    def test_time_slowed_down(self):
        history = averages.History()
        for minute, bits in enumerate((0x0, 0x4, 0x50000, 0x8)):  # Only "now" bits count.
            history.add(minute * 60, report(throttled=bits))
        self.assertEqual(history.averages(180)["windows"]["5m"]["slowed"], 50.0)

    def test_series_for_charts(self):
        history = averages.History()
        start = 1_000_000 * 300
        for minute in range(30):
            history.add(start + minute * 60, report(temp_c=40.0 + minute // 5, sensors=[{"watts": 2.0}]))
        series = history.series(start + 29 * 60, ("temp", "fan_missing", "sensor_watts"))
        self.assertEqual(len(series["times"]), 6)
        self.assertEqual(series["values"]["temp"], [40.0, 41.0, 42.0, 43.0, 44.0, 45.0])
        self.assertEqual(series["values"]["fan_missing"], [None] * 6)
        self.assertEqual(series["values"]["sensor_watts"], [2.0] * 6)

    def test_missing_readings_are_skipped(self):
        history = averages.History()
        history.add(0, report(fan_rpm=None))
        self.assertNotIn("fan", history.averages(0)["windows"]["5m"])


class OrderTests(unittest.TestCase):
    def test_spelled_and_written_numbers_sort_in_order(self):
        names = ["BrainTwo", "BrainFive", "BrainOne", "pi10", "pi2"]
        self.assertEqual(sorted(names, key=hub._natural_key), ["BrainOne", "BrainTwo", "BrainFive", "pi2", "pi10"])



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
