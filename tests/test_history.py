"""History (MindfulFleet Plus): storage, the owner switch, the Excel writer and the hub's History API."""
import csv
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "hub")]

import history_store as hs  # noqa: E402
import mindful_fleet_hub as hub  # noqa: E402
import plan as plans  # noqa: E402
import xlsx  # noqa: E402

PI = "abcdef0123456789"
OTHER = "0123456789abcdef"
DAY = 86400
START = 1_800_000_000  # A fixed moment, so tests don't depend on the clock.


def report(pi_id=PI, hostname="Three", **changes):
    base = {"id": pi_id, "hostname": hostname, "temp_c": 50.0, "hashrate": 1000.0, "board_watts": 10.0,
            "throttled": 0, "cpu_per_core": [100.0] * 4, "cpu_mhz": [2400] * 4}
    base.update(changes)
    return base


def row_count(store, tier=None):
    if tier is None:
        return store._db.execute("SELECT COUNT(*) FROM snaps").fetchone()[0]
    return store._db.execute("SELECT COUNT(*) FROM snaps WHERE tier = ?", (tier,)).fetchone()[0]


def balanced(xml_text):
    """True when every tag that opens also closes, in order (a light well-formedness check)."""
    stack = []
    for closing, name, empty in re.findall(r"<(/?)([A-Za-z][\w:.-]*)[^>]*?(/?)>", xml_text):
        if empty:
            continue
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


