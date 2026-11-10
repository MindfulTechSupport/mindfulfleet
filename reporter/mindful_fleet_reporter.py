#!/usr/bin/env python3
"""Mindful Fleet reporter: reads this computer's health and sends it to the hub.

Standard library only. Reads files under /proc and /sys, plus `vcgencmd get_throttled`.
It only ever sends health numbers. It accepts no commands and opens no ports.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.2.9"
XMRIG_DEFAULT = "http://127.0.0.1:37841"  # The port "mindful-fleet xmrig" sets up.
DEFAULT_INTERVAL = 15


def _read(root, path):
    try:
        with open(os.path.join(root, path.lstrip("/")), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def read_temperature_c(root="/"):
    raw = _read(root, "/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw.strip()) / 1000, 1)
    except (AttributeError, ValueError):
        return None


def read_throttled(run=subprocess.run):
    """Return the raw throttle bits from the firmware, or None if unavailable."""
    try:
        result = run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return int(result.stdout.strip().split("=", 1)[1], 16)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def read_fan_rpm(root="/"):
    pattern = os.path.join(root, "sys/class/hwmon/hwmon*/fan*_input")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def read_fan_percent(root="/"):
    """How hard the board is driving its fan, from 0 to 100, or None if it isn't controlling one."""
    pattern = os.path.join(root, "sys/class/hwmon/hwmon*/pwm1")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                return round(int(f.read().strip()) * 100 / 255)
        except (OSError, ValueError):
            continue
    return None


def read_pmic(run=subprocess.run):
    """Input voltage and estimated board power from a Pi 5's power chip.

    The chip reports voltage and current for each supply rail on the board. Their products add up
    to what the board itself uses, not counting USB devices or fans powered elsewhere.
    """
    try:
        result = run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None, None
    except (OSError, subprocess.SubprocessError):
        return None, None
    amps, volts = {}, {}
    for rail, kind, value in re.findall(r"(\S+)_([AV])\s+(?:current|volt)\(\d+\)=([\d.]+)[AV]", result.stdout):
        (amps if kind == "A" else volts)[rail] = float(value)
    input_volts = round(volts["EXT5V"], 2) if "EXT5V" in volts else None
    shared = amps.keys() & volts.keys()
    watts = round(sum(amps[rail] * volts[rail] for rail in shared), 2) if shared else None
    return input_volts, watts


def read_input_volts(run=subprocess.run):
    """Voltage arriving at the board. Only a Pi 5's power chip reports this."""
    return read_pmic(run)[0]


def read_cpu_mhz(root="/"):
    """Each core's clock speed right now, and the fastest it's allowed to run, in MHz."""
    now, top = [], None
    for path in sorted(glob.glob(os.path.join(root, "sys/devices/system/cpu/cpu[0-9]*")),
                       key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0)):
        raw = _read(path, "cpufreq/scaling_cur_freq")
        try:
            now.append(round(int(raw.strip()) / 1000))
        except (AttributeError, ValueError):
            continue
        if top is None:
            for name in ("cpufreq/scaling_max_freq", "cpufreq/cpuinfo_max_freq"):
                try:
                    top = round(int(_read(path, name).strip()) / 1000)
                    break
                except (AttributeError, ValueError):
                    pass
    return now, top


def read_load(root="/"):
    raw = _read(root, "/proc/loadavg")
    try:
        return [float(value) for value in raw.split()[:3]]
    except (AttributeError, ValueError):
        return None


def read_net_bytes(root="/"):
    """Total bytes received and sent on every interface except loopback."""
    text = _read(root, "/proc/net/dev")
    if not text:
        return None
    received = sent = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if name.strip() == "lo" or len(fields) < 9:
            continue
        received += int(fields[0])
        sent += int(fields[8])
    return received, sent, time.monotonic()


def net_rates(before, after):
    if not before or not after or after[2] <= before[2]:
        return None, None
    seconds = after[2] - before[2]
    return (max(0, round((after[0] - before[0]) / seconds)),
            max(0, round((after[1] - before[1]) / seconds)))


POWER_SENSOR_CHIPS = ("ina219", "ina220", "ina226", "ina230", "ina231", "ina260", "ina238")


