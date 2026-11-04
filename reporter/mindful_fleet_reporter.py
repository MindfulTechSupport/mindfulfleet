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
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
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
            hostname=None):
    hostname = hostname or socket.gethostname()
    mem_total, mem_used = read_memory(root)
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
        "cpu_per_core": cpu_percent(cpu_before, cpu_after),
        "mem_total": mem_total,
        "mem_used": mem_used,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "uptime_s": read_uptime(root),
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

    before = read_cpu_times()
    time.sleep(1)
    failing = False
    while True:
        after = read_cpu_times()
        report = collect(args.hub or "", before, after)
        before = after
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
