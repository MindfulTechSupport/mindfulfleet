"""Which paid features this hub may use.

Until MindfulFleet Plus can be bought, the owner turns paid features on with `sudo mindful-fleet plan owner`,
which writes one word to /etc/mindful-fleet/plan. Only root can write there, so the dashboard can't change it.
The licence check replaces this file when Plus goes on sale (Ed, D-086).

Plus: History. Pro (Plus and more): exporting History to a spreadsheet (Ed, D-083).
Anything missing or unreadable means the free plan.
"""
import os
from pathlib import Path

DEFAULT_PATH = "/etc/mindful-fleet/plan"
PLANS = {
    "free": {"plus": False, "pro": False},
    "plus": {"plus": True, "pro": False},
    "pro": {"plus": True, "pro": True},
    "owner": {"plus": True, "pro": True},
    "demo": {"plus": True, "pro": True},  # The --demo dashboard shows everything.
}


class Plan:
    def __init__(self, path=None, fixed=None):
        self._path = Path(path or os.environ.get("MINDFUL_FLEET_PLAN_FILE", DEFAULT_PATH))
        self._fixed = fixed
        self._seen = (None, "free")

    def name(self):
        if self._fixed:
            return self._fixed
        try:
            info = self._path.stat()
        except OSError:
            return "free"
        stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        if stamp != self._seen[0]:
            try:
                word = self._path.read_text(encoding="utf-8", errors="replace")[:40].strip().lower()
            except OSError:
                word = "free"
            self._seen = (stamp, word if word in PLANS else "free")
        return self._seen[1]

    def current(self):
        name = self.name()
        return {"name": name, **PLANS[name]}