def read_power_sensors(root="/", labels=None):
    """Extra power sensors the Linux kernel already knows about, such as an INA219 on a fan's supply line.

    The kernel's driver reports bus voltage in millivolts, current in milliamps and power in microwatts."""
    labels = labels or []
    found = []
    for folder in sorted(glob.glob(os.path.join(root, "sys/class/hwmon/hwmon*"))):
        chip = (_read(folder, "name") or "").strip()
        if chip not in POWER_SENSOR_CHIPS:
            continue

        def value(name, scale):
            raw = _read(folder, name)
            try:
                return round(int(raw.strip()) / scale, 3)
            except (AttributeError, ValueError):
                return None
        volts, amps, watts = value("in1_input", 1000), value("curr1_input", 1000), value("power1_input", 1_000_000)
        if watts is None and volts is not None and amps is not None:
            watts = round(volts * amps, 3)
        index = len(found)
        found.append({"label": labels[index] if index < len(labels) else f"Power sensor {index + 1}",
                      "chip": chip, "volts": volts, "amps": amps, "watts": watts})
    return found[:8]


def read_wifi_dbm(root="/"):
    """Wi-Fi signal strength in dBm, or None on a wired connection."""
    for line in (_read(root, "/proc/net/wireless") or "").splitlines()[2:]:
        fields = line.split(":", 1)[-1].split()
        try:
            return int(float(fields[2]))
        except (IndexError, ValueError):
            continue
    return None


def root_device(path="/"):
    try:
        device = os.stat(path).st_dev
        return os.major(device), os.minor(device)
    except OSError:
        return None


def read_disk_bytes(device, root="/"):
    """Bytes read from and written to the storage holding the system, from /proc/diskstats."""
    if not device:
        return None
    for line in (_read(root, "/proc/diskstats") or "").splitlines():
        fields = line.split()
        if len(fields) > 9 and (int(fields[0]), int(fields[1])) == tuple(device):
            return int(fields[5]) * 512, int(fields[9]) * 512, time.monotonic()
    return None


def read_process_ticks(root="/"):
    """CPU time used so far by each running program, in clock ticks."""
    ticks = {}
    for path in glob.glob(os.path.join(root, "proc/[0-9]*/stat")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue  # The program ended while we were looking.
        if ")" not in text:
            continue
        name = text[text.index("(") + 1:text.rindex(")")]
        fields = text[text.rindex(")") + 2:].split()
        try:
            ticks[path] = (int(fields[11]) + int(fields[12]), name)
        except (IndexError, ValueError):
            continue
    return ticks, time.monotonic()


def busiest_process(before, after, ticks_per_second=None):
    """The program that used the most CPU between two samples, and how many cores' worth it used, as a percent."""
    if not before or not after or after[1] <= before[1]:
        return None, None
    ticks_per_second = ticks_per_second or os.sysconf("SC_CLK_TCK")
    best_name, best_ticks = None, 0
    for key, (used, name) in after[0].items():
        previous = before[0].get(key)
        if previous and previous[1] == name and used - previous[0] > best_ticks:
            best_name, best_ticks = name, used - previous[0]
    if not best_name:
        return None, None
    return best_name, round(100 * best_ticks / ticks_per_second / (after[1] - before[1]), 1)


def read_xmrig(url, token=None, timeout=2):
    """Mining numbers from XMRig's built-in web API, which it serves only on this computer.

    Read-only: the API is turned on in "restricted" mode, so it can't change the miner.
    Nothing here is sent anywhere except to the hub, and the wallet address isn't read.
    """
    if not url or url == "off":
        return {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def get(path):
        request = urllib.request.Request(url.rstrip("/") + path, headers=headers)
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read(500_000))

    try:
        summary = get("/2/summary")
    except (OSError, ValueError):
        return {}
    if not isinstance(summary, dict):
        return {}
    rates = (summary.get("hashrate") or {}).get("total") or []
    rate = lambda i: rates[i] if len(rates) > i and isinstance(rates[i], (int, float)) else None
    results = summary.get("results") or {}
    connection = summary.get("connection") or {}
    found = {
        "hashrate": rate(1) if rate(1) is not None else rate(0),
        "hashrate_15m": rate(2),
        "shares_good": results.get("shares_good"),
        "shares_total": results.get("shares_total"),
        "pool": connection.get("pool"),
        # XMRig reports the TLS version when the pool link is encrypted, and nothing when it isn't.
        "pool_tls": (connection.get("tls") or "none") if connection.get("pool") else None,
        "rig_id": summary.get("worker_id"),
        "algo": summary.get("algo"),
        "miner_version": summary.get("version"),
    }
    try:
        for backend in get("/2/backends"):
            if backend.get("type") == "cpu" and backend.get("enabled"):
                found["mining_threads"] = len(backend.get("threads") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return found


def read_core_volts(run=subprocess.run):
    try:
        result = run(["vcgencmd", "measure_volts", "core"], capture_output=True, text=True, timeout=5)
        match = re.search(r"volt=([\d.]+)V", result.stdout) if result.returncode == 0 else None
        return round(float(match.group(1)), 3) if match else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def read_os_name(root="/"):
    match = re.search(r'^PRETTY_NAME="?([^"\n]*)', _read(root, "/etc/os-release") or "", re.M)
    return match.group(1)[:60] if match else None


def read_cpu_times(root="/"):
    """Return {core_index: (busy_ticks, total_ticks)} from /proc/stat."""
    text = _read(root, "/proc/stat") or ""
    cores = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("cpu") or parts[0] == "cpu":
            continue
        try:
            index = int(parts[0][3:])
            values = [int(v) for v in parts[1:9]]
        except ValueError:
            continue
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        cores[index] = (sum(values) - idle, sum(values))
    return cores


def cpu_percent(before, after):
    result = []
    for index in sorted(after):
        if index not in before:
            continue
        busy = after[index][0] - before[index][0]
        total = after[index][1] - before[index][1]
        result.append(round(100 * busy / total, 1) if total > 0 else 0.0)
    return result


def read_memory(root="/"):
    text = _read(root, "/proc/meminfo") or ""
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0]) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if not total or available is None:
        return None, None
    return total, total - available


