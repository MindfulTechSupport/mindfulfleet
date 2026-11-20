"""History: each Pi's readings kept on the hub for up to 3 years (MindfulFleet Plus).

Each Pi has its own switch and interval. Every interval the hub saves one snapshot per Pi: the average,
low and high of each reading over that interval. Full detail is kept for 90 days, then each 3 hours is
folded into one snapshot that is kept for 3 years.

The database is one SQLite file next to the hub's state file. Python has SQLite built in, so there is
nothing to install. To spare SD cards, snapshots are written once a minute in one batch, and the hub
stops saving new detail when its disk gets nearly full.
"""
import math
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from averages import readings as report_readings

# Every reading History keeps, in the order exports list them.
READINGS = ("temp", "hashrate", "per_watt", "watts", "sensor_watts", "fan", "cpu", "clock", "load", "memory",
            "volts", "slowed", "wifi", "net_in", "net_out", "disk_read", "disk_write")
EXPORT_NAMES = {
    "temp": "Temperature (°C)", "hashrate": "Hashrate (H/s)", "per_watt": "Hashes per watt", "watts": "Board power (W)",
    "sensor_watts": "Sensor power (W)", "fan": "Fan (RPM)", "cpu": "CPU (%)", "clock": "Clock (MHz)", "load": "Load",
    "memory": "Memory (%)", "volts": "Input volts (V)", "slowed": "Slowed down (% of time)", "wifi": "Wi-Fi (dBm)",
    "net_in": "Network in (bytes/s)", "net_out": "Network out (bytes/s)", "disk_read": "Disk read (bytes/s)",
    "disk_write": "Disk write (bytes/s)",
}

INTERVALS = (15, 30, 60, 180, 300, 900, 1800, 3600, 6 * 3600, 86400)  # The choices on each card; Custom adds any other.
MIN_INTERVAL = 15  # How often a Pi reports by default. A shorter snapshot would just repeat one reading.
MAX_INTERVAL = 7 * 86400
DEFAULT_INTERVAL = 60
FULL_DAYS = 90  # Full detail (Ed, D-085).
ROLLUP_SECONDS = 3 * 3600  # Then one snapshot per 3 hours...
ROLLUP_DAYS = 3 * 365 + 1  # ...for 3 years.
FLUSH_SECONDS = 60
MAINTAIN_SECONDS = 3600
LATE_SECONDS = 20  # A report can arrive a little after its interval ends; wait this long before closing it.
MIN_FREE_BYTES = 1024**3  # Stop saving new detail when the hub's disk has less than 1 GB free.
MAX_MARKERS = 1000
MARKER_TEXT = 80
MAX_POINTS = 2000
MAX_EXPORT_ROWS = 1_048_575  # One Excel sheet's rows, less the heading.

FULL, ROLLED = 0, 1  # The two tiers.


def utc_offset(when):
    """Seconds the hub's local time is ahead of UTC at that moment, so a day starts at local midnight."""
    return time.localtime(when).tm_gmtoff or 0


def bucket_start(when, span):
    when = int(when)
    return when - (when + utc_offset(when)) % span


def clean_interval(value):
    """An interval in whole seconds, or ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Interval must be a number of seconds.")
    seconds = int(value)
    if seconds < MIN_INTERVAL:
        raise ValueError(f"The shortest interval is {MIN_INTERVAL} seconds, how often a Pi reports.")
    if seconds > MAX_INTERVAL:
        raise ValueError("The longest interval is 7 days.")
    return seconds


def snapshot_values(report):
    """The readings to keep from one report, by History's names."""
    values = report_readings(report)
    for key in [k for k in values if k.startswith("core")]:
        del values[key]
    watts = values.get("watts")  # Board watts, as the card's Per watt dial uses.
    if values.get("hashrate") is not None and watts:
        values["per_watt"] = values["hashrate"] / watts
    return {name: values[name] for name in READINGS if values.get(name) is not None}


