"""Region and units (0.2.16, doc 14 #69): the hub's checks, the upgrade rule (D-090) and the dashboard's conversions."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "hub"), str(ROOT / "reporter")]

import mindful_fleet_hub as hub  # noqa: E402
import mindful_fleet_reporter as reporter  # noqa: E402

REGION_JS = ROOT / "hub" / "dashboard" / "region.js"


def a_report():
    return {"id": "abcdef0123456789", "hostname": "test-pi", "ip": "192.0.2.5", "model": "x", "reporter_version": "0.2.16",
            "temp_c": 50.0, "throttled": 0, "fan_rpm": 2000, "cpu_per_core": [10.0] * 4, "mem_total": 2 ** 33,
            "mem_used": 2 ** 31, "disk_total": 2 ** 36, "disk_used": 2 ** 33, "uptime_s": 100}


class HubRegionTests(unittest.TestCase):
    def test_known_choices_kept(self):
        given = {"country": "DE", "units": "imperial", "currency": "EUR", "dates": "ymd", "clock": "24",
                 "timezone": "Europe/Berlin", "sizes": "decimal", "speed": "bits"}
        self.assertEqual(hub.clean_layout({"region": given})["region"], given)

    def test_anything_else_falls_back(self):
        cleaned = hub.clean_region({"country": "de", "units": "kelvin", "currency": "<b>", "dates": 3, "clock": None,
                                    "timezone": "../../etc/passwd", "sizes": [], "speed": "bauds", "extra": "dropped"})
        self.assertEqual(cleaned, {"country": "", "units": "metric", "currency": "USD", "dates": "auto", "clock": "auto",
                                   "timezone": "auto", "sizes": "binary", "speed": "bytes"})
        self.assertEqual(hub.clean_region({"timezone": "A" * 41})["timezone"], "auto")

    def test_not_set_stays_not_set(self):
        self.assertIsNone(hub.clean_layout({})["region"])
        self.assertIsNone(hub.clean_region("metric"))

    def test_region_js_is_served(self):
        self.assertIn("/region.js", hub.STATIC_FILES)
        self.assertTrue(REGION_JS.exists())

    def test_hub_and_dashboard_offer_the_same_choices(self):
        text = REGION_JS.read_text()
        currencies = re.search(r"const CURRENCIES = \[(.*?)\];", text, re.S).group(1)
        self.assertEqual(tuple(re.findall(r'"([A-Z]{3})"', currencies)), hub.REGION_CURRENCIES)
        for key, options in hub.REGION_CHOICES.items():
            line = re.search(rf"{key}: \[(.*?)\]", text).group(1)
            self.assertEqual(tuple(re.findall(r'"([a-z0-9]+)"', line)), options, key)


class UpgradeKeepsCelsiusTests(unittest.TestCase):
    """D-090: a hub that already has computers keeps °C; a brand-new hub lets the first browser choose."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.state = self.folder / "state.json"

    def test_upgraded_hub_keeps_metric_and_its_layout(self):
        fleet = hub.Fleet(self.state)
        fleet.record(a_report())
        fleet.save_layout({"order": ["abcdef0123456789"], "groups": [], "members": {}, "hidden": ["swap"], "price": 0.15})
        fleet.settle_region()
        saved = json.loads((self.folder / "layout.json").read_text())
        self.assertEqual(saved["region"]["units"], "metric")
        self.assertEqual(saved["region"]["currency"], "USD")
        self.assertEqual(saved["hidden"], ["swap"])
        self.assertEqual(saved["price"], 0.15)

    def test_new_hub_left_for_the_browser(self):
        fleet = hub.Fleet(self.state)
        fleet.settle_region()
        self.assertFalse((self.folder / "layout.json").exists())

    def test_owners_choice_never_overwritten(self):
        fleet = hub.Fleet(self.state)
        fleet.record(a_report())
        chosen = hub.clean_layout({"region": {"units": "imperial", "currency": "CAD"}})
        fleet.save_layout(chosen)
        fleet.settle_region()
        self.assertEqual(fleet.load_layout()["region"]["units"], "imperial")


class VersionTests(unittest.TestCase):
    def test_versions_move_together(self):
        # 0.2.15 left the reporter at 0.2.14, so every card said "Update available" while up to date.
        version = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(hub.VERSION, version)
        self.assertEqual(reporter.VERSION, version)
        self.assertIn(f"## {version} ", (ROOT / "CHANGELOG.md").read_text())


