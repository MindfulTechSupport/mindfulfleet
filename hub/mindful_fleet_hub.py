#!/usr/bin/env python3
"""MindfulFleet hub: receives reports and serves the dashboard. Standard library only."""
import argparse
import getpass
import hmac
import json
import math
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth  # noqa: E402
import discover  # noqa: E402
import health  # noqa: E402
from averages import History  # noqa: E402

VERSION = "0.2.11"
DEFAULT_PORT = 8742
MAX_BODY_BYTES = 16 * 1024
MAX_PIS = 256
HISTORY_SAMPLES = 120  # 30 minutes at the default 15-second interval.
SAVE_EVERY_SECONDS = 300  # Every 5 minutes, to spare SD cards.
UPDATE_CHECK_SECONDS = 6 * 3600
RELEASE_VERSION_URL = "https://raw.githubusercontent.com/MindfulTechSupport/mindfulfleet/release/VERSION"
MAX_LAYOUT_BYTES = 16 * 1024
MAX_AUTH_BYTES = 1024
HISTORY_READINGS = ("temp", "hashrate", "watts", "cpu", "fan", "memory", "clock", "sensor_watts")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_ID_RE = re.compile(r"^[a-f0-9]{8,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_IP_RE = re.compile(r"^[0-9a-fA-F:.]{2,45}$")


class BadReport(ValueError):
    pass


def _number(value, low, high):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadReport("expected a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise BadReport("number out of range")
    return value


def _numbers(values, low, high, limit=256):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > limit:
        raise BadReport("bad number list")
    return [_number(v, low, high) for v in values]


def _text(value, length):
    if not isinstance(value, str):
        return None
    return "".join(ch for ch in value[:length] if ch.isprintable()) or None


def _sensors(values):
    """Extra power sensors, such as one on a fan's supply line."""
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 8:
        raise BadReport("bad sensor list")
    cleaned = []
    for item in values:
        if not isinstance(item, dict):
            raise BadReport("bad sensor")
        cleaned.append({
            "label": _text(item.get("label"), 40) or "Power sensor",
            "chip": _text(item.get("chip"), 20),
            "volts": _number(item.get("volts"), 0, 100),
            "amps": _number(item.get("amps"), -100, 100),
            "watts": _number(item.get("watts"), 0, 10000),
        })
    return cleaned


def clean_report(raw):
    """Keep only known fields, and refuse anything malformed."""
    if not isinstance(raw, dict):
        raise BadReport("report must be an object")
    pi_id, hostname = raw.get("id"), raw.get("hostname")
    if not isinstance(pi_id, str) or not _ID_RE.match(pi_id):
        raise BadReport("bad id")
    if not isinstance(hostname, str) or not _HOST_RE.match(hostname):
        raise BadReport("bad hostname")
    ip = raw.get("ip")
    if ip is not None and (not isinstance(ip, str) or not _IP_RE.match(ip)):
        raise BadReport("bad ip")
    model, version = raw.get("model"), raw.get("reporter_version")
    cores = raw.get("cpu_per_core", [])
    if not isinstance(cores, list) or len(cores) > 256:
        raise BadReport("bad cpu list")
    terabytes = 1 << 50
    return {
        "id": pi_id,
        "hostname": hostname,
        "ip": ip,
        "model": model[:80] if isinstance(model, str) else None,
        "reporter_version": version[:20] if isinstance(version, str) else None,
        "temp_c": _number(raw.get("temp_c"), -50, 200),
        "throttled": _number(raw.get("throttled"), 0, 2**32),
        "fan_rpm": _number(raw.get("fan_rpm"), 0, 100000),
        "fan_percent": _number(raw.get("fan_percent"), 0, 100),
        "input_volts": _number(raw.get("input_volts"), 0, 60),
        "board_watts": _number(raw.get("board_watts"), 0, 1000),
        "cpu_per_core": [_number(c, 0, 100) for c in cores],
        "mem_total": _number(raw.get("mem_total"), 0, terabytes),
        "mem_used": _number(raw.get("mem_used"), 0, terabytes),
        "disk_total": _number(raw.get("disk_total"), 0, terabytes),
        "disk_used": _number(raw.get("disk_used"), 0, terabytes),
        "uptime_s": _number(raw.get("uptime_s"), 0, 10**10),
        "board_ram_mb": _number(raw.get("board_ram_mb"), 0, 1 << 22),
        "cpu_mhz": _numbers(raw.get("cpu_mhz"), 0, 100000),
        "cpu_max_mhz": _number(raw.get("cpu_max_mhz"), 0, 100000),
        "load_avg": _numbers(raw.get("load_avg"), 0, 100000, limit=3),
        "swap_total": _number(raw.get("swap_total"), 0, terabytes),
        "swap_used": _number(raw.get("swap_used"), 0, terabytes),
        "net_rx_bps": _number(raw.get("net_rx_bps"), 0, terabytes),
        "net_tx_bps": _number(raw.get("net_tx_bps"), 0, terabytes),
        "core_volts": _number(raw.get("core_volts"), 0, 10),
        "os_name": _text(raw.get("os_name"), 60),
        "wifi_dbm": _number(raw.get("wifi_dbm"), -150, 50),
        "disk_read_bps": _number(raw.get("disk_read_bps"), 0, terabytes),
        "disk_write_bps": _number(raw.get("disk_write_bps"), 0, terabytes),
        "top_process": _text(raw.get("top_process"), 40),
        "top_process_cpu": _number(raw.get("top_process_cpu"), 0, 100000),
        "sensors": _sensors(raw.get("sensors")),
        "hashrate": _number(raw.get("hashrate"), 0, 1e12),
        "hashrate_15m": _number(raw.get("hashrate_15m"), 0, 1e12),
        "mining_threads": _number(raw.get("mining_threads"), 0, 4096),
        "shares_good": _number(raw.get("shares_good"), 0, 1e15),
        "shares_total": _number(raw.get("shares_total"), 0, 1e15),
        "pool": _text(raw.get("pool"), 80),
        "pool_tls": _text(raw.get("pool_tls"), 20),
        "rig_id": _text(raw.get("rig_id"), 40),
        "algo": _text(raw.get("algo"), 30),
        "miner_version": _text(raw.get("miner_version"), 20),
        "kernel": _text(raw.get("kernel"), 40),
    }


class Fleet:
    """Everything the hub knows. Remembers Pis across restarts, so a dead one still shows as offline."""

    def __init__(self, state_path=None, clock=time.time):
        self._lock = threading.Lock()
        self._pis = {}
        self._state_path = Path(state_path) if state_path else None
        self._clock = clock
        self._memory_layout = {}
        self.latest_version = None
        self._load()

    def _load(self):
        if not self._state_path or not self._state_path.exists():
            return
        try:
            for pi_id, saved in json.loads(self._state_path.read_text()).items():
                self._pis[pi_id] = {
                    "report": clean_report(saved["report"]),
                    "last_seen": float(saved["last_seen"]),
                    "history": deque(saved.get("history", []), maxlen=HISTORY_SAMPLES),
                    "averages": History(saved.get("averages")),
                    "fan_max_rpm": saved.get("fan_max_rpm", 0),
                    "hashrate_max": saved.get("hashrate_max", 0),
                }
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"Ignoring unreadable state file: {error}", file=sys.stderr)
            self._pis = {}

    def save(self):
        if not self._state_path:
            return
        with self._lock:
            data = {
                pi_id: {
                    "report": p["report"],
                    "last_seen": p["last_seen"],
                    "history": list(p["history"]),
                    "averages": p["averages"].to_saved(),
                    "fan_max_rpm": p["fan_max_rpm"],
                    "hashrate_max": p["hashrate_max"],
                }
                for pi_id, p in self._pis.items()
            }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self._state_path.parent, delete=False) as f:
            json.dump(data, f)
        os.replace(f.name, self._state_path)

    def record(self, report):
        """Store a cleaned report. Returns False when the fleet is full."""
        with self._lock:
            pi = self._pis.get(report["id"])
            if pi is None:
                if len(self._pis) >= MAX_PIS:
                    return False
                pi = self._pis[report["id"]] = {
                    "history": deque(maxlen=HISTORY_SAMPLES),
                    "averages": History(),
                    "fan_max_rpm": 0,
                    "hashrate_max": 0,
                }
            pi["report"] = report
            pi["last_seen"] = self._clock()
            if report["temp_c"] is not None:
                pi["history"].append(report["temp_c"])
            pi["averages"].add(pi["last_seen"], report)
            pi["fan_max_rpm"] = max(pi["fan_max_rpm"], report.get("fan_rpm") or 0)
            # The 15-minute figure, so one lucky 10-second burst doesn't set the bar.
            pi["hashrate_max"] = max(pi["hashrate_max"], report.get("hashrate_15m") or 0)
        return True

    def history(self, pi_id, names=HISTORY_READINGS):
        with self._lock:
            pi = self._pis.get(pi_id)
            return pi["averages"].series(self._clock(), names) if pi else None

    def seed_history(self, report, when):
        """Used by demo mode to fill in a day of pretend history."""
        with self._lock:
            pi = self._pis.get(report["id"])
            if pi:
                pi["averages"].add(when, report)

    def age_all(self, seconds):
        """Used by demo mode to make a Pi look like it went quiet a while ago."""
        with self._lock:
            for pi in self._pis.values():
                pi["last_seen"] -= seconds

    def known_addresses(self):
        """{address: name} for every computer that reports, for Find my Pis."""
        with self._lock:
            return {p["report"]["ip"]: p["report"]["hostname"] for p in self._pis.values() if p["report"].get("ip")}

    def snapshot(self):
        now = self._clock()
        with self._lock:
            pis = [(p["report"], p["last_seen"], list(p["history"]), p["averages"].averages(now), p["fan_max_rpm"],
                    p["hashrate_max"]) for p in self._pis.values()]
        cards = []
        for report, last_seen, history, averages, fan_max_rpm, hashrate_max in pis:
            age = max(0, now - last_seen)
            cards.append({
                **report,
                "age_s": round(age),
                "history": history,
                "averages": averages,
                "fan_max_rpm": fan_max_rpm,
                "hashrate_max": hashrate_max,
                "health": health.assess(report, age, fan_max_rpm),
            })
        cards.sort(key=lambda c: _natural_key(c["hostname"]))
        return {"version": VERSION, "latest_version": self.latest_version, "server_time": now, "pis": cards}

    def load_layout(self):
        path = self._layout_path()
        if not path:
            return self._memory_layout
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def save_layout(self, layout):
        path = self._layout_path()
        if not path:
            self._memory_layout = layout
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
            json.dump(layout, f)
        os.replace(f.name, path)

    def _layout_path(self):
        return self._state_path.with_name("layout.json") if self._state_path else None