class IntervalTests(unittest.TestCase):
    def test_intervals_are_whole_seconds_within_limits(self):
        self.assertEqual(hs.clean_interval(60), 60)
        self.assertEqual(hs.clean_interval(90.7), 90)
        for bad in (14, 0, -5, 8 * DAY, True, "60", float("nan"), float("inf"), None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                hs.clean_interval(bad)

    def test_eds_list_plus_an_hour_and_six_hours(self):
        self.assertEqual(hs.INTERVALS, (15, 30, 60, 180, 300, 900, 1800, 3600, 21600, 86400))

    def test_days_start_at_local_midnight(self):
        with mock.patch.object(hs, "utc_offset", return_value=-7 * 3600):
            start = hs.bucket_start(START + 12345, DAY)
        self.assertEqual((start - 7 * 3600) % DAY, 0)
        self.assertLessEqual(start, START + 12345)

    def test_snapshot_readings_include_hashes_per_watt(self):
        values = hs.snapshot_values(report())
        self.assertEqual(values["per_watt"], 100.0)
        self.assertNotIn("core0", values)
        self.assertNotIn("per_watt", hs.snapshot_values(report(board_watts=None)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = hs.HistoryStore(None, clock=lambda: START)

    def fill(self, every, reports, start=START, pi_id=PI, step=15, **changes):
        self.store.set(pi_id, "Three", enabled=True, every=every)
        for i in range(reports):
            self.store.add(report(pi_id, temp_c=50.0 + (i % 4), **changes), start + i * step)

    def test_nothing_is_saved_until_history_is_on(self):
        self.store.add(report(), START)
        self.store.tick(START + 3600)
        self.assertEqual(row_count(self.store), 0)
        self.assertEqual(self.store.settings(), {})

    def test_each_snapshot_keeps_average_low_and_high(self):
        self.fill(60, 8)  # Two minutes of reports every 15 seconds.
        self.store.tick(START + 3600)
        rows = self.store._db.execute("SELECT n, temp, temp_lo, temp_hi FROM snaps ORDER BY t").fetchall()
        self.assertTrue(all(n <= 4 for n, *_ in rows))
        self.assertEqual(sum(n for n, *_ in rows), 8)
        full = [row for row in rows if row[0] == 4]
        self.assertTrue(full)
        self.assertEqual(full[0][1:], (51.5, 50.0, 53.0))

    def test_a_single_reading_leaves_low_and_high_empty(self):
        self.fill(15, 1)
        self.store.tick(START + 3600)
        self.assertEqual(self.store._db.execute("SELECT temp, temp_lo, temp_hi FROM snaps").fetchone(), (50.0, None, None))

    def test_open_snapshot_waits_for_its_interval_to_end(self):
        self.fill(300, 2)
        self.store.tick(START + 60)
        self.assertEqual(row_count(self.store), 0)
        self.store.tick(START + 300 + hs.LATE_SECONDS + 1)
        self.assertEqual(row_count(self.store), 1)

    def test_changing_the_interval_closes_the_open_snapshot(self):
        self.fill(3600, 3)
        self.store.set(PI, "Three", every=60)
        self.store.tick(START + 10)
        self.assertEqual(self.store._db.execute("SELECT span FROM snaps").fetchone(), (3600,))
        self.assertEqual(self.store.settings()[PI], {"enabled": True, "every": 60})

    def test_turning_off_stops_saving_and_keeps_what_was_saved(self):
        self.fill(60, 8)
        self.store.set(PI, "Three", enabled=False)
        self.store.add(report(), START + 500)
        self.store.tick(START + 3600)
        self.assertEqual(sum(n for (n,) in self.store._db.execute("SELECT n FROM snaps")), 8)

    def test_full_detail_for_90_days_then_3_hour_snapshots_for_3_years(self):
        self.store.set(PI, "Three", enabled=True, every=900)
        old = START - 100 * DAY
        for i in range(24):  # 6 hours of 15-minute snapshots, 100 days ago.
            self.store.add(report(temp_c=40.0 + i), old + i * 900)
        self.store.add(report(), START - 4 * 365 * DAY)  # Older than 3 years.
        self.store.add(report(), START - 10)  # Recent.
        self.store.tick(START + 2000)
        self.assertEqual(row_count(self.store, hs.FULL), 1)
        rolled = self.store._db.execute("SELECT span, n, temp, temp_lo, temp_hi FROM snaps WHERE tier = ? ORDER BY t",
                                        (hs.ROLLED,)).fetchall()
        self.assertTrue(2 <= len(rolled) <= 3)  # 6 hours land in two or three 3-hour snapshots.
        self.assertTrue(all(span == hs.ROLLUP_SECONDS for span, *_ in rolled))
        self.assertEqual(sum(n for _, n, *_ in rolled), 24)
        self.assertEqual(min(low for *_, low, _ in rolled), 40.0)
        self.assertEqual(max(high for *_, high in rolled), 63.0)
        weighted = sum(n * avg for _, n, avg, _, _ in rolled) / 24
        self.assertAlmostEqual(weighted, sum(40.0 + i for i in range(24)) / 24, places=2)

    def test_rolling_up_twice_merges_instead_of_losing_data(self):
        self.store.set(PI, "Three", enabled=True, every=900)
        base = hs.bucket_start(START - 100 * DAY, hs.ROLLUP_SECONDS)
        self.store.add(report(temp_c=40.0), base)
        self.store.tick(START)
        self.store.add(report(temp_c=60.0), base + 1800)
        self.store.tick(START + 2 * hs.MAINTAIN_SECONDS)
        self.assertEqual(self.store._db.execute("SELECT n, temp, temp_lo, temp_hi FROM snaps WHERE tier = ?",
                                                (hs.ROLLED,)).fetchall(), [(2, 50.0, 40.0, 60.0)])

    def test_disk_guard_pauses_instead_of_filling_the_card(self):
        with tempfile.TemporaryDirectory() as folder:
            store = hs.HistoryStore(Path(folder) / "history.db", clock=lambda: START, min_free_bytes=1 << 62)
            store.set(PI, "Three", enabled=True, every=15)
            store.add(report(), START)
            store.tick(START + 3600)
            self.assertEqual(row_count(store), 0)
            self.assertIn("paused", store.paused)
            store._db.close()

    def test_settings_and_snapshots_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.db"
            first = hs.HistoryStore(path, clock=lambda: START, min_free_bytes=0)
            first.set(PI, "Three", enabled=True, every=30)
            first.add(report(), START)
            first.flush_now()
            self.assertGreater(first.used_bytes(), 0)
            first._db.close()
            second = hs.HistoryStore(path, clock=lambda: START, min_free_bytes=0)
            self.assertEqual(second.settings(), {PI: {"enabled": True, "every": 30}})
            self.assertEqual(row_count(second), 1)
            second._db.close()

    def test_chart_points_are_averaged_and_report_their_step(self):
        self.fill(15, 240)  # An hour.
        self.store.tick(START + 7200)
        found = self.store.series([PI], START, START + 3600, ["temp", "hashrate", "nonsense"], points=30)[PI]
        self.assertEqual(found["step"], 120)
        self.assertEqual(len(found["times"]), 30)
        self.assertEqual(set(found["values"]), {"temp", "hashrate"})
        self.assertEqual(found["low"]["temp"][0], 50.0)
        self.assertEqual(found["high"]["temp"][0], 53.0)
        self.assertEqual(self.store.series([PI], START, START, ["temp"]), {})
        self.assertEqual(self.store.series(["ffffffffffffffff"], START, START + 10, ["temp"]), {})

    def test_gaps_stay_gaps(self):
        self.fill(60, 20)
        self.fill(60, 20, start=START + 3 * 3600)
        self.store.tick(START + 5 * 3600)
        found = self.store.series([PI], START, START + 4 * 3600, ["temp"], points=600)[PI]
        jumps = [b - a for a, b in zip(found["times"], found["times"][1:], strict=False)]
        self.assertTrue(any(jump > 2.5 * found["step"] for jump in jumps))
        self.assertNotIn(0, [v for v in found["values"]["temp"] if v is not None])

    def test_before_and_after(self):
        self.store.set(PI, "Three", enabled=True, every=3600)
        for hour in range(-48, 48):
            self.store.add(report(temp_c=70.0 if hour < 0 else 61.0), START + hour * 3600)
        self.store.tick(START + 49 * 3600)
        found = self.store.compare(PI, START, 2)
        self.assertEqual((found["before"]["temp"], found["after"]["temp"]), (70.0, 61.0))
        self.assertEqual((found["before_hours"], found["after_hours"]), (48.0, 48.0))
        self.assertIsNone(self.store.compare("ffffffffffffffff", START, 2))

    def test_markers(self):
        marker = self.store.add_marker(PI, "Three", START, "  Ice   cooler installed  ")
        self.assertEqual(marker["text"], "Ice cooler installed")
        self.store.add_marker(None, None, START + 5, "Whole fleet " + "x" * 200)
        found = self.store.markers(START - 1, START + 10)
        self.assertEqual([m["pi"] for m in found], [PI, None])
        self.assertEqual(len(found[1]["text"]), hs.MARKER_TEXT)
        for bad_text, bad_time in (("", START), ("ok", float("nan")), ("ok", True), ("ok", "noon")):
            with self.subTest(text=bad_text, when=bad_time), self.assertRaises(ValueError):
                self.store.add_marker(PI, "Three", bad_time, bad_text)
        self.assertTrue(self.store.delete_marker(marker["id"]))
        self.assertFalse(self.store.delete_marker(marker["id"]))
        with mock.patch.object(hs, "MAX_MARKERS", 1), self.assertRaises(ValueError):
            self.store.add_marker(PI, "Three", START, "One too many")

    def test_export_rows_and_columns(self):
        self.fill(60, 40)
        self.store.tick(START + 7200)
        heading, kept, sheets = self.store.export_plan([PI], START, START + 3600, 0, lows=True)
        self.assertEqual(heading[:5], ["Time", "Seconds covered", "Temperature (°C)", "Temperature (°C) low", "Temperature (°C) high"])
        self.assertNotIn("Fan (RPM)", heading)  # No Pi reported a fan, so no empty column.
        rows = list(self.store.export_rows(sheets[0][1], START, START + 3600, kept, 0, True))
        self.assertEqual(len(rows[0]), len(heading))
        self.assertEqual(rows[0][1], 60)
        hourly = list(self.store.export_rows(sheets[0][1], START, START + 3600, kept, 3600, False))
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0][1], sum(row[1] for row in rows))

    def test_export_refuses_more_than_a_spreadsheet_holds(self):
        self.fill(15, 30)
        self.store.tick(START + 3600)
        with mock.patch.object(hs, "MAX_EXPORT_ROWS", 5), self.assertRaises(ValueError):
            self.store.export_plan([PI], START, START + 3600)
        self.assertEqual(self.store.export_plan([PI], START, START + 3600, every=3600)[2], [("Three", 1)])

    def test_hostile_names_are_only_ever_data(self):
        name = "x'); DROP TABLE snaps; --"
        self.store.set(OTHER, name, enabled=True, every=15)
        self.store.add(report(OTHER, hostname=name), START)
        self.store.tick(START + 3600)
        self.assertEqual(self.store.export_plan([OTHER], START, START + 60)[2][0][0], name)
        self.assertEqual(row_count(self.store), 1)

    def test_loading_a_saved_24_hours(self):
        saved = {"saved": "2026-11-20T20:00:00Z", "charts": {
            "Three": {"times": [START, START + 300, "bad", START + 600],
                      "values": {"temp": [70.0, None, 1, True], "hashrate": [900, 910, 1, None], "evil": ["x"]}},
            "Stranger": {"times": [START], "values": {"temp": [1]}},
        }}
        added = self.store.import_saved(saved, {"Three": PI})
        self.assertEqual(added, {"Three": 2})
        self.assertEqual(self.store.import_saved(saved, {"Three": PI}), {"Three": 0})  # Never twice.
        self.assertEqual(self.store.compare(PI, START + 1, 1)["before"]["temp"], 70.0)
        for bad in ({}, {"charts": []}, [1], "text"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.import_saved(bad, {"Three": PI})


class PlanTests(unittest.TestCase):
    def test_missing_unreadable_or_odd_files_mean_free(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan"
            self.assertEqual(plans.Plan(path).current(), {"name": "free", "plus": False, "pro": False})
            for word, expected in (("owner\n", "owner"), ("  PLUS ", "plus"), ("pro", "pro"), ("gold", "free"), ("", "free")):
                with self.subTest(word=word):
                    path.write_text(word)
                    self.assertEqual(plans.Plan(path).name(), expected)

    def test_owner_turns_on_plus_and_pro(self):
        self.assertEqual(plans.Plan(fixed="owner").current(), {"name": "owner", "plus": True, "pro": True})
        self.assertEqual(plans.Plan(fixed="plus").current()["pro"], False)

    def test_a_changed_file_is_noticed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan"
            path.write_text("free")
            current = plans.Plan(path)
            self.assertFalse(current.current()["plus"])
            time.sleep(0.01)
            path.write_text("owner")
            self.assertTrue(current.current()["plus"])


class XlsxTests(unittest.TestCase):
    def test_column_letters(self):
        self.assertEqual([xlsx.column_letters(i) for i in (0, 25, 26, 51, 52, 701, 702)], ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"])

    def test_sheet_names_follow_excels_rules(self):
        self.assertEqual(xlsx.sheet_names(["Three", "three", "a/b:c*d?e[f]g\\h", "x" * 40, ""]),
                         ["Three", "three (2)", "a-b-c-d-e-f-g-h", "x" * 31, "Pi"])

    def test_workbook_opens_with_one_sheet_per_pi(self):
        out = io.BytesIO()
        rows = [("Three", iter([[START, 60, 51.5, None], [START + 60, 60, 52.0, 3.5]])),
                ("Four & <Five>", iter([[START, 60, 49.0, 1.0]]))]
        xlsx.write(out, ["Time", "Seconds covered", "Temperature (°C)", "Load"], rows, lambda when: 0)
        with zipfile.ZipFile(out) as book:
            names = book.namelist()
            parts = {name: book.read(name).decode() for name in names}
        self.assertIn("xl/worksheets/sheet2.xml", names)
        for name, text in parts.items():
            with self.subTest(part=name):
                self.assertTrue(balanced(text.split("?>", 1)[-1]))
        self.assertIn('name="Three"', parts["xl/workbook.xml"])
        self.assertIn('name="Four &amp; &lt;Five&gt;"', parts["xl/workbook.xml"])
        sheet = parts["xl/worksheets/sheet1.xml"]
        self.assertEqual(sheet.count("<row "), 3)
        second = re.search(r'<row r="2">(.*?)</row>', sheet).group(1)
        self.assertEqual(re.findall(r'<c r="([A-Z]+2)"', second), ["A2", "B2", "C2"])  # The empty Load cell is left out.
        first_time = float(re.search(r"<v>([0-9.]+)</v>", second).group(1))
        self.assertAlmostEqual(first_time, START / DAY + xlsx.EXCEL_EPOCH_DAYS, places=6)
        self.assertIn('<t>Temperature (°C)</t>', sheet)


class HistoryApiTests(unittest.TestCase):
    def start_hub(self, plan_name):
        self.fleet = hub.Fleet()
        self.history = hs.HistoryStore(None)
        handler = hub.make_handler(self.fleet, "secret", history=self.history, plan=plans.Plan(fixed=plan_name))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, body=None, method="GET", headers=None):
        data = None if body is None else json.dumps(body).encode()
        sent = {"Content-Type": "application/json", **(headers or {})} if body is not None else (headers or {})
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                kind = response.headers.get("Content-Type", "")
                return response.status, (json.loads(raw) if kind.startswith("application/json") else raw)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def report_in(self, **changes):
        request = urllib.request.Request(self.base + "/api/report", data=json.dumps(report(**changes)).encode(),
                                         method="POST", headers={"Authorization": "Bearer secret"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)

    def test_free_hub_shows_the_plan_and_refuses_history(self):
        self.start_hub("free")
        self.report_in()
        status, fleet = self.call("/api/fleet")
        self.assertEqual(fleet["plan"], {"name": "free", "plus": False, "pro": False})
        self.assertEqual(fleet["history"]["intervals"], list(hs.INTERVALS))
        status, body = self.call("/api/history/settings", {"ids": [PI], "enabled": True}, "PUT")
        self.assertEqual((status, body["error"]), (403, hub.NEEDS_PLUS))
        now = int(time.time())
        self.assertEqual(self.call(f"/api/history/range?ids={PI}&from={now - 60}&to={now}&readings=temp")[0], 403)
        self.assertEqual(self.call("/api/history/markers", {"pi": PI, "t": now, "text": "x"}, "POST")[0], 403)

    def test_owner_turns_history_on_and_reports_are_saved(self):
        self.start_hub("owner")
        self.report_in()
        status, body = self.call("/api/history/settings", {"ids": [PI], "enabled": True, "every": 15}, "PUT")
        self.assertEqual((status, body["settings"][PI]), (200, {"enabled": True, "every": 15}))
        self.report_in(temp_c=60.0)
        self.history.flush_now()
        now = int(time.time())
        status, body = self.call(f"/api/history/range?ids={PI}&from={now - 600}&to={now + 60}&readings=temp,hashrate")
        self.assertEqual(status, 200)
        self.assertIn(60.0, body["series"][PI]["values"]["temp"])
        status, fleet = self.call("/api/fleet")
        self.assertEqual(fleet["history"]["settings"][PI]["enabled"], True)

    def test_settings_are_checked(self):
        self.start_hub("owner")
        self.report_in()
        for bad in ({"ids": PI, "enabled": True}, {"ids": [], "enabled": True}, {"ids": ["../x"], "enabled": True},
                    {"ids": [PI], "enabled": "yes"}, {"ids": [PI], "every": 5}, {"ids": [PI], "every": "60"}, [PI]):
            with self.subTest(body=bad):
                self.assertEqual(self.call("/api/history/settings", bad, "PUT")[0], 400)
        self.assertEqual(self.call("/api/history/settings", {"ids": [OTHER], "enabled": True}, "PUT")[0], 404)
        cross = self.call("/api/history/settings", {"ids": [PI], "enabled": True}, "PUT", {"Origin": "http://evil.example.com"})
        self.assertEqual(cross[0], 404)
        self.assertEqual(self.history.settings(), {})

    def test_markers_and_before_and_after_over_the_api(self):
        self.start_hub("plus")
        self.report_in()
        now = int(time.time())
        status, marker = self.call("/api/history/markers", {"pi": PI, "t": now, "text": "Ice cooler installed"}, "POST")
        self.assertEqual(status, 200)
        status, body = self.call(f"/api/history/markers?from={now - 10}&to={now + 10}")
        self.assertEqual([m["text"] for m in body["markers"]], ["Ice cooler installed"])
        self.assertEqual(self.call(f"/api/history/compare?id={PI}&t={now}&days=7")[0], 200)
        self.assertEqual(self.call(f"/api/history/compare?id={PI}&t={now}&days=900")[0], 400)
        self.assertEqual(self.call("/api/history/markers", {"pi": OTHER, "t": now, "text": "x"}, "POST")[0], 404)
        self.assertEqual(self.call("/api/history/markers", {"pi": PI, "t": "noon", "text": "x"}, "POST")[0], 400)
        self.assertEqual(self.call("/api/history/markers", {"delete": marker["id"]}, "POST")[1], {"deleted": True})

    def test_export_needs_pro(self):
        self.start_hub("plus")
        now = int(time.time())
        status, body = self.call(f"/api/history/export?ids={PI}&from={now - 60}&to={now}&format=csv")
        self.assertEqual((status, body["error"]), (403, hub.NEEDS_PRO))

    def test_export_csv_and_xlsx(self):
        self.start_hub("owner")
        self.report_in()
        self.call("/api/history/settings", {"ids": [PI], "enabled": True, "every": 15}, "PUT")
        self.report_in(temp_c=61.5)
        self.history.flush_now()
        now = int(time.time())
        status, raw = self.call(f"/api/history/export?ids={PI}&from={now - 600}&to={now + 60}&format=csv&every=0&lows=1")
        self.assertEqual(status, 200)
        lines = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(lines[0][:4], ["Pi", "Time", "Seconds covered", "Temperature (°C)"])
        self.assertEqual((lines[1][0], lines[1][3]), ("Three", "61.5"))
        status, raw = self.call(f"/api/history/export?ids={PI}&from={now - 600}&to={now + 60}&format=xlsx&every=3600")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            self.assertIn("xl/worksheets/sheet1.xml", book.namelist())
        for bad in ("format=pdf", "format=csv&every=60", f"format=csv&ids={PI},../x"):
            with self.subTest(query=bad):
                query = bad if "ids=" in bad else f"ids={PI}&{bad}"
                self.assertEqual(self.call(f"/api/history/export?{query}&from={now - 60}&to={now}")[0], 400)
        empty = self.call(f"/api/history/export?ids={PI}&from=1000&to=2000&format=csv")
        self.assertEqual(empty[0], 404)

    def test_import_a_saved_file(self):
        self.start_hub("owner")
        self.report_in()
        now = int(time.time())
        saved = {"charts": {"Three": {"times": [now - 600], "values": {"temp": [44.0]}}}}
        status, body = self.call("/api/history/import", saved, "POST")
        self.assertEqual((status, body), (200, {"added": {"Three": 1}}))

    def test_no_history_store_means_no_history(self):
        fleet = hub.Fleet()
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(fleet, "secret"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/fleet") as response:
            body = json.load(response)
        self.assertEqual((body["history"], body["plan"]["name"]), (None, "free"))


class StartupTests(unittest.TestCase):
    def test_a_broken_history_file_doesnt_stop_the_dashboard(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "history.db").write_bytes(b"this is not a database" * 100)
            state = str(Path(folder) / "fleet.json")
            started = threading.Event()

            def serve_forever(server):
                started.set()

            with mock.patch.dict("os.environ", {"MINDFUL_FLEET_TOKEN": "secret"}), \
                    mock.patch.object(hub.ThreadingHTTPServer, "serve_forever", serve_forever), \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as errors:
                result = hub.main(["--state", state, "--port", "0", "--no-update-check"])
            self.assertEqual(result, 0)
            self.assertTrue(started.is_set())
            self.assertIn("History is off", errors.getvalue())


class DemoTests(unittest.TestCase):
    def test_demo_week_shows_the_cooler(self):
        fleet = hub.Fleet()
        for name in ("garage-pi", "attic-pi"):
            fleet.record(hub.clean_report(hub.demo_report(name, 50)))
        store = hs.HistoryStore(None)
        hub.seed_demo_history(fleet, store, START)
        attic = {name: pi_id for pi_id, name in fleet.names().items()}["attic-pi"]
        marker = store.markers(START - 7 * DAY, START)[0]
        found = store.compare(attic, marker["t"], 2)
        self.assertLess(found["after"]["temp"], found["before"]["temp"] - 5)


if __name__ == "__main__":
    unittest.main()
