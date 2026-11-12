#!/usr/bin/env python3
"""MindfulFleet tools that run on each Pi: free up space, and a Wi-Fi watchdog.

Standard library only. Called by the `mindful-fleet` command (as root):
    mindful_fleet_tools.py tidy [--yes]
    mindful_fleet_tools.py tidy-undo
    mindful_fleet_tools.py wifi-check
"""
import glob
import json
import os
import shutil
import subprocess  # nosec B404 - fixed commands only, never a shell
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("MINDFUL_FLEET_STATE_DIR", "/var/lib/mindful-fleet"))
JOURNAL_CAP = Path("/etc/systemd/journald.conf.d/mindful-fleet.conf")
JOURNAL_CAP_TEXT = "# Added by `mindful-fleet tidy`. Remove with `sudo mindful-fleet tidy undo`.\n[Journal]\nSystemMaxUse=100M\n"
JOURNAL_KEEP = "100M"

MISSES_BEFORE_RESTART = 3  # One check every 2 minutes, so about 6 minutes without the router.
RESTARTS_BEFORE_REBOOT = 3
REBOOT_GAP = 3600  # Never reboot more than once an hour.


# ---------- shared ----------

def run(args, timeout=60):
    """Run a fixed command (a list, never a shell). Returns (ok, output)."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def human(size):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} bytes"


def ask(question, answer=None):
    if answer is not None:
        return answer
    try:
        reply = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in ("y", "yes")


# ---------- free up space ----------

def apt_cache_size(folder="/var/cache/apt/archives"):
    return sum(os.path.getsize(f) for f in glob.glob(os.path.join(folder, "*.deb")) if os.path.isfile(f))


def journal_size():
    """Bytes the system log uses, from `journalctl --disk-usage`, or None."""
    ok, text = run(["journalctl", "--disk-usage"])
    return parse_journal_usage(text) if ok else None


def parse_journal_usage(text):
    # "Archived and active journals take up 456.0M in the file system."
    for word in text.split():
        number, unit = word[:-1], word[-1:].upper()
        scale = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(unit)
        if scale:
            try:
                return int(float(number) * scale)
            except ValueError:
                continue
    return None


def tidy(yes=None, root="/"):
    before = shutil.disk_usage(root).free
    cache = apt_cache_size()
    logs = journal_size()
    print(f"Free space now: {human(before)}")
    print(f"  Downloaded update files (safe to clear): {human(cache)}")
    print(f"  System log: {human(logs) if logs is not None else 'unknown'} (keeps the newest {JOURNAL_KEEP})")
    if not ask("Clear these now?", yes):
        print("Nothing changed.")
        return
    run(["apt-get", "clean"], timeout=300)
    run(["journalctl", f"--vacuum-size={JOURNAL_KEEP}"], timeout=300)
    if not JOURNAL_CAP.exists() and ask(
            f"Also keep the system log under {JOURNAL_KEEP} from now on? (Less writing to the SD card.)", yes):
        JOURNAL_CAP.parent.mkdir(parents=True, exist_ok=True)
        JOURNAL_CAP.write_text(JOURNAL_CAP_TEXT)
        run(["systemctl", "restart", "systemd-journald"])
        print(f"Log cap added ({JOURNAL_CAP}). Undo: sudo mindful-fleet tidy undo")
    after = shutil.disk_usage(root).free
    print(f"Free space now: {human(after)} ({human(max(0, after - before))} freed)")


def tidy_undo():
    if not JOURNAL_CAP.exists():
        print("There's no log cap to remove.")
        return
    JOURNAL_CAP.unlink()
    run(["systemctl", "restart", "systemd-journald"])
    print("Log cap removed; the system log uses its normal limit again.")


# ---------- Wi-Fi watchdog ----------

def default_route(route_file="/proc/net/route"):
    """(interface, gateway address) of the default route, or (None, None)."""
    try:
        lines = Path(route_file).read_text().splitlines()[1:]
    except OSError:
        return None, None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000":
            gateway = bytes.fromhex(fields[2])[::-1]
            return fields[0], ".".join(str(b) for b in gateway)
    return None, None


def decide(state, reachable, now):
    """What to do after one check. Returns (action, new state). Actions: none, restart, reboot."""
    state = {"misses": 0, "restarts": 0, "last_reboot": 0, **state}
    if reachable:
        return "none", {**state, "misses": 0, "restarts": 0}
    state["misses"] += 1
    if state["misses"] < MISSES_BEFORE_RESTART:
        return "none", state
    state["misses"] = 0
    if state["restarts"] < RESTARTS_BEFORE_REBOOT:
        state["restarts"] += 1
        return "restart", state
    if now - state["last_reboot"] >= REBOOT_GAP:
        return "reboot", {**state, "restarts": 0, "last_reboot": now}
    return "none", state


def restart_wifi(interface):
    if shutil.which("nmcli"):
        run(["nmcli", "radio", "wifi", "off"])
        time.sleep(3)
        return run(["nmcli", "radio", "wifi", "on"])[0]
    run(["ip", "link", "set", interface, "down"])
    time.sleep(3)
    return run(["ip", "link", "set", interface, "up"])[0]


def wifi_check(now=None, route_file="/proc/net/route"):
    interface, gateway = default_route(route_file)
    if not interface or not interface.startswith("wl"):
        return  # Not using Wi-Fi right now (cable, or no network at all): nothing for the watchdog to do.
    path = STATE_DIR / "wifi-watchdog.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    reachable = run(["ping", "-c", "1", "-W", "3", gateway], timeout=10)[0]
    action, state = decide(state, reachable, now or time.time())
    if action == "restart":
        print(f"Wi-Fi watchdog: can't reach the router ({gateway}); restarting Wi-Fi (try {state['restarts']}).")
        restart_wifi(interface)
    elif action == "reboot":
        print("Wi-Fi watchdog: Wi-Fi restarts didn't help; rebooting (at most once an hour).")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    if action == "reboot":
        run(["systemctl", "reboot"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    commands = {"tidy": lambda: tidy(yes=True if "--yes" in argv else None), "tidy-undo": tidy_undo,
                "wifi-check": wifi_check}
    if command in commands:
        commands[command]()
        return 0
    print("Usage: mindful_fleet_tools.py tidy [--yes] | tidy-undo | wifi-check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