class _Bucket:
    __slots__ = ("start", "span", "n", "sums", "counts", "lows", "highs")

    def __init__(self, start, span):
        self.start, self.span, self.n = start, span, 0
        self.sums, self.counts, self.lows, self.highs = {}, {}, {}, {}

    def add(self, values):
        self.n += 1
        for name, value in values.items():
            self.sums[name] = self.sums.get(name, 0.0) + value
            self.counts[name] = self.counts.get(name, 0) + 1
            self.lows[name] = min(self.lows.get(name, value), value)
            self.highs[name] = max(self.highs.get(name, value), value)

    def row(self):
        """(start, span, n, then avg, low, high per reading). Low and high stay empty when only one report came in."""
        out = [self.start, self.span, self.n]
        for name in READINGS:
            count = self.counts.get(name)
            if not count:
                out += [None, None, None]
            elif count == 1:
                out += [round(self.sums[name], 3), None, None]
            else:
                out += [round(self.sums[name] / count, 3), round(self.lows[name], 3), round(self.highs[name], 3)]
        return out


def _columns():
    names = []
    for name in READINGS:
        names += [name, f"{name}_lo", f"{name}_hi"]
    return names


COLUMNS = _columns()

# Every SQL statement is built once, here, from the fixed names above. Nothing a browser or a Pi sends
# becomes part of a statement: their values always travel as ? parameters.
_ROW = ", ".join(COLUMNS)
_AGG = ", ".join(
    f"SUM({name} * n) / SUM(CASE WHEN {name} IS NULL THEN NULL ELSE n END), "
    f"MIN(COALESCE({name}_lo, {name})), MAX(COALESCE({name}_hi, {name}))" for name in READINGS)
_AVG = ", ".join(f"SUM({name} * n) / SUM(CASE WHEN {name} IS NULL THEN NULL ELSE n END)" for name in READINGS)
_WIDE = ", ".join(f"{name}, COALESCE({name}_lo, {name}), COALESCE({name}_hi, {name})" for name in READINGS)
_MARKS = ", ".join("?" * (5 + len(COLUMNS)))
SQL_CREATE = (
    "CREATE TABLE IF NOT EXISTS pis (n INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, "
    "enabled INTEGER NOT NULL DEFAULT 0, every INTEGER NOT NULL DEFAULT " + str(DEFAULT_INTERVAL) + "); "
    "CREATE TABLE IF NOT EXISTS snaps (pi INTEGER NOT NULL, tier INTEGER NOT NULL, t INTEGER NOT NULL, "
    "span INTEGER NOT NULL, n INTEGER NOT NULL, " + ", ".join(c + " REAL" for c in COLUMNS) + ", "
    "PRIMARY KEY (pi, tier, t)) WITHOUT ROWID; "
    "CREATE TABLE IF NOT EXISTS markers (id INTEGER PRIMARY KEY, pi INTEGER, t INTEGER NOT NULL, text TEXT NOT NULL);")
SQL_SAVE = "INSERT OR REPLACE INTO snaps VALUES (" + _MARKS + ")"
SQL_SAVE_IF_EMPTY = "INSERT OR IGNORE INTO snaps VALUES (" + _MARKS + ")"
SQL_OLD = "SELECT pi, t, span, n, " + _ROW + " FROM snaps WHERE tier = ? AND t < ? ORDER BY pi, t"
SQL_ROLLED = "SELECT pi, t, span, n, " + _ROW + " FROM snaps WHERE pi = ? AND tier = ? AND t = ?"
SQL_SERIES = ("SELECT (t - ?) / ? AS b, " + _AGG + " FROM snaps WHERE pi = ? AND t >= ? AND t < ? "
              "GROUP BY b ORDER BY b")
SQL_COMPARE = "SELECT SUM(span), " + _AVG + " FROM snaps WHERE pi = ? AND t >= ? AND t < ?"
SQL_PRESENT = ("SELECT " + ", ".join(f"MAX({name} IS NOT NULL)" for name in READINGS) +
               ", COUNT(*) FROM snaps WHERE pi = ? AND t >= ? AND t < ?")
