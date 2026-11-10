"""Averages over several time windows.

To keep memory small on a Pi, the last hour is kept as one-minute buckets and the last
day as five-minute buckets: 348 buckets per computer in all.
"""
from collections import deque

WINDOWS = (("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("24h", 1440))
FINE_MINUTES = 60
COARSE_MINUTES = 5
COARSE_BUCKETS = 1440 // COARSE_MINUTES


def readings(report):
    """The numbers worth averaging, by name, from one report."""
    values = {
        "temp": report.get("temp_c"),
        "fan": report.get("fan_rpm"),
        "volts": report.get("input_volts"),
        "net_in": report.get("net_rx_bps"),
        "net_out": report.get("net_tx_bps"),
        "disk_read": report.get("disk_read_bps"),
        "disk_write": report.get("disk_write_bps"),
        "wifi": report.get("wifi_dbm"),
        "watts": report.get("board_watts"),
    }
    throttled = report.get("throttled")
    if throttled is not None:
        values["slowed"] = 100.0 if throttled & 0b1110 else 0.0  # Percent of the time it was holding back.
    cores = report.get("cpu_per_core") or []
    if cores:
        values["cpu"] = sum(cores) / len(cores)
        for index, load in enumerate(cores):
            values[f"core{index}"] = load
    mhz = report.get("cpu_mhz") or []
    if mhz:
        values["clock"] = sum(mhz) / len(mhz)
    load = report.get("load_avg") or []
    if load:
        values["load"] = load[0]
    if report.get("mem_total") and report.get("mem_used") is not None:
        values["memory"] = 100 * report["mem_used"] / report["mem_total"]
    return {name: value for name, value in values.items() if value is not None}


class _Tier:
    def __init__(self, width, count, saved=None):
        self.width = width
        self.buckets = deque(maxlen=count)
        for bucket in saved or []:
            start, sums = int(bucket[0]), bucket[1]
            self.buckets.append([start, {k: [float(v[0]), int(v[1])] for k, v in sums.items()}])

    def add(self, minute, values):
        start = minute - minute % self.width
        if not self.buckets or self.buckets[-1][0] != start:
            self.buckets.append([start, {}])
        sums = self.buckets[-1][1]
        for name, value in values.items():
            entry = sums.setdefault(name, [0.0, 0])
            entry[0] += value
            entry[1] += 1

    def total(self, since_minute):
        totals = {}
        for start, sums in reversed(self.buckets):
            if start + self.width <= since_minute:
                break
            for name, (value_sum, count) in sums.items():
                entry = totals.setdefault(name, [0.0, 0])
                entry[0] += value_sum
                entry[1] += count
        return totals


class History:
    def __init__(self, saved=None):
        saved = saved or {}
        self.fine = _Tier(1, FINE_MINUTES, saved.get("fine"))
        self.coarse = _Tier(COARSE_MINUTES, COARSE_BUCKETS, saved.get("coarse"))

    def add(self, now, report):
        minute = int(now // 60)
        values = readings(report)
        self.fine.add(minute, values)
        self.coarse.add(minute, values)

    def averages(self, now):
        """Return {window: {reading: average}} plus how many minutes of history exist."""
        minute = int(now // 60)
        result = {}
        for name, length in WINDOWS:
            tier = self.fine if length <= FINE_MINUTES else self.coarse
            totals = tier.total(minute - length + 1)
            result[name] = {key: round(s / c, 2) for key, (s, c) in totals.items() if c}
        oldest = self.coarse.buckets[0][0] if self.coarse.buckets else minute
        return {"windows": result, "minutes": max(0, minute - oldest + 1)}

    def to_saved(self):
        return {"fine": [list(b) for b in self.fine.buckets], "coarse": [list(b) for b in self.coarse.buckets]}