_NUMBER_WORDS = {word: index + 1 for index, word in enumerate(
    "one two three four five six seven eight nine ten eleven twelve".split())}


def _natural_key(text):
    """Sort so pi2 comes before pi10, and BrainOne before BrainTwo."""
    spelled = re.sub("|".join(_NUMBER_WORDS), lambda m: f"{_NUMBER_WORDS[m.group()]:02d}", text.lower())
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", spelled) if part]


def clean_layout(raw):
    """Keep only the shared parts of a dashboard layout. Card size stays in each browser."""
    if not isinstance(raw, dict):
        raise BadReport("layout must be an object")
    ids = [i for i in raw.get("order", []) if isinstance(i, str) and _ID_RE.match(i)][:MAX_PIS]
    groups = []
    for group in raw.get("groups", [])[:64]:
        if isinstance(group, dict) and isinstance(group.get("id"), str) and re.match(r"^g[a-z0-9]{1,20}$", group["id"]):
            groups.append({"id": group["id"], "name": _text(group.get("name"), 40) or "Group"})
    known = {g["id"] for g in groups}
    members = raw.get("members", {})
    members = {k: v for k, v in members.items() if isinstance(k, str) and _ID_RE.match(k) and v in known} \
        if isinstance(members, dict) else {}
    hidden = [h for h in raw.get("hidden", []) if isinstance(h, str) and re.match(r"^[a-z]{1,20}$", h)][:40]
    price = raw.get("price")
    price = price if isinstance(price, (int, float)) and not isinstance(price, bool) and 0 <= price <= 10 else None
    return {"order": ids, "groups": groups, "members": members, "hidden": hidden, "price": price}


