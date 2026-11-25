# SPDX-License-Identifier: MIT
"""UPS reader add-on: status, battery, minutes left and load through NUT's upsc.

Output is the same as the reporter's built-in UPS reader (tests/test_engine.py checks they match).
"""

import math
import shutil
import subprocess  # noqa: S404 - runs upsc only, with a fixed argument list


def read_ups(run=subprocess.run, name=None):
    if not shutil.which("upsc"):
        return None
    try:
        if not name:
            listed = run(["upsc", "-l"], capture_output=True, text=True, timeout=3, check=False)
            names = listed.stdout.split() if listed.returncode == 0 else []
            if not names:
                return None
            name = f"{names[0]}@localhost"
        done = run(["upsc", name], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    values = dict(line.split(": ", 1) for line in done.stdout.splitlines() if ": " in line)

    def number(key):
        try:
            number = float(values[key])
        except (KeyError, ValueError):
            return None
        return number if math.isfinite(number) else None  # No NaN or infinity

    return {"name": name.split("@", 1)[0][:40], "status": values.get("ups.status", "").strip()[:40],
            "charge": number("battery.charge"), "runtime_s": number("battery.runtime"), "load": number("ups.load")}


def read(settings):
    ups = read_ups(settings.get("run", subprocess.run), settings.get("name") or None)
    return {"ups": ups} if ups is not None else {}
