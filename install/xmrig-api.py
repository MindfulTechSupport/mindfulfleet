#!/usr/bin/env python3
"""Turns on XMRig's built-in web API so MindfulFleet can show hashrate.

The API is set to listen only on this computer (127.0.0.1) and in restricted, read-only mode,
so nothing on the network can reach it and it can't change the miner. Before changing anything,
this shows the change, asks first, and keeps a backup.
"""
import glob
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

PORT = 37841
REPORTER_ENV = "/etc/mindful-fleet/reporter.env"
API = {"enabled": True, "host": "127.0.0.1", "port": PORT, "access-token": None, "restricted": True}


def say(text=""):
    print(text, flush=True)


def ask(question, assume_yes):
    if assume_yes:
        return True
    try:
        with open("/dev/tty") as tty:
            sys.stdout.write(f"{question} [y/N] ")
            sys.stdout.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        return False


def find_miner():
    """The running XMRig process: its arguments, working folder, owner, and systemd service if any."""
    for folder in glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{folder}/cmdline", "rb") as f:
                args = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if not args or "xmrig" not in os.path.basename(args[0]).lower():
            continue
        pid = int(os.path.basename(folder))
        try:
            exe = os.readlink(f"{folder}/exe")
        except OSError:
            exe = args[0]
        try:
            cwd = os.readlink(f"{folder}/cwd")
            owner = os.stat(folder).st_uid
            with open(f"{folder}/cgroup") as f:
                cgroup = f.read()
        except OSError:
            cwd, owner, cgroup = "/", 0, ""
        service = next((part for part in cgroup.replace("/", " ").split() if part.endswith(".service")), None)
        return {"pid": pid, "args": args, "exe": exe, "cwd": cwd, "owner": owner, "service": service}
    return None


def option(args, short, long):
    for index, arg in enumerate(args):
        if arg in (short, long) and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(long + "="):
            return arg.split("=", 1)[1]
    return None


def find_config(miner):
    args, cwd = miner["args"], miner["cwd"]
    named = option(args, "-c", "--config")
    if named:
        return os.path.join(cwd, named)
    if option(args, "-o", "--url"):
        return None  # Set up entirely with command-line options.
    home = pwd.getpwuid(miner["owner"]).pw_dir if miner["owner"] else "/root"
    binary = args[0] if os.path.isabs(args[0]) else os.path.realpath(os.path.join(cwd, args[0]))
    for candidate in (os.path.join(os.path.dirname(binary), "config.json"), os.path.join(cwd, "config.json"),
                      os.path.join(home, ".xmrig.json"), os.path.join(home, ".config", "xmrig.json")):
        if os.path.isfile(candidate):
            return candidate
    return None


def set_reporter(port, token):
    """Point the reporter at the API, then restart it."""
    if not os.path.isfile(REPORTER_ENV):
        say("The MindfulFleet reporter isn't installed here, so there's nothing to connect yet.")
        return
    with open(REPORTER_ENV) as f:
        lines = [line for line in f.read().splitlines() if not line.startswith("MINDFUL_FLEET_XMRIG")]
    if port != PORT:
        lines.append(f"MINDFUL_FLEET_XMRIG=http://127.0.0.1:{port}")
    if token:
        lines.append(f"MINDFUL_FLEET_XMRIG_TOKEN={token}")
    with open(REPORTER_ENV, "w") as f:
        f.write("\n".join(lines) + "\n")
    subprocess.run(["systemctl", "restart", "mindful-fleet-reporter"], check=False)  # noqa: S603, S607 - fixed systemctl command


def check(port, token):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/2/summary",
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(15):
        try:
            with opener.open(request, timeout=2) as response:
                rate = (json.load(response).get("hashrate") or {}).get("total") or []
            shown = next((r for r in rate if r), None)
            say(f"XMRig's API is answering{f' at {round(shown):,} H/s' if shown else ''}. "
                "Hashrate will show on the dashboard within about 15 seconds.")
            return True
        except (OSError, ValueError):
            time.sleep(2)
    say("XMRig's API isn't answering yet. If the miner is still starting, give it a minute and check the dashboard.")
    return False


def main():
    assume_yes = "--yes" in sys.argv
    if os.geteuid() != 0:
        sys.exit("Please run with sudo.")
    miner = find_miner()
    if not miner:
        sys.exit("XMRig doesn't seem to be running on this computer. Start it, then run this again.")
    say(f"Found XMRig (process {miner['pid']}{', service ' + miner['service'] if miner['service'] else ''}).")

    config = find_config(miner)
    if config:
        try:
            with open(config) as f:
                settings = json.load(f)
        except (OSError, ValueError) as error:
            sys.exit(f"Couldn't read {config} ({error}). No changes made.")
        current = settings.get("http") or {}
        if current.get("enabled") and current.get("port") and current.get("host") in ("127.0.0.1", "localhost", "::1"):
            say(f"XMRig's API is already on, at port {current['port']}. Connecting the reporter to it.")
            set_reporter(current["port"], current.get("access-token"))
            check(current["port"], current.get("access-token"))
            return
        wanted = {**API, "access-token": current.get("access-token")}
        say(f"\nThis will change {config}:")
        say(f"  \"http\": {json.dumps(current) if current else '(not set)'}")
        say(f"  becomes \"http\": {json.dumps(wanted)}")
        say("That turns on XMRig's read-only API for this computer only, then restarts the miner.")
        if not ask("Go ahead?", assume_yes):
            sys.exit("No changes made.")
        backup = f"{config}.before-mindful-fleet-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(config, backup)
        settings["http"] = wanted
        with open(config, "w") as f:  # Writing in place keeps the file's owner.
            json.dump(settings, f, indent=4)
            f.write("\n")
        say(f"Saved. The old settings are in {backup}")
        token = wanted["access-token"]
    else:
        if not miner["service"]:
            say("XMRig is set up with command-line options and isn't running as a service, so this can't change it.")
            say(f"Add these to the command that starts it, then restart it: --http-host=127.0.0.1 --http-port={PORT}")
            sys.exit(1)
        dropin_folder = f"/etc/systemd/system/{miner['service']}.d"
        dropin = f"{dropin_folder}/mindful-fleet-api.conf"
        # systemd needs a full path to the program.
        command = shlex.join([miner["exe"], *miner["args"][1:], "--http-host=127.0.0.1", f"--http-port={PORT}"])
        say(f"\nThis will add {dropin} so {miner['service']} starts XMRig with its read-only API on:")
        say(f"  {command}")
        if not ask("Go ahead?", assume_yes):
            sys.exit("No changes made.")
        os.makedirs(dropin_folder, exist_ok=True)
        with open(dropin, "w") as f:
            f.write(f"# Added by MindfulFleet. Delete this file and restart {miner['service']} to undo.\n"
                    f"[Service]\nExecStart=\nExecStart={command}\n")
        subprocess.run(["systemctl", "daemon-reload"], check=False)  # noqa: S603, S607 - fixed systemctl command
        say(f"Saved. To undo, delete {dropin} and restart {miner['service']}.")
        token = None

    if miner["service"]:
        say(f"Restarting {miner['service']}…")
        subprocess.run(["systemctl", "restart", miner["service"]], check=False)  # noqa: S603, S607 - fixed systemctl command
    else:
        say("XMRig isn't running as a service, so please restart it yourself now.")
    set_reporter(PORT, token)
    check(PORT, token)


if __name__ == "__main__":
    main()
