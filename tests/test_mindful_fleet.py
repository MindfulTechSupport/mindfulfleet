import io
import json
import subprocess
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "hub"), str(ROOT / "reporter"), str(ROOT / "tools")]

import health  # noqa: E402
import mindful_fleet_hub as hub  # noqa: E402
import auth  # noqa: E402
import discover  # noqa: E402
import mindful_fleet_reporter as reporter  # noqa: E402
import averages  # noqa: E402
import mindful_fleet_tools as tools  # noqa: E402

GIB = 1024**3


def report(**changes):
    base = {
        "id": "abcdef0123456789", "hostname": "test-pi", "ip": "192.168.1.5", "model": "x",
        "reporter_version": "0.2.11", "temp_c": 50.0, "throttled": 0, "fan_rpm": 2000,
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

    def test_only_web_addresses(self):
        self.assertTrue(reporter.is_web_address("http://192.0.2.10:8742"))
        self.assertTrue(reporter.is_web_address("https://hub.example"))
        for bad in ("file:///etc/passwd", "ftp://x", "192.0.2.10:8742", "", None, "http://"):
            self.assertFalse(reporter.is_web_address(bad), bad)
        self.assertEqual(reporter.read_xmrig("file:///etc/passwd"), {})

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


class PasswordTests(unittest.TestCase):
    def test_hash_checks_only_the_right_password(self):
        record = auth.hash_password("correct horse battery", iterations=1000)
        self.assertNotIn("correct horse", json.dumps(record))
        self.assertTrue(auth.check_password("correct horse battery", record))
        self.assertFalse(auth.check_password("correct horse batterY", record))
        self.assertFalse(auth.check_password(None, record))
        self.assertFalse(auth.check_password("x", {"algorithm": "md5"}))
        self.assertNotEqual(record["salt"], auth.hash_password("correct horse battery", iterations=1000)["salt"])

    def test_password_rules(self):
        self.assertIsNotNone(auth.password_problem("short"))
        self.assertIsNotNone(auth.password_problem(" padded password "))
        self.assertIsNotNone(auth.password_problem("x" * 201))
        self.assertIsNone(auth.password_problem("my pis run cool"))

    def test_guessing_slows_down_and_success_resets(self):
        now = [0.0]
        guard = auth.Guard(clock=lambda: now[0])
        for _ in range(auth.FREE_TRIES - 1):
            self.miss(guard, "10.0.0.9")
        self.assertEqual(guard.wait_seconds("10.0.0.9"), 0)
        self.miss(guard, "10.0.0.9")
        first = guard.wait_seconds("10.0.0.9")
        self.assertGreater(first, 0)
        now[0] += first
        self.miss(guard, "10.0.0.9")
        self.assertGreater(guard.wait_seconds("10.0.0.9"), first)
        self.assertEqual(guard.wait_seconds("10.0.0.10"), 0)
        now[0] += auth.MAX_WAIT_SECONDS
        self.assertEqual(guard.begin("10.0.0.9"), 0)
        guard.finish("10.0.0.9", True)
        self.assertEqual(guard.wait_seconds("10.0.0.9"), 0)

    @staticmethod
    def miss(guard, address):
        guard.begin(address)
        guard.finish(address, False)

    def test_many_addresses_guessing_makes_new_addresses_wait_too(self):
        guard = auth.Guard(clock=lambda: 0.0)
        guard.begin("owner")
        guard.finish("owner", True)
        for n in range(auth.FLEET_WIDE_LIMIT):
            self.miss(guard, f"10.0.{n}.1")
        self.assertGreater(guard.wait_seconds("10.0.1.1"), 0)
        self.assertGreater(guard.wait_seconds("10.9.9.9"), 0)  # A brand-new address doesn't get round it.
        self.assertEqual(guard.wait_seconds("owner"), 0)  # Somewhere that has signed in before still can.

    def test_wait_never_exceeds_five_minutes(self):
        now = [0.0]
        guard = auth.Guard(clock=lambda: now[0])
        for _ in range(40):
            now[0] += 1000
            self.miss(guard, "a")
        self.assertLessEqual(guard.wait_seconds("a"), auth.MAX_WAIT_SECONDS)

    def test_command_sets_password_and_file_is_private(self):
        folder = tempfile.mkdtemp()
        passwords = auth.Auth(Path(folder, "password.json"), iterations=1000)
        answers = iter(["too short", "my pis run cool", "my pis run cooler", "my pis run cool", "my pis run cool"])
        self.assertEqual(hub.set_password_here(passwords, None, ask=lambda _: next(answers)), 0)
        self.assertTrue(passwords.has_password())
        self.assertEqual(os.stat(Path(folder, "password.json")).st_mode & 0o777, 0o600)
        token, _ = passwords.sign_in("my pis run cool", "a")
        self.assertTrue(passwords.session_ok(token))


class PasswordSafetyTests(unittest.TestCase):
    """From the independent review of 0.2.11 (C-012)."""

    def make(self, clock=None):
        folder = tempfile.mkdtemp()
        passwords = auth.Auth(Path(folder, "password.json"), clock=clock or time.time, iterations=1000)
        passwords.set_password("my pis run cool")
        return passwords, Path(folder, "password.json")

    def test_unreadable_password_file_locks_rather_than_opens(self):
        passwords, path = self.make()
        token, _ = passwords.sign_in("my pis run cool", "a")
        for broken in ("", "{not json", json.dumps({"hash": "zz"}), json.dumps([1])):
            with self.subTest(broken=broken):
                path.write_text(broken)
                self.assertTrue(passwords.has_password())
                self.assertTrue(passwords.unreadable())
                self.assertFalse(passwords.session_ok(None))
                self.assertFalse(passwords.session_ok(token))
                self.assertEqual(passwords.sign_in("my pis run cool", "a")[0], None)

    def test_only_a_missing_file_means_no_password(self):
        passwords, path = self.make()
        path.unlink()
        self.assertFalse(passwords.has_password())
        self.assertTrue(passwords.session_ok(None))

    def test_guesses_sent_together_are_checked_one_at_a_time(self):
        passwords, _ = self.make()
        checked = []
        real = auth.check_password

        def slow(password, record):
            checked.append(password)
            time.sleep(0.05)
            return real(password, record)
        auth.check_password = slow
        self.addCleanup(setattr, auth, "check_password", real)
        threads = [threading.Thread(target=passwords.sign_in, args=("wrong guess!!", "attacker")) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(len(checked), auth.FREE_TRIES)

    def test_owner_can_sign_in_while_others_are_braked(self):
        passwords, _ = self.make()
        self.assertIsNotNone(passwords.sign_in("my pis run cool", "10.9.9.9")[0])
        for n in range(auth.FLEET_WIDE_LIMIT):
            passwords.sign_in("wrong guess!!", f"10.0.{n}.1")
        self.assertGreater(passwords.guard.wait_seconds("10.0.0.1"), 0)
        self.assertIsNotNone(passwords.sign_in("my pis run cool", "10.9.9.9")[0])

    def test_wrong_guesses_are_forgotten_after_an_hour(self):
        now = [0.0]
        guard = auth.Guard(clock=lambda: now[0])
        for _ in range(auth.FREE_TRIES + 2):
            guard.begin("a")
            guard.finish("a", False)
        self.assertGreater(guard.wait_seconds("a"), 0)
        now[0] += auth.FORGET_MISSES_AFTER + 1
        self.assertEqual(guard.wait_seconds("a"), 0)

    def test_new_password_from_the_hub_clears_the_waits(self):
        passwords, _ = self.make()
        for _ in range(auth.FREE_TRIES + 1):
            passwords.sign_in("wrong guess!!", "owner")
        self.assertGreater(passwords.guard.wait_seconds("owner"), 0)
        time.sleep(0.01)
        auth.Auth(passwords.path, iterations=1000).set_password("a fresh start here")
        self.assertIsNotNone(passwords.sign_in("a fresh start here", "owner")[0])

    def test_putting_an_old_password_file_back_doesnt_revive_old_sessions(self):
        passwords, path = self.make()
        token, _ = passwords.sign_in("my pis run cool", "a")
        kept = path.with_name("password.json.off")
        path.rename(kept)
        self.assertTrue(passwords.session_ok(None))
        kept.rename(path)
        self.assertFalse(passwords.session_ok(token))

    def test_odd_characters_dont_crash(self):
        passwords, _ = self.make()
        self.assertFalse(auth.check_password("\ud800", auth.hash_password("x" * 10, iterations=1000)))
        self.assertIsNotNone(auth.password_problem("\ud800" * 12))
        self.assertIsNone(passwords.sign_in("\ud800", "a")[0])

    def test_file_is_private_and_fully_written(self):
        _, path = self.make()
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertTrue(auth.valid_record(json.loads(path.read_text())))


class SignInTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.folder = tempfile.mkdtemp()
        self.passwords = auth.Auth(Path(self.folder, "password.json"), clock=lambda: self.now[0], iterations=1000)
        self.passwords.set_password("my pis run cool")
        self.fleet = hub.Fleet(clock=lambda: self.now[0])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          hub.make_handler(self.fleet, "secret", passwords=self.passwords))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, body=None, method=None, cookie=None, headers=None):
        all_headers = {"Content-Type": "application/json", **(headers or {})}
        if cookie:
            all_headers["Cookie"] = f"{auth.COOKIE}={cookie}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response), response.headers
        except urllib.error.HTTPError as error:
            return error.code, json.load(error), error.headers

    def sign_in(self, password="my pis run cool"):
        status, _, headers = self.call("/api/sign-in", {"password": password})
        cookie = headers.get("Set-Cookie", "")
        return status, cookie.split(";", 1)[0].partition("=")[2], cookie

    def test_fleet_data_needs_sign_in(self):
        for path in ("/api/fleet", "/api/layout", "/api/history?id=abcdef0123456789"):
            with self.subTest(path=path):
                self.assertEqual(self.call(path)[0], 401)
        self.assertEqual(self.call("/api/auth")[1], {"password_set": True, "signed_in": False})

    def test_page_files_and_health_stay_open(self):
        for path in ("/", "/app.js", "/style.css", "/healthz"):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)

    def test_right_password_signs_in_with_a_safe_cookie(self):
        status, session, cookie = self.sign_in()
        self.assertEqual(status, 200)
        for part in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertEqual(self.call("/api/fleet", cookie=session)[0], 200)
        self.assertEqual(self.call("/api/auth", cookie=session)[1]["signed_in"], True)

    def test_wrong_password_refused_then_slowed(self):
        for _ in range(auth.FREE_TRIES - 1):
            self.assertEqual(self.sign_in("wrong password!")[0], 401)
        self.assertEqual(self.sign_in("wrong password!")[0], 429)
        self.assertEqual(self.sign_in()[0], 429)  # Even the right one waits its turn.

    def test_sign_out_ends_the_session(self):
        _, session, _ = self.sign_in()
        self.assertEqual(self.call("/api/sign-out", {}, cookie=session)[0], 200)
        self.assertEqual(self.call("/api/fleet", cookie=session)[0], 401)

    def test_session_expires(self):
        _, session, _ = self.sign_in()
        self.now[0] += auth.SESSION_SECONDS + 1
        self.assertEqual(self.call("/api/fleet", cookie=session)[0], 401)

    def test_made_up_cookie_refused(self):
        self.assertEqual(self.call("/api/fleet", cookie="made-up")[0], 401)
        self.assertEqual(self.call("/api/fleet", headers={"Cookie": "\x00;;=bad"})[0], 401)

    def test_change_password_needs_the_current_one_and_signs_others_out(self):
        _, mine, _ = self.sign_in()
        _, other, _ = self.sign_in()
        status, body, _ = self.call("/api/password", {"current": "nope nope nope", "new": "a newer password"}, cookie=mine)
        self.assertEqual(status, 400)
        self.assertIn("current password", body["error"])
        self.assertEqual(self.call("/api/password", {"current": "my pis run cool", "new": "short"}, cookie=mine)[0], 400)
        status, _, headers = self.call("/api/password", {"current": "my pis run cool", "new": "a newer password"},
                                       cookie=mine)
        self.assertEqual(status, 200)
        fresh = headers["Set-Cookie"].split(";", 1)[0].partition("=")[2]
        self.assertEqual(self.call("/api/fleet", cookie=other)[0], 401)
        self.assertEqual(self.call("/api/fleet", cookie=mine)[0], 401)
        self.assertEqual(self.call("/api/fleet", cookie=fresh)[0], 200)
        self.assertEqual(self.sign_in("a newer password")[0], 200)

    def test_change_password_needs_sign_in(self):
        self.assertEqual(self.call("/api/password", {"current": "my pis run cool", "new": "a newer password"})[0], 401)

    def test_new_password_from_the_hub_signs_everyone_out(self):
        _, session, _ = self.sign_in()
        auth.Auth(Path(self.folder, "password.json"), iterations=1000).set_password("set on the hub itself")
        self.assertEqual(self.call("/api/fleet", cookie=session)[0], 401)

    def test_other_sites_refused(self):
        evil = {"Origin": "http://evil.example"}
        self.assertEqual(self.call("/api/sign-in", {"password": "my pis run cool"}, headers=evil)[0], 404)
        _, session, _ = self.sign_in()
        self.assertEqual(self.call("/api/layout", {"order": []}, method="PUT", cookie=session, headers=evil)[0], 404)
        same = {"Origin": self.base}
        self.assertEqual(self.call("/api/layout", {"order": []}, method="PUT", cookie=session, headers=same)[0], 200)

    def test_form_posts_refused(self):
        status, _, _ = self.call("/api/sign-in", {"password": "my pis run cool"},
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 404)

    def test_layout_change_needs_sign_in(self):
        self.assertEqual(self.call("/api/layout", {"order": []}, method="PUT")[0], 401)

    def test_reporters_still_report_with_the_token(self):
        request = urllib.request.Request(self.base + "/api/report", data=json.dumps(report()).encode(), method="POST",
                                         headers={"Authorization": "Bearer secret"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)

    def test_other_apps_cookies_dont_hide_ours(self):
        _, session, _ = self.sign_in()
        for header in (f'other={{"a":1}}; {auth.COOKIE}={session}', f"pihole=a b; {auth.COOKIE}={session}"):
            with self.subTest(header=header):
                self.assertEqual(self.call("/api/fleet", headers={"Cookie": header})[0], 200)

    def test_unreadable_password_file_refuses_everyone(self):
        _, session, _ = self.sign_in()
        Path(self.folder, "password.json").write_text("")
        self.assertEqual(self.call("/api/fleet")[0], 401)
        self.assertEqual(self.call("/api/fleet", cookie=session)[0], 401)
        self.assertEqual(self.call("/api/sign-in", {"password": "my pis run cool"})[0], 503)

    def test_fleet_says_whether_a_password_is_set(self):
        _, session, _ = self.sign_in()
        self.assertTrue(self.call("/api/fleet", cookie=session)[1]["password_set"])

    def test_no_password_yet_keeps_the_dashboard_open(self):
        Path(self.folder, "password.json").unlink()
        self.assertEqual(self.call("/api/fleet")[0], 200)
        self.assertEqual(self.call("/api/auth")[1], {"password_set": False, "signed_in": True})
        self.assertEqual(self.call("/api/sign-in", {"password": "anything at all"})[0], 409)


class WifiWatchdogTests(unittest.TestCase):
    def check(self, state, results, start=10_000.0):
        actions = []
        for n, reachable in enumerate(results):
            action, state = tools.decide(state, reachable, start + n * 120)
            actions.append(action)
        return actions, state

    def test_router_reachable_does_nothing_and_resets(self):
        actions, state = self.check({"misses": 2, "restarts": 1}, [True])
        self.assertEqual(actions, ["none"])
        self.assertEqual((state["misses"], state["restarts"]), (0, 0))

    def test_restarts_wifi_after_three_misses(self):
        actions, _ = self.check({}, [False] * 3)
        self.assertEqual(actions, ["none", "none", "restart"])

    def test_reboots_only_after_three_restarts_and_at_most_hourly(self):
        actions, state = self.check({}, [False] * 12)
        self.assertEqual(actions.count("restart"), 3)
        self.assertEqual(actions[-1], "reboot")
        actions, _ = self.check(state, [False] * 12, start=state["last_reboot"] + 60)
        self.assertNotIn("reboot", actions)

    def test_reads_the_default_route(self):
        folder = tempfile.mkdtemp()
        route = Path(folder, "route")
        route.write_text("Iface\tDestination\tGateway\tFlags\n"
                         "wlan0\t0001A8C0\t00000000\t0001\n"
                         "wlan0\t00000000\t0101A8C0\t0003\n")
        self.assertEqual(tools.default_route(route), ("wlan0", "192.168.1.1"))
        self.assertEqual(tools.default_route(Path(folder, "missing")), (None, None))

    def test_does_nothing_on_a_cable(self):
        folder = tempfile.mkdtemp()
        route = Path(folder, "route")
        route.write_text("Iface\tDestination\tGateway\n" "eth0\t00000000\t0101A8C0\n")
        state_dir = tools.STATE_DIR
        tools.STATE_DIR = Path(folder, "state")
        self.addCleanup(setattr, tools, "STATE_DIR", state_dir)
        self.assertIsNone(tools.wifi_check(route_file=route))
        self.assertFalse(Path(tools.STATE_DIR, "wifi-watchdog.json").exists())  # It didn't even ping.


class WifiWatchdogMoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        state_dir = tools.STATE_DIR
        tools.STATE_DIR = Path(self.folder, "state")
        self.addCleanup(setattr, tools, "STATE_DIR", state_dir)
        self.calls = []
        self.answers = {}
        real = tools.run
        tools.run = lambda args, timeout=60: (self.calls.append(args), self.answers.get(args[0], (False, "")))[1]
        self.addCleanup(setattr, tools, "run", real)
        sleep = tools.time.sleep
        tools.time.sleep = lambda seconds: None
        self.addCleanup(setattr, tools.time, "sleep", sleep)

    def route(self, text):
        path = Path(self.folder, "route")
        path.write_text("Iface\tDestination\tGateway\n" + text)
        return str(path)

    def test_reboots_slow_down_while_the_router_stays_away_and_reset_when_it_returns(self):
        state, now, reboots = {}, 10_000.0, []
        for _ in range(48 * 30):  # Two days of checks every 2 minutes with the router switched off.
            action, state = tools.decide(state, False, now)
            if action == "reboot":
                reboots.append(now)
            now += 120
        gaps = [b - a for a, b in zip(reboots, reboots[1:])]  # noqa: B905
        self.assertTrue(all(later >= earlier for earlier, later in zip(gaps, gaps[1:])))  # noqa: B905
        self.assertLessEqual(len(reboots), 7)
        self.assertGreaterEqual(min(gaps), 2 * tools.REBOOT_GAP)
        _, state = tools.decide(state, True, now)
        self.assertEqual(state["reboot_gap"], tools.REBOOT_GAP)

    def test_odd_saved_notes_dont_crash(self):
        action, state = tools.decide({"misses": "lots", "reboot_gap": None, "last_reboot": [1]}, False, 100.0)
        self.assertEqual((action, state["misses"]), ("none", 1))

    def test_router_that_ignores_pings_still_counts_as_there(self):
        self.answers["ip"] = (True, "192.168.1.1 lladdr aa:bb:cc:dd:ee:ff REACHABLE")
        self.assertTrue(tools.router_answers("192.168.1.1", "wlan0"))
        self.answers["ip"] = (True, "192.168.1.1 lladdr aa:bb:cc:dd:ee:ff STALE")
        self.assertFalse(tools.router_answers("192.168.1.1", "wlan0"))

    def test_lost_connection_with_no_route_counts_as_misses(self):
        net = Path(self.folder, "net")
        Path(net, "wlan0").mkdir(parents=True)
        tools.wifi_check(now=1000.0, route_file=self.route("wlan0\t00000000\t0101A8C0\n"), net_dir=net)
        self.answers["ping"] = (False, "")
        empty = self.route("")
        for n in range(3):
            tools.wifi_check(now=1120.0 + n * 120, route_file=empty, net_dir=net)
        self.assertIn(["nmcli", "radio", "wifi", "on"] if tools.shutil.which("nmcli") else
                      ["ip", "link", "set", "wlan0", "up"], self.calls)

    def test_moving_to_a_cable_starts_afresh(self):
        tools.wifi_check(now=1000.0, route_file=self.route("wlan0\t00000000\t0101A8C0\n"), net_dir=self.folder)
        notes = Path(tools.STATE_DIR, "wifi-watchdog.json")
        self.assertTrue(notes.exists())
        tools.wifi_check(now=1120.0, route_file=self.route("eth0\t00000000\t0101A8C0\n"), net_dir=self.folder)
        self.assertFalse(notes.exists())

    def test_turning_on_refuses_when_the_router_cant_be_told_apart(self):
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(tools.wifi_ready(self.route("wlan0\t00000000\t0101A8C0\n")), 1)
            self.answers["ping"] = (True, "")
            self.assertEqual(tools.wifi_ready(self.route("wlan0\t00000000\t0101A8C0\n")), 0)
            self.assertEqual(tools.wifi_ready(self.route("eth0\t00000000\t0101A8C0\n")), 0)


class TidyTests(unittest.TestCase):
    def test_reads_the_log_size(self):
        self.assertEqual(tools.parse_journal_usage("Archived and active journals take up 456.0M in the file system."),
                         int(456.0 * 1024**2))
        self.assertEqual(tools.parse_journal_usage("take up 1.5G in"), int(1.5 * 1024**3))
        self.assertIsNone(tools.parse_journal_usage("no numbers here"))

    def test_counts_downloaded_update_files(self):
        folder = tempfile.mkdtemp()
        Path(folder, "a.deb").write_bytes(b"x" * 1000)
        Path(folder, "notes.txt").write_bytes(b"x" * 50)
        self.assertEqual(tools.apt_cache_size(folder), 1000)

    def test_saying_no_changes_nothing(self):
        cap = tools.JOURNAL_CAP
        tools.JOURNAL_CAP = Path(tempfile.mkdtemp(), "cap.conf")
        self.addCleanup(setattr, tools, "JOURNAL_CAP", cap)
        calls = []
        real = tools.run
        tools.run = lambda args, timeout=60: (calls.append(args), (False, ""))[1]
        self.addCleanup(setattr, tools, "run", real)
        self.assertIsNone(tools.tidy(yes=False, root=tempfile.gettempdir()))
        self.assertEqual([c for c in calls if c[0] in ("apt-get", "systemctl")], [])
        self.assertFalse(tools.JOURNAL_CAP.exists())

    def test_sizes_read_plainly(self):
        self.assertEqual(tools.human(512), "512 bytes")
        self.assertEqual(tools.human(1536 * 1024), "1.5 MB")


ROUTES = """Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT
wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0
"""
NETWORK_TABLE = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.23     0x1         0x2         d8:3a:dd:12:34:56     *        wlan0
192.168.1.30     0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlan0
192.168.1.41     0x1         0x2         B8:27:EB:65:43:21     *        wlan0
192.168.1.50     0x1         0x0         00:00:00:00:00:00     *        wlan0
192.168.1.60     0x1         0x2         f0:40:af:9a:bc:de     *        wlan0
192.168.1.61     0x1         0x2         f0:40:af:1a:bc:de     *        wlan0
10.9.9.9         0x1         0x2         dc:a6:32:00:00:01     *        eth9
"""


def write(folder, name, text):
    path = Path(folder, name)
    path.write_text(text)
    return str(path)


class FindMyPisTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.routes = write(self.folder, "route", ROUTES)
        self.table = write(self.folder, "arp", NETWORK_TABLE)
        self.knocked = []

    def knock(self, address):
        self.knocked.append(str(address))
        return str(address) == "192.168.1.23"

    def find(self, known=None, routes=None, own="192.168.1.10"):
        return discover.find_pis(known or {}, routes or self.routes, self.table, knocker=self.knock,
                                 find_own=lambda gateway: discover.ipaddress.IPv4Address(own), settle=0)

    def test_raspberry_pi_prefixes(self):
        for mac in ("b8:27:eb:00:00:01", "DC-A6-32-00-00-01", "2ccf67000001", "f0:40:af:9f:ff:ff",
                    "8c:1f:64:34:a0:01"):
            with self.subTest(mac=mac):
                self.assertTrue(discover.is_raspberry_pi(mac))
        for mac in ("aa:bb:cc:dd:ee:ff", "f0:40:af:1f:ff:ff", "8c:1f:64:34:b0:01", "b8:27:eb", ""):
            with self.subTest(mac=mac):
                self.assertFalse(discover.is_raspberry_pi(mac))

    def test_finds_only_pis_on_the_hubs_network(self):
        results = self.find(known={"192.168.1.41": "attic-pi"})
        self.assertEqual(results["network"], "192.168.1.0/24")
        self.assertEqual(results["checked"], 253)  # Every address but the hub's own.
        self.assertNotIn("192.168.1.10", self.knocked)
        self.assertEqual([(p["ip"], p["ssh"], p["reporting"], p["name"]) for p in results["found"]], [
            ("192.168.1.23", True, False, None),
            ("192.168.1.41", False, True, "attic-pi"),
            ("192.168.1.60", False, False, None),
        ])
        self.assertEqual(results["found"][1]["mac"], "b8:27:eb:65:43:21")

    def test_a_slash_23_is_narrowed_too(self):
        wide = ROUTES.replace("00FFFFFF", "00FEFFFF").replace("0001A8C0\t00000000", "0000A8C0\t00000000")
        self.assertEqual(self.find(routes=write(self.folder, "w23", wide))["network"], "192.168.1.0/24")

    def test_big_networks_are_narrowed_to_the_hubs_own_slice(self):
        wide = ROUTES.replace("0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF", "0000A8C0\t00000000\t0001\t0\t0\t600\t0000FFFF")
        results = self.find(routes=write(self.folder, "wide", wide))
        self.assertEqual(results["network"], "192.168.1.0/24")

    def test_refuses_public_networks_and_no_router(self):
        public = ROUTES.replace("0101A8C0", "01000A2D").replace("0001A8C0", "00000A2D")  # 45.10.0.x
        self.assertIn("error", self.find(routes=write(self.folder, "public", public), own="45.10.0.10"))
        self.assertIn("error", self.find(routes=write(self.folder, "empty", ROUTES.splitlines()[0] + "\n")))
        self.assertEqual(self.knocked, [])

    def test_one_look_at_a_time_and_a_short_rest(self):
        now = [1000.0]
        looks = []

        def finder(known):
            looks.append(1)
            return {"network": "192.168.1.0/24", "checked": 1, "when": now[0],
                    "found": [{"ip": "192.168.1.23", "mac": "d8:3a:dd:12:34:56", "ssh": True,
                               "reporting": False, "name": None}]}

        finding = discover.Discovery(finder, clock=lambda: now[0])
        finding.run({})
        again = finding.run({"192.168.1.23": "new-pi"})
        self.assertEqual(len(looks), 1)
        self.assertEqual(again["found"][0]["name"], "new-pi")  # Names stay current between looks.
        now[0] += discover.REST_SECONDS
        finding.run({})
        self.assertEqual(len(looks), 2)
        with finding._busy:
            self.assertIsNone(finding.run({}))


class FindMyPisHubTests(unittest.TestCase):
    call = SignInTests.call
    sign_in = SignInTests.sign_in

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.passwords = auth.Auth(Path(self.folder, "password.json"), iterations=1000)
        self.passwords.set_password("my pis run cool")
        self.fleet = hub.Fleet()
        self.fleet.record(hub.clean_report(report(ip="192.168.1.41", hostname="attic-pi")))
        self.seen = []

        def finder(known):
            self.seen.append(known)
            return discover.demo_results()

        handler = hub.make_handler(self.fleet, "secret", passwords=self.passwords,
                                   discovery=discover.Discovery(finder))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_needs_sign_in_and_same_site(self):
        self.assertEqual(self.call("/api/discover", {})[0], 401)
        _, session, _ = self.sign_in()
        self.assertEqual(self.call("/api/discover", {}, cookie=session,
                                   headers={"Origin": "http://evil.example"})[0], 404)
        self.assertEqual(self.seen, [])

    def test_refused_until_a_password_is_set(self):
        Path(self.folder, "password.json").unlink()
        self.assertEqual(self.call("/api/discover", {})[0], 403)
        self.assertEqual(self.seen, [])

    def test_signed_in_look_lists_pis_and_who_reports(self):
        _, session, _ = self.sign_in()
        status, body, _ = self.call("/api/discover", {}, cookie=session)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["found"]), 2)
        self.assertEqual(self.seen, [{"192.168.1.41": "attic-pi"}])



class ToolsCommandTests(unittest.TestCase):
    def test_watchdog_ignores_odd_interface_names(self):
        folder = tempfile.mkdtemp()
        for name in ("wl;reboot", "wlan0-with-a-very-long-name", "eth0"):
            route = Path(folder, "route")
            route.write_text("Iface\tDestination\tGateway\n" + f"{name}\t00000000\t0101A8C0\n")
            with self.subTest(name=name), mock.patch.object(tools, "run") as run:
                tools.wifi_check(route_file=str(route))
                run.assert_not_called()

    def test_exit_codes(self):
        with mock.patch.object(tools, "tidy_undo") as undo:
            self.assertEqual(tools.main(["tidy-undo"]), 0)
        undo.assert_called_once()
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(tools.main(["nonsense"]), 2)

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