NODE = shutil.which("node")
CHECK_SCRIPT = r"""
globalThis.window = {};
require(process.argv[1]);
const R = window.MF_REGION;
const t = Date.UTC(2026, 10, 21, 14, 30) / 1000;  // Nov 21, 2026, 14:30 world time
const us = R.make({ country: "US", units: "imperial", timezone: "UTC" });
const de = R.make({ country: "DE", currency: "EUR", timezone: "UTC" });
const out = {
  usGuess: R.guess("en-US"), deGuess: R.guess("de-DE"), bare: R.guess("en"),
  f20: us.temp(20), change20: us.tempChange(20), c20: de.temp(20), units: [us.tempUnit, de.tempUnit],
  words: us.words("It's running warm at 72°C."), wordsC: de.words("It's running warm at 72°C."),
  usDate: us.date(t, false), usDateTime: us.date(t, true), deDate: de.date(t, false), deTime: de.time(t),
  ymd: R.make({ dates: "ymd", timezone: "UTC" }).date(t, false),
  clock12: R.make({ country: "DE", clock: "12", timezone: "UTC" }).time(t),
  denver: R.make({ country: "US", timezone: "America/Denver" }).time(t),
  binary: us.size(64 * 1024 ** 3), decimal: R.make({ sizes: "decimal" }).size(64 * 1024 ** 3),
  bits: R.make({ speed: "bits" }).speed(1.5 * 1024 ** 2), bytes: us.speed(1.5 * 1024 ** 2),
  usd: us.money(0.456, 3), eur: de.money(1.5, 2),
  badZone: R.clean({ timezone: "Mars/Olympus_Mons" }).timezone,
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "Node isn't installed here; the GitHub code checker has it")
class DashboardConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run([NODE, "-e", CHECK_SCRIPT, str(REGION_JS)], capture_output=True, text=True, timeout=30,
                              check=True)
        cls.out = json.loads(done.stdout)

    def test_temperatures(self):
        self.assertAlmostEqual(self.out["f20"], 68)
        self.assertAlmostEqual(self.out["change20"], 36)  # A difference: no +32.
        self.assertAlmostEqual(self.out["c20"], 20)
        self.assertEqual(self.out["units"], ["°F", "°C"])
        self.assertEqual(self.out["words"], "It's running warm at 162°F.")
        self.assertEqual(self.out["wordsC"], "It's running warm at 72°C.")

    def test_region_guess(self):
        self.assertEqual((self.out["usGuess"]["units"], self.out["usGuess"]["currency"]), ("imperial", "USD"))
        self.assertEqual((self.out["deGuess"]["units"], self.out["deGuess"]["currency"]), ("metric", "EUR"))
        self.assertEqual((self.out["bare"]["units"], self.out["bare"]["currency"]), ("metric", "USD"))

    def test_dates_and_clock(self):
        self.assertEqual(self.out["usDate"], "Nov 21, 2026")
        self.assertEqual(self.out["usDateTime"], "Nov 21, 2:30 PM")
        self.assertEqual(self.out["deDate"], "21 Nov 2026")
        self.assertEqual(self.out["deTime"], "14:30")
        self.assertEqual(self.out["ymd"], "2026-11-21")
        self.assertEqual(self.out["clock12"], "2:30 PM")
        self.assertEqual(self.out["denver"], "7:30 AM")

    def test_sizes_speeds_and_money(self):
        self.assertEqual(self.out["binary"], "64.0 GB")
        self.assertEqual(self.out["decimal"], "68.7 GB")
        self.assertEqual(self.out["bits"], "12.6 Mbps")
        self.assertEqual(self.out["bytes"], "1.5 MB/s")
        self.assertEqual(self.out["usd"], "$0.456")
        self.assertEqual(self.out["eur"], "€1.50")
        self.assertEqual(self.out["badZone"], "auto")


if __name__ == "__main__":
    unittest.main()


class BatchTests(unittest.TestCase):
    """0.2.16's screen-only batch (D-094): Side by side (#61), Connect (#65), power advice (#46), cooling (#45), cheat sheet (#55)."""

    def setUp(self):
        self.app = (ROOT / "hub" / "dashboard" / "app.js").read_text()
        self.page = (ROOT / "hub" / "dashboard" / "index.html").read_text()

    def test_new_looks_and_rows(self):
        self.assertIn('["side", "Side by side"], ["table", "One row each"]', self.app)
        self.assertIn('["connect", "Connect (SSH)"]', self.app)
        self.assertIn("grid.replaceChildren(compareTable(pis))", self.app)

    def test_power_advice_names_the_official_supplies(self):
        self.assertIn("official 27 W supply (5.1 V, 5 A)", self.app)
        self.assertIn("official 15 W supply (5.1 V, 3 A)", self.app)

    def test_cooling_gap_uses_a_difference_not_a_temperature(self):
        advice = self.app[self.app.index("function coolingAdvice("):self.app.index("function sshBox(")]
        self.assertIn("fmt.tempChange(gap)", advice)
        self.assertNotIn("fmt.temp(gap)", advice)

    def test_cheat_sheet_matches_the_command(self):
        command = (ROOT / "install" / "mindful-fleet").read_text()
        for word in ("status", "update", "hold", "resume", "skip", "every", "xmrig", "password", "tidy", "wifi-watchdog", "plan"):
            self.assertIn(f"  {word})", command, word)
            self.assertIn(f"mindful-fleet {word}" if word in ("status", "update", "xmrig", "password", "tidy") else word, self.page)