def check_latest_version(fleet, stop, url=RELEASE_VERSION_URL):
    """Ask GitHub which version is on the release line, so cards can say when an update is waiting."""
    while True:
        try:
            with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed https address
                text = response.read(32).decode().strip()
            if _VERSION_RE.match(text):
                fleet.latest_version = text
        except (OSError, ValueError):
            pass  # Offline or blocked. Cards just won't show the note.
        if stop.wait(UPDATE_CHECK_SECONDS):
            return


def make_handler(fleet, token, static_dir=DASHBOARD_DIR, passwords=None, discovery=None):
    """passwords is an auth.Auth. Without one, or before a password is set, the dashboard is open, as before 0.3.0.
    discovery is a discover.Discovery for the Find my Pis button."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "MindfulFleet"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 15

        def log_message(self, *args):
            pass  # Reports arrive constantly. Logging them would wear out SD cards.

        def _send(self, status, body, content_type="application/json", headers=()):
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload, headers=()):
            self._send(status, json.dumps(payload).encode(), headers=headers)

        def _session(self):
            # Read by hand: one odd cookie from another app on this computer mustn't hide ours.
            for header in self.headers.get_all("Cookie") or []:
                for part in header.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == auth.COOKIE and value:
                        return value
            return None

        def _signed_in(self):
            return passwords is None or passwords.session_ok(self._session())

        def _refuse_signed_out(self):
            self._json(401, {"error": "sign in"})

        def _same_site(self):
            """Browsers name the page that sent a POST or PUT. Refuse any that came from another site."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            sent_from = urllib.parse.urlsplit(origin)
            return sent_from.scheme in ("http", "https") and bool(sent_from.netloc) \
                and sent_from.netloc == self.headers.get("Host") and not sent_from.path

        def _read_json(self, limit):
            """Returns the parsed body, or None after sending the refusal."""
            if not self.headers.get("Content-Type", "").startswith("application/json") or not self._same_site():
                self.close_connection = True
                self._json(404, {"error": "not found"})
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.close_connection = True
                self._json(411, {"error": "length required"})
                return None
            if not 0 < length <= limit:
                self.close_connection = True
                self._json(413, {"error": "too large"})
                return None
            try:
                return json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "bad request"})
                return None

        def _cookie(self, value, max_age):
            return ("Set-Cookie", f"{auth.COOKIE}={value}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict")

        def _auth_post(self, path):
            body = self._read_json(MAX_AUTH_BYTES)
            if body is None:
                return
            if not isinstance(body, dict):
                return self._json(400, {"error": "bad request"})
            address = self.client_address[0]
            if path == "/api/sign-out":
                passwords.sign_out(self._session())
                return self._json(200, {"ok": True}, headers=[self._cookie("", 0)])
            if not passwords.has_password():
                return self._json(409, {"error": "No password yet. Set one on the hub: sudo mindful-fleet password"})
            if passwords.unreadable():
                return self._json(503, {"error": "The hub can't read its password file. "
                                                 "On the hub, run: sudo mindful-fleet password"})
            if path == "/api/sign-in":
                session, wait = passwords.sign_in(body.get("password"), address)
                if session:
                    return self._json(200, {"ok": True}, headers=[self._cookie(session, auth.SESSION_SECONDS)])
                if wait:
                    return self._json(429, {"error": "Too many wrong tries.", "wait": wait},
                                      headers=[("Retry-After", str(wait))])
                return self._json(401, {"error": "That password didn't work."})
            if not self._signed_in():
                return self._refuse_signed_out()
            session, problem = passwords.change(body.get("current"), body.get("new"), address)
            if session:
                return self._json(200, {"ok": True}, headers=[self._cookie(session, auth.SESSION_SECONDS)])
            text, wait = problem
            if wait:
                return self._json(429, {"error": text, "wait": wait}, headers=[("Retry-After", str(wait))])
            return self._json(400, {"error": text})

        def _discover(self):
            if not self._signed_in():
                self.close_connection = True
                return self._refuse_signed_out()
            if self._read_json(MAX_AUTH_BYTES) is None:
                return
            results = discovery.run(fleet.known_addresses())
            if results is None:
                return self._json(409, {"error": "Already looking. The results will show in a few seconds."})
            if "error" in results:
                return self._json(503, results)
            self._json(200, results)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/auth":
                return self._json(200, {"password_set": bool(passwords and passwords.has_password()),
                                        "signed_in": self._signed_in()})
            if path.startswith("/api/") and not self._signed_in():
                return self._refuse_signed_out()
            if path == "/api/fleet":
                self._json(200, {**fleet.snapshot(), "password_set": bool(passwords and passwords.has_password())})
            elif path == "/api/history":
                query = urllib.parse.parse_qs(self.path.partition("?")[2])
                pi_id = (query.get("id") or [""])[0]
                found = fleet.history(pi_id) if _ID_RE.match(pi_id) else None
                if found is None:
                    self._json(404, {"error": "unknown computer"})
                else:
                    self._json(200, found)
            elif path == "/api/layout":
                self._json(200, fleet.load_layout())
            elif path == "/healthz":
                self._json(200, {"ok": True, "version": VERSION})
            elif path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                self._send(200, (static_dir / name).read_bytes(), content_type)
            else:
                self._json(404, {"error": "not found"})

        do_HEAD = do_GET

        def do_PUT(self):
            # Browsers can't send this from another site without asking first, and the hub never says yes.
            if self.path != "/api/layout":
                self.close_connection = True
                return self._json(404, {"error": "not found"})
            if not self._signed_in():
                self.close_connection = True
                return self._refuse_signed_out()
            body = self._read_json(MAX_LAYOUT_BYTES)
            if body is None:
                return
            try:
                layout = clean_layout(body)
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            try:
                fleet.save_layout(layout)
            except OSError:
                return self._json(500, {"error": "couldn't save"})
            self._json(200, layout)

        def do_POST(self):
            # Read errors close the connection, since the body may be unread.
            if passwords is not None and self.path in ("/api/sign-in", "/api/sign-out", "/api/password"):
                return self._auth_post(self.path)
            if discovery is not None and self.path == "/api/discover":
                return self._discover()
            if self.path != "/api/report":
                self.close_connection = True
                return self._json(404, {"error": "not found"})
            supplied = self.headers.get("Authorization", "")
            if not token or not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                self.close_connection = True
                return self._json(401, {"error": "bad token"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.close_connection = True
                return self._json(411, {"error": "length required"})
            if not 0 < length <= MAX_BODY_BYTES:
                self.close_connection = True
                return self._json(413, {"error": "report too large"})
            try:
                report = clean_report(json.loads(self.rfile.read(length)))
            except (ValueError, UnicodeDecodeError) as error:
                return self._json(400, {"error": str(error)})
            if not fleet.record(report):
                return self._json(507, {"error": "fleet is full"})
            self._json(200, {"ok": True})

    return Handler


def demo_report(name, temp, *, throttled=0, fan=None, mem=38, disk=31, cpu=None, uptime=864000):
    gib = 1024**3
    return {
        "id": f"{abs(hash(name)) & (2**64 - 1):016x}",
        "hostname": name,
        "ip": f"192.168.1.{10 + sum(map(ord, name)) % 200}",
        "model": "Raspberry Pi 5 Model B Rev 1.0",
        "reporter_version": "0.2.2" if name == "media-pi" else VERSION,
        "temp_c": round(temp + random.uniform(-0.6, 0.6), 1),
        "throttled": throttled,
        "fan_rpm": fan if fan is not None else 2400 + random.randint(-80, 80),
        "fan_percent": None if fan == 0 else 45,
        "input_volts": round(5.08 + random.uniform(-0.02, 0.02), 2),
        "board_watts": round((3.2 + sum(cpu or [20] * 4) / 400 * 5.4) * random.uniform(0.97, 1.03), 2),
        "cpu_per_core": cpu or [round(random.uniform(8, 30), 1) for _ in range(4)],
        "mem_total": (4 if name in ("pi-hole", "old-shed-pi") else 8) * gib * 98 // 100,
        "mem_used": (4 if name in ("pi-hole", "old-shed-pi") else 8) * gib * mem // 100,
        "disk_total": 64 * gib,
        "disk_used": 64 * gib * disk // 100,
        "uptime_s": uptime,
        "cpu_mhz": [2400 if c > 50 else 1500 for c in (cpu or [20] * 4)],
        "cpu_max_mhz": 2400,
        "board_ram_mb": 4096 if name in ("pi-hole", "old-shed-pi") else 8192,
        "load_avg": [round(sum(cpu or [20] * 4) / 100 + random.uniform(-0.1, 0.1), 2), 1.1, 1.0],
        "swap_total": 512 * 1024**2,
        "swap_used": 12 * 1024**2,
        "net_rx_bps": random.randint(2000, 40000),
        "net_tx_bps": random.randint(1000, 9000),
        "core_volts": 0.88,
        "os_name": "Debian GNU/Linux 13 (trixie)",
        "wifi_dbm": None if name in ("garage-pi", "attic-pi") else random.randint(-68, -52),
        "disk_read_bps": random.randint(0, 4000),
        "disk_write_bps": random.randint(2000, 30000),
        **({"hashrate": round(sum(cpu) * 3.1 * random.uniform(0.97, 1.03), 1),
            "hashrate_15m": round(sum(cpu) * 3.1, 1), "mining_threads": sum(1 for c in cpu if c > 50),
            "shares_good": 1840, "shares_total": 1843, "pool": "pool.example.com:20004", "pool_tls": "TLSv1.3",
            "rig_id": "rig01", "algo": "rx/0",
            "miner_version": "6.24.0"} if cpu and sum(cpu) > 250 else {}),
        "sensors": [{"label": "Fan supply", "chip": "ina219", "volts": round(12.02 + random.uniform(-0.03, 0.03), 2),
                     "amps": round(0.41 + random.uniform(-0.02, 0.02), 3), "watts": round(4.93 + random.uniform(-0.2, 0.2), 2)}]
        if name == "attic-pi" else [],
        "top_process": "xmrig" if sum(cpu or [0]) > 250 else "python3",
        "top_process_cpu": round(sum(cpu) - 4, 1) if cpu and sum(cpu) > 250 else round(random.uniform(1, 9), 1),
        "kernel": "6.18.50+rpt-rpi-2712",
    }


def run_demo(fleet, stop):
    """Pretend fleet that shows off each state a card can be in."""
    busy = [100.0, 100.0, 100.0, 100.0]
    cast = (
        ("garage-pi", 52, {}),
        ("pi-hole", 47, {}),
        ("workshop-pi", 76, {"cpu": [100.0, 100.0, 100.0, 2.0], "fan": 0}),
        ("attic-pi", 83, {"cpu": busy, "throttled": 0x60006}),
        ("media-pi", 49, {"disk": 93, "throttled": 0x10000, "fan": 0, "uptime": 7200}),
    )
    fleet.record(demo_report("workshop-pi", 76, cpu=busy))  # Its fan worked once, so 0 RPM now means trouble.
    fleet.record(demo_report("old-shed-pi", 44))
    fleet.age_all(660)  # Only these two exist so far, and workshop-pi reports again below.
    for _ in range(40):
        for name, temp, kwargs in cast:
            fleet.record(demo_report(name, temp, **kwargs))
    now = time.time()
    for minutes_ago in [*range(1440, 60, -5), *range(60, 0, -1)]:
        swing = 4 * math.sin(minutes_ago / 180)  # Warmer in the afternoon, cooler at night.
        for name, temp, kwargs in cast:
            fleet.seed_history(clean_report(demo_report(name, temp + swing, **kwargs)), now - minutes_ago * 60)
    while not stop.wait(5):
        for name, temp, kwargs in cast:
            fleet.record(demo_report(name, temp, **kwargs))


def set_password_here(passwords, parser, ask=getpass.getpass):
    if passwords is None:
        parser.error("set MINDFUL_FLEET_STATE so the hub knows where to keep the password")
    print(f"Choose the dashboard password. At least {auth.MIN_LENGTH} characters; a short sentence works well.")
    print("Nothing shows on screen while you type.")
    for _ in range(3):
        first = ask("New password: ")
        problem = auth.password_problem(first)
        if problem:
            print(problem)
            continue
        if ask("Type it again: ") != first:
            print("Those didn't match. Let's try again.")
            continue
        passwords.set_password(first)
        print("Saved. Anyone signed in to the dashboard has been signed out.")
        return 0
    print("Password not changed.", file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the MindfulFleet hub and dashboard.")
    parser.add_argument("--host", default=os.environ.get("MINDFUL_FLEET_HOST", "0.0.0.0"))  # noqa: S104 - the Pis on the network report to it
    parser.add_argument("--port", type=int, default=int(os.environ.get("MINDFUL_FLEET_PORT", DEFAULT_PORT)))
    parser.add_argument("--state", default=os.environ.get("MINDFUL_FLEET_STATE"),
                        help="file that remembers your Pis between restarts")
    parser.add_argument("--demo", action="store_true", help="show a pretend fleet on this computer only")
    parser.add_argument("--no-update-check", action="store_true",
                        default=os.environ.get("MINDFUL_FLEET_UPDATE_CHECK", "on") == "off",
                        help="don't ask GitHub for the latest version")
    parser.add_argument("--set-password", action="store_true",
                        help="choose the dashboard password (use: sudo mindful-fleet password)")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    token = os.environ.get("MINDFUL_FLEET_TOKEN")
    passwords = auth.Auth(Path(args.state).with_name("password.json")) if args.state and not args.demo else None

    if args.set_password:
        return set_password_here(passwords, parser)

    if args.demo:
        args.host, args.state, token = "127.0.0.1", None, None
    elif not token:
        parser.error("set the MINDFUL_FLEET_TOKEN environment variable, or use --demo")

    fleet = Fleet(args.state)
    stop = threading.Event()
    finder = (lambda known: discover.demo_results()) if args.demo else discover.find_pis
    handler = make_handler(fleet, token, passwords=passwords, discovery=discover.Discovery(finder))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True

    def shut_down(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shut_down)
    signal.signal(signal.SIGINT, shut_down)

    if args.demo:
        fleet.latest_version = VERSION
        threading.Thread(target=run_demo, args=(fleet, stop), daemon=True).start()
    else:
        def keep_saving():
            while not stop.wait(SAVE_EVERY_SECONDS):
                try:
                    fleet.save()
                except OSError as error:
                    print(f"Couldn't save state: {error}", file=sys.stderr, flush=True)
        threading.Thread(target=keep_saving, daemon=True).start()
        if not args.no_update_check:
            threading.Thread(target=check_latest_version, args=(fleet, stop), daemon=True).start()

    print(f"MindfulFleet {VERSION} dashboard: http://{args.host}:{args.port}/", flush=True)
    if passwords and not passwords.has_password():
        print("No dashboard password yet. Set one with: sudo mindful-fleet password", flush=True)
    server.serve_forever()
    try:
        fleet.save()
    except OSError as error:
        print(f"Couldn't save state: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
