# SPDX-License-Identifier: MIT
"""Report contract v1: every field a device may send, its kind and its limits.

This is the one published list of what a device reports (nothing else is accepted). The hub's
checker keeps only these fields; tests/test_engine.py holds the hub to this list, and
engine/report/CONTRACT.md is written from it.

Kinds:
  id        lowercase hex, 8 to 64 characters; anything else refuses the whole report
  hostname  letters, digits, dot, dash, underscore; up to 63; anything else refuses the report
  address   an IP address (digits, hex, dots, colons), or missing; anything else refuses the report
  short     a string cut to `length` characters (non-strings become missing)
  text      a string cut to `length` characters, unprintable characters removed
  number    a number from `low` to `high`; out of range or not a number refuses the report
  numbers   a list of up to `count` numbers from `low` to `high`; anything else refuses the report
  count     a whole number from 0 to `high`; anything else is dropped (missing), not refused
  choice    one of `choices`; anything else is dropped
  sensors   up to 8 power sensors: label, chip, volts, amps, watts
  ups       one UPS: name, status (capital letters), charge, runtime_s, load; a bad one is dropped
"""

from collections import namedtuple

CONTRACT_VERSION = 1

Field = namedtuple("Field", "name kind low high length count choices source meaning",
                   defaults=(None, None, None, None, None, "core", ""))

_TB = 1 << 50

FIELDS = (
    Field("id", "id", meaning="The device's own random ID"),
    Field("hostname", "hostname", meaning="The device's network name"),
    Field("ip", "address", meaning="The device's address on its network (a flavor may leave it out)"),
    Field("model", "short", length=80, meaning="Board or computer model"),
    Field("reporter_version", "short", length=20, meaning="Version of the reporting program"),
    Field("temp_c", "number", -50, 200, meaning="Processor temperature, degrees C"),
    Field("throttled", "number", 0, 2**32, meaning="Slow-down and low-voltage flags"),
    Field("fan_rpm", "number", 0, 100000, meaning="Fan speed"),
    Field("fan_percent", "number", 0, 100, meaning="Fan power, percent"),
    Field("input_volts", "number", 0, 60, meaning="Power supply volts"),
    Field("board_watts", "number", 0, 1000, meaning="Board power use"),
    Field("cpu_per_core", "numbers", 0, 100, count=256, meaning="Busy percent per core"),
    Field("mem_total", "number", 0, _TB, meaning="Memory size, bytes"),
    Field("mem_used", "number", 0, _TB, meaning="Memory in use, bytes"),
    Field("disk_total", "number", 0, _TB, meaning="Main disk size, bytes"),
    Field("disk_used", "number", 0, _TB, meaning="Main disk used, bytes"),
    Field("uptime_s", "number", 0, 10**10, meaning="Seconds since start"),
    Field("board_ram_mb", "number", 0, 1 << 22, meaning="Memory fitted on the board, MB"),
    Field("cpu_mhz", "numbers", 0, 100000, count=256, meaning="Speed per core, MHz"),
    Field("cpu_max_mhz", "number", 0, 100000, meaning="Top speed, MHz"),
    Field("load_avg", "numbers", 0, 100000, count=3, meaning="1, 5 and 15 minute load"),
    Field("swap_total", "number", 0, _TB, meaning="Swap size, bytes"),
    Field("swap_used", "number", 0, _TB, meaning="Swap in use, bytes"),
    Field("net_rx_bps", "number", 0, _TB, meaning="Network in, bytes a second"),
    Field("net_tx_bps", "number", 0, _TB, meaning="Network out, bytes a second"),
    Field("core_volts", "number", 0, 10, meaning="Processor core volts"),
    Field("os_name", "text", length=60, meaning="Operating system name"),
    Field("wifi_dbm", "number", -150, 50, meaning="Wi-Fi signal, dBm"),
    Field("disk_read_bps", "number", 0, _TB, meaning="Disk reads, bytes a second"),
    Field("disk_write_bps", "number", 0, _TB, meaning="Disk writes, bytes a second"),
    Field("top_process", "text", length=40, meaning="Busiest program's name"),
    Field("top_process_cpu", "number", 0, 100000, meaning="Busiest program's share, percent"),
    Field("sensors", "sensors", count=8, meaning="Extra power sensors"),
    Field("hashrate", "number", 0, 1e12, source="xmrig", meaning="Mining speed, hashes a second"),
    Field("hashrate_15m", "number", 0, 1e12, source="xmrig", meaning="Mining speed, 15-minute average"),
    Field("mining_threads", "number", 0, 4096, source="xmrig", meaning="Mining threads"),
    Field("shares_good", "number", 0, 1e15, source="xmrig", meaning="Accepted shares"),
    Field("shares_total", "number", 0, 1e15, source="xmrig", meaning="All shares sent"),
    Field("pool", "text", length=80, source="xmrig", meaning="Mining pool address"),
    Field("pool_tls", "text", length=20, source="xmrig", meaning="Pool link encryption, or none"),
    Field("rig_id", "text", length=40, source="xmrig", meaning="Miner's name at the pool"),
    Field("algo", "text", length=30, source="xmrig", meaning="Mining algorithm"),
    Field("miner_version", "text", length=20, source="xmrig", meaning="Miner program version"),
    Field("kernel", "text", length=40, meaning="Kernel version"),
    Field("page_kb", "count", high=1 << 20, meaning="Memory page size, KB"),
    Field("huge_pages", "count", high=1 << 30, meaning="Huge pages set aside"),
    Field("thp", "choice", choices=("always", "madvise", "never"), meaning="Transparent huge pages setting"),
    Field("ups", "ups", source="ups-nut", meaning="UPS status, battery charge, minutes left and load"),
)


def field_names(source=None):
    """Every field name in contract order, or only those from one source ("core" or an add-on name)."""
    return [f.name for f in FIELDS if source is None or f.source == source]


def by_name():
    return {f.name: f for f in FIELDS}


def contract_markdown():
    """The contract as a page people can read; CONTRACT.md is kept equal to this by a test."""
    lines = [
        f"# Report contract v{CONTRACT_VERSION}",
        "",
        "Every field a device may send. The hub keeps only these and checks each one; this file is",
        "written from engine/report/contract.py (a test keeps them equal). A change that breaks this",
        "list is a new major version, announced to every project that uses the engine.",
        "",
        "| Field | Kind | Limits | From | Meaning |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in FIELDS:
        if f.kind in ("number", "numbers"):
            limits = f"{f.low} to {f.high}" + (f", up to {f.count} values" if f.count else "")
        elif f.kind in ("short", "text"):
            limits = f"up to {f.length} characters"
        elif f.kind == "count":
            limits = f"whole number 0 to {f.high}"
        elif f.kind == "choice":
            limits = ", ".join(f.choices)
        elif f.kind == "sensors":
            limits = f"up to {f.count}"
        else:
            limits = "see kinds"
        lines.append(f"| {f.name} | {f.kind} | {limits} | {f.source} | {f.meaning} |")
    return "\n".join(lines) + "\n"