def read_swap(root="/"):
    values = {}
    for line in (_read(root, "/proc/meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0]) * 1024
    total, free = values.get("SwapTotal"), values.get("SwapFree")
    if total is None or free is None:
        return None, None
    return total, total - free


def read_disk(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used
    except OSError:
        return None, None


def read_uptime(root="/"):
    raw = _read(root, "/proc/uptime")
    try:
        return int(float(raw.split()[0]))
    except (AttributeError, IndexError, ValueError):
        return None


def read_model(root="/"):
    raw = _read(root, "/proc/device-tree/model")
    return raw.replace("\x00", "").strip() if raw else None


def read_board_ram_mb(root="/"):
    """Memory fitted to the board, decoded from the revision code that Raspberry Pi boards report."""
    match = re.search(r"^Revision\s*:\s*([0-9a-fA-F]+)", _read(root, "/proc/cpuinfo") or "", re.M)
    if not match:
        return None
    code = int(match.group(1), 16)
    if not code & (1 << 23):  # Older boards use a different scheme.
        return None
    return 256 << ((code >> 20) & 7)


def machine_identity(hostname, root="/"):
    """A stable id. Hostname is mixed in so cloned SD cards with new names stay separate,
    and machine-id is mixed in so two Pis left named 'raspberrypi' stay separate."""
    machine_id = (_read(root, "/etc/machine-id") or "").strip()
    return hashlib.sha256(f"{machine_id}|{hostname}".encode()).hexdigest()[:16]


def local_ip(hub_url):
    """Find the address this machine uses to reach the hub. Sends no packets."""
    parsed = urllib.parse.urlparse(hub_url)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((parsed.hostname or "192.0.2.1", parsed.port or 80))
            return s.getsockname()[0]
    except OSError:
        return None


def collect(hub_url, cpu_before, cpu_after, root="/", disk_path="/", run=subprocess.run,
            hostname=None, net_before=None, net_after=None, disk_before=None, disk_after=None,
            procs_before=None, procs_after=None, xmrig_url=None, xmrig_token=None):
    hostname = hostname or socket.gethostname()
    mem_total, mem_used = read_memory(root)
    swap_total, swap_used = read_swap(root)
    mhz_now, mhz_max = read_cpu_mhz(root)
    input_volts, board_watts = read_pmic(run)
    net_in, net_out = net_rates(net_before, net_after)
    disk_in, disk_out = net_rates(disk_before, disk_after)  # Same arithmetic: bytes per second between samples.
    top_name, top_cpu = busiest_process(procs_before, procs_after)
    disk_total, disk_used = read_disk(disk_path)
    return {
        "id": machine_identity(hostname, root),
        "hostname": hostname,
        "model": read_model(root),
        "ip": local_ip(hub_url),
        "reporter_version": VERSION,
        "temp_c": read_temperature_c(root),
        "throttled": read_throttled(run),
        "fan_rpm": read_fan_rpm(root),
        "fan_percent": read_fan_percent(root),
        "input_volts": input_volts,
        "board_watts": board_watts,
        "cpu_per_core": cpu_percent(cpu_before, cpu_after),
        "mem_total": mem_total,
        "mem_used": mem_used,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "uptime_s": read_uptime(root),
        "board_ram_mb": read_board_ram_mb(root),
        "cpu_mhz": mhz_now,
        "cpu_max_mhz": mhz_max,
        "load_avg": read_load(root),
        "swap_total": swap_total,
        "swap_used": swap_used,
        "net_rx_bps": net_in,
        "net_tx_bps": net_out,
        "core_volts": read_core_volts(run),
        **read_xmrig(xmrig_url, xmrig_token),
        "wifi_dbm": read_wifi_dbm(root),
        "sensors": read_power_sensors(root, [l.strip() for l in os.environ.get("MINDFUL_FLEET_SENSOR_LABELS", "").split(",") if l.strip()]),
        "disk_read_bps": disk_in,
        "disk_write_bps": disk_out,
        "top_process": top_name,
        "top_process_cpu": top_cpu,
        "os_name": read_os_name(root),
        "kernel": os.uname().release[:40] if root == "/" else None,
    }


def send(hub_url, token, report, timeout=10):
    request = urllib.request.Request(
        hub_url.rstrip("/") + "/api/report",
        data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send this computer's health to a Mindful Fleet hub.")
    parser.add_argument("--hub", default=os.environ.get("MINDFUL_FLEET_HUB"),
                        help="hub address, for example http://192.168.1.10:8742")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("MINDFUL_FLEET_INTERVAL", DEFAULT_INTERVAL)))
    parser.add_argument("--once", action="store_true", help="send one report and exit")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print one report and send nothing")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    token = os.environ.get("MINDFUL_FLEET_TOKEN")

    if not args.print_only and (not args.hub or not token):
        parser.error("needs --hub (or MINDFUL_FLEET_HUB) and the MINDFUL_FLEET_TOKEN environment variable")

    device = root_device()
    sample = lambda: (read_cpu_times(), read_net_bytes(), read_disk_bytes(device), read_process_ticks())
    previous = sample()
    time.sleep(1)
    failing = False
    while True:
        current = sample()
        report = collect(args.hub or "", previous[0], current[0], net_before=previous[1], net_after=current[1],
                         disk_before=previous[2], disk_after=current[2],
                         procs_before=previous[3], procs_after=current[3],
                         xmrig_url=os.environ.get("MINDFUL_FLEET_XMRIG", XMRIG_DEFAULT),
                         xmrig_token=os.environ.get("MINDFUL_FLEET_XMRIG_TOKEN"))
        previous = current
        if args.print_only:
            print(json.dumps(report, indent=2))
            return 0
        try:
            send(args.hub, token, report)
            if failing:
                print("reconnected to hub", flush=True)
            failing = False
        except urllib.error.HTTPError as error:
            hint = " (the token doesn't match the hub's)" if error.code == 401 else ""
            print(f"hub refused the report: HTTP {error.code}{hint}", file=sys.stderr, flush=True)
            failing = True
            if args.once:
                return 1
        except (OSError, ValueError) as error:
            if not failing or args.once:
                print(f"can't reach hub: {error}", file=sys.stderr, flush=True)
            failing = True
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    sys.exit(main())