SQL_GROUPS = "SELECT COUNT(DISTINCT (t - ?) / ?) FROM snaps WHERE pi = ? AND t >= ? AND t < ?"
SQL_EXPORT = "SELECT t, span, " + _WIDE + " FROM snaps WHERE pi = ? AND t >= ? AND t < ? ORDER BY t"
SQL_EXPORT_EVERY = ("SELECT ((t - ?) / ?) * ? + ?, SUM(span), " + _AGG + " FROM snaps "
                    "WHERE pi = ? AND t >= ? AND t < ? GROUP BY (t - ?) / ? ORDER BY 1")


class HistoryStore:
    """Thread-safe. path=None keeps everything in memory (demo mode and tests)."""

    def __init__(self, path=None, clock=time.time, min_free_bytes=MIN_FREE_BYTES):
        self._path = Path(path) if path else None
        self._clock = clock
        self._min_free = min_free_bytes
        self._lock = threading.Lock()
        self._open = {}  # Pi number -> the _Bucket being filled.
        self._pending = []  # Closed rows waiting for the next batch write.
        self._settings = {}  # Pi id -> {"n", "name", "enabled", "every"}
        self._last_maintain = 0.0
        self.paused = None  # Plain-English reason new detail isn't being saved, or None.
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._address, self._uri = str(self._path), False
        else:
            # A named in-memory database, so an export can read it on its own connection.
            self._address, self._uri = f"file:mindfulfleet-history-{uuid.uuid4().hex}?mode=memory&cache=shared", True
        self._db = self._connect()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SQL_CREATE)
        for n, pi_id, name, enabled, every in self._db.execute("SELECT n, id, name, enabled, every FROM pis"):
            self._settings[pi_id] = {"n": n, "name": name, "enabled": bool(enabled), "every": every}

    def _connect(self):
        return sqlite3.connect(self._address, uri=self._uri, check_same_thread=False, isolation_level=None)

    # Settings

    def _pi_number(self, pi_id, name):
        """Call with the lock held."""
        known = self._settings.get(pi_id)
        if known:
            if name and name != known["name"]:
                known["name"] = name
                self._db.execute("UPDATE pis SET name = ? WHERE n = ?", (name, known["n"]))
            return known["n"]
        cursor = self._db.execute("INSERT INTO pis (id, name) VALUES (?, ?)", (pi_id, name or pi_id))
        self._settings[pi_id] = {"n": cursor.lastrowid, "name": name or pi_id, "enabled": False, "every": DEFAULT_INTERVAL}
        return cursor.lastrowid

    def settings(self):
        with self._lock:
            return {pi_id: {"enabled": s["enabled"], "every": s["every"]} for pi_id, s in self._settings.items()}

    def set(self, pi_id, name, enabled=None, every=None):
        """Turn one Pi's history on or off, or change its interval. Returns its new settings."""
        if every is not None:
            every = clean_interval(every)
        with self._lock:
            n = self._pi_number(pi_id, name)
            current = self._settings[pi_id]
            if every is not None and every != current["every"]:
                self._close(n)  # The open snapshot keeps the interval it started with.
                current["every"] = every
            if enabled is not None:
                if not enabled:
                    self._close(n)
                current["enabled"] = bool(enabled)
            self._db.execute("UPDATE pis SET enabled = ?, every = ? WHERE n = ?", (int(current["enabled"]), current["every"], n))
            return {"enabled": current["enabled"], "every": current["every"]}

    # Recording

    def add(self, report, now=None):
        """Called with every cleaned report. Does nothing unless this Pi's history is on."""
        now = self._clock() if now is None else now
        with self._lock:
            setting = self._settings.get(report["id"])
            if not setting or not setting["enabled"]:
                return
            n = self._pi_number(report["id"], report.get("hostname"))
            start = bucket_start(now, setting["every"])
            bucket = self._open.get(n)
            if bucket and bucket.start != start:
                self._close(n)
                bucket = None
            if bucket is None:
                bucket = self._open[n] = _Bucket(start, setting["every"])
            bucket.add(snapshot_values(report))

    def _close(self, n):
        """Call with the lock held."""
        bucket = self._open.pop(n, None)
        if bucket and bucket.n:
            self._pending.append([n, FULL, *bucket.row()])

    def tick(self, now=None):
        """Close finished snapshots and write them in one batch. The hub calls this once a minute."""
        now = self._clock() if now is None else now
        with self._lock:
            for n, bucket in list(self._open.items()):
                if bucket.start + bucket.span + LATE_SECONDS <= now:
                    self._close(n)
            rows, self._pending = self._pending, []
            if rows and self._disk_low():
                rows = []  # Dropped rather than risk filling the SD card.
            if rows:
                self._db.execute("BEGIN")
                self._db.executemany(SQL_SAVE, rows)
                self._db.execute("COMMIT")
            if now - self._last_maintain >= MAINTAIN_SECONDS:
                self._last_maintain = now
                self._maintain(now)

    def _disk_low(self):
        if not self._path:
            return False
        free = shutil.disk_usage(self._path.parent).free
        if free < self._min_free:
            self.paused = (f"History is paused: the hub's disk has only {free // 1024**2} MB free. "
                           "Free up space (sudo mindful-fleet tidy) and it carries on by itself.")
            return True
        self.paused = None
        return False

    def _maintain(self, now):
        """Fold full detail older than 90 days into 3-hour snapshots, and drop anything older than 3 years."""
        cutoff = bucket_start(now - FULL_DAYS * 86400, ROLLUP_SECONDS)
        old = self._db.execute(SQL_OLD, (FULL, cutoff)).fetchall()
        groups = {}
        for row in old:
            groups.setdefault((row[0], bucket_start(row[1], ROLLUP_SECONDS)), []).append(row)
        self._db.execute("BEGIN")
        for (pi, start), rows in groups.items():
            existing = self._db.execute(SQL_ROLLED, (pi, ROLLED, start)).fetchone()
            if existing:
                rows.append(existing)
            self._db.execute(SQL_SAVE, [pi, ROLLED, start, ROLLUP_SECONDS, *_combine(rows)])
        self._db.execute("DELETE FROM snaps WHERE tier = ? AND t < ?", (FULL, cutoff))
        self._db.execute("DELETE FROM snaps WHERE tier = ? AND t < ?", (ROLLED, now - ROLLUP_DAYS * 86400))
        self._db.execute("DELETE FROM markers WHERE t < ?", (now - ROLLUP_DAYS * 86400,))
        self._db.execute("COMMIT")
        if old:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def used_bytes(self):
        if not self._path:
            return 0
        return sum(os.path.getsize(p) for p in (self._path, Path(f"{self._path}-wal")) if p.exists())

    # Reading back

    def _numbers(self, pi_ids):
        with self._lock:
            return {pi_id: self._settings[pi_id]["n"] for pi_id in pi_ids if pi_id in self._settings}

    def series(self, pi_ids, start, end, names, points=600):
        """Charts: per Pi, its readings from start to end as at most `points` averages, with low and high.

        Each answer has "step", the seconds each point covers. Two points further apart than about two steps
        have a gap between them (the Pi was off, or History was), which the chart shows as a break."""
        start, end = int(start), int(end)
        wanted = [(READINGS.index(name), name) for name in names if name in READINGS]
        if not wanted or end <= start:
            return {}
        points = max(10, min(MAX_POINTS, int(points)))
        result = {}
        for pi_id, n in self._numbers(pi_ids).items():
            with self._lock:
                shortest = self._db.execute("SELECT MIN(span) FROM snaps WHERE pi = ? AND t >= ? AND t < ?",
                                            (n, start, end)).fetchone()[0]
                step = max(int(shortest or 0), math.ceil((end - start) / points))
                rows = self._db.execute(SQL_SERIES, (start, step, n, start, end)).fetchall() if shortest else []
            out = {"times": [start + row[0] * step for row in rows], "step": step if rows else 0,
                   "values": {}, "low": {}, "high": {}}
            for i, name in wanted:
                out["values"][name] = [_round(row[1 + 3 * i]) for row in rows]
                out["low"][name] = [_round(row[2 + 3 * i]) for row in rows]
                out["high"][name] = [_round(row[3 + 3 * i]) for row in rows]
            result[pi_id] = out
        return result

    def compare(self, pi_id, when, days):
        """Before and After: each reading's average for `days` before `when` and `days` after it."""
        n = self._numbers([pi_id]).get(pi_id)
        if n is None:
            return None
        span, when = int(days * 86400), int(when)
        answer = {"days": days}
        with self._lock:
            for label, low, high in (("before", when - span, when), ("after", when, when + span)):
                row = self._db.execute(SQL_COMPARE, (n, low, high)).fetchone()
                answer[label] = {name: _round(value) for name, value in zip(READINGS, row[1:], strict=True) if value is not None}
                answer[f"{label}_hours"] = round((row[0] or 0) / 3600, 1)
        return answer

    def export_plan(self, pi_ids, start, end, every=0, lows=False):
        """What an export holds: (heading, kept reading indexes, [(sheet name, Pi number)]).

        Columns no chosen Pi has a reading for are left out. Raises ValueError when it's more rows
        than one spreadsheet holds."""
        start, end, every = int(start), int(end), int(every)
        numbers = self._numbers(pi_ids)
        with self._lock:
            names = {pi_id: self._settings[pi_id]["name"] for pi_id in numbers}
            has = [False] * len(READINGS)
            sheets = []
            total = 0
            for pi_id, n in numbers.items():
                row = self._db.execute(SQL_PRESENT, (n, start, end)).fetchone()
                count = self._db.execute(SQL_GROUPS, (start, every, n, start, end)).fetchone()[0] if every else row[-1]
                if not count:
                    continue
                has = [a or bool(b) for a, b in zip(has, row[:-1], strict=True)]
                total += count
                sheets.append((names[pi_id], n))
        if total > MAX_EXPORT_ROWS:
            raise ValueError(f"That's {total:,} rows, more than one spreadsheet holds ({MAX_EXPORT_ROWS:,}). "
                             "Pick a shorter time, or hourly or daily averages.")
        kept = [i for i, present in enumerate(has) if present]
        heading = ["Time", "Seconds covered"]
        for i in kept:
            heading.append(EXPORT_NAMES[READINGS[i]])
            if lows:
                heading += [f"{EXPORT_NAMES[READINGS[i]]} low", f"{EXPORT_NAMES[READINGS[i]]} high"]
        return heading, kept, sheets

    def export_rows(self, n, start, end, kept, every=0, lows=False):
        """One Pi's rows for export_plan's heading, oldest first: (time in seconds, seconds covered, values...).

        Reads on its own connection, so a long export doesn't hold up reports coming in."""
        start, end, every = int(start), int(end), int(every)
        db = self._connect()
        try:
            if every:
                cursor = db.execute(SQL_EXPORT_EVERY, (start, every, every, start, n, start, end, start, every))
            else:
                cursor = db.execute(SQL_EXPORT, (n, start, end))
            for row in cursor:
                out = [row[0], row[1]]
                for i in kept:
                    out.append(_round(row[2 + 3 * i]))
                    if lows:
                        out += [_round(row[3 + 3 * i]), _round(row[4 + 3 * i])]
                yield out
        finally:
            db.close()

    # Markers ("Ice cooler on Three")

    def markers(self, start, end):
        with self._lock:
            ids = {s["n"]: pi_id for pi_id, s in self._settings.items()}
            rows = self._db.execute("SELECT id, pi, t, text FROM markers WHERE t >= ? AND t < ? ORDER BY t", (start, end)).fetchall()
        return [{"id": mid, "pi": ids.get(pi), "t": t, "text": text} for mid, pi, t, text in rows]

    def add_marker(self, pi_id, name, when, text):
        text = " ".join(str(text or "").split())[:MARKER_TEXT]
        if not text:
            raise ValueError("Write a few words for the marker, such as \"Ice cooler installed\".")
        if isinstance(when, bool) or not isinstance(when, (int, float)) or not math.isfinite(when):
            raise ValueError("The marker needs a time.")
        with self._lock:
            if self._db.execute("SELECT COUNT(*) FROM markers").fetchone()[0] >= MAX_MARKERS:
                raise ValueError(f"That's {MAX_MARKERS} markers already. Delete an old one first.")
            n = self._pi_number(pi_id, name) if pi_id else None
            cursor = self._db.execute("INSERT INTO markers (pi, t, text) VALUES (?, ?, ?)", (n, int(when), text))
            return {"id": cursor.lastrowid, "pi": pi_id, "t": int(when), "text": text}

    def delete_marker(self, marker_id):
        with self._lock:
            return self._db.execute("DELETE FROM markers WHERE id = ?", (int(marker_id),)).rowcount > 0

    # Loading a saved 24 hours (the file the dashboard's console line saved before History was on)

    def import_saved(self, saved, known):
        """saved: the downloaded file. known: {hostname: pi id} for Pis on this dashboard.
        Adds 5-minute snapshots only where History has none. Returns {hostname: snapshots added}."""
        charts = saved.get("charts") if isinstance(saved, dict) else None
        if not isinstance(charts, dict):
            raise ValueError("That isn't a saved MindfulFleet file.")
        added = {}
        with self._lock:
            self._db.execute("BEGIN")
            for hostname, chart in list(charts.items())[:256]:
                pi_id = known.get(hostname)
                if not pi_id or not isinstance(chart, dict):
                    continue
                times, values = chart.get("times"), chart.get("values")
                if not isinstance(times, list) or not isinstance(values, dict):
                    continue
                n = self._pi_number(pi_id, hostname)
                count = 0
                for i, when in enumerate(times[:2000]):
                    if isinstance(when, bool) or not isinstance(when, (int, float)) or not math.isfinite(when):
                        continue
                    row = [n, FULL, int(when), 300, 20]  # 5-minute averages of about 20 reports each.
                    found = False
                    for name in READINGS:
                        series = values.get(name)
                        value = series[i] if isinstance(series, list) and i < len(series) else None
                        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                        row += [float(value) if ok else None, None, None]
                        found = found or ok
                    if found:
                        count += self._db.execute(SQL_SAVE_IF_EMPTY, row).rowcount
                added[hostname] = count
            self._db.execute("COMMIT")
        return added

    def flush_now(self):
        """Write everything, including snapshots still filling. Used when the hub stops."""
        with self._lock:
            for n in list(self._open):
                self._close(n)
        self.tick()


def _round(value):
    return None if value is None else round(value, 3)


def _combine(rows):
    """Fold several snapshots (pi, t, span, n, columns...) into one: n, then n-weighted averages, lowest lows, highest highs."""
    out = [sum(row[3] for row in rows)]
    for i in range(len(READINGS)):
        avg_i = 4 + 3 * i
        have = [row for row in rows if row[avg_i] is not None]
        if not have:
            out += [None, None, None]
            continue
        weight = sum(row[3] for row in have)
        lows = [row[avg_i + 1] if row[avg_i + 1] is not None else row[avg_i] for row in have]
        highs = [row[avg_i + 2] if row[avg_i + 2] is not None else row[avg_i] for row in have]
        out += [round(sum(row[avg_i] * row[3] for row in have) / weight, 3), round(min(lows), 3), round(max(highs), 3)]
    return out
