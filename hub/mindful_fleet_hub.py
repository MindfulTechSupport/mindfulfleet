#!/usr/bin/env python3
"""MindfulFleet hub: receives reports and serves the dashboard. Standard library only."""
import argparse
import csv
import datetime
import getpass
import hmac
import io
import ipaddress
import json
import math
import os
import random
import re
import signal
import sqlite3
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
import history_store  # noqa: E402
import plan as plans  # noqa: E402
import xlsx  # noqa: E402
from averages import History  # noqa: E402

VERSION = "0.2.17"
DEFAULT_PORT = 8742
MAX_BODY_BYTES = 16 * 1024
MAX_PIS = 256
HISTORY_SAMPLES = 120  # 30 minutes at the default 15-second interval.
SAVE_EVERY_SECONDS = 300  # Every 5 minutes, to spare SD cards.
UPDATE_CHECK_SECONDS = 6 * 3600
RELEASE_VERSION_URL = "https://raw.githubusercontent.com/MindfulTechSupport/mindfulfleet/release/VERSION"
MAX_LAYOUT_BYTES = 16 * 1024
MAX_AUTH_BYTES = 1024
MAX_HISTORY_BYTES = 4 * 1024
MAX_IMPORT_BYTES = 2 * 1024 * 1024  # A saved 24 hours for a big fleet is a few hundred KB.
EXPORT_EVERY = (0, 3600, 86400)  # Each snapshot, hourly averages, daily averages.
NEEDS_PLUS = "History is part of MindfulFleet Plus."
NEEDS_PRO = "Exporting History is part of MindfulFleet Pro."
HISTORY_READINGS = ("temp", "hashrate", "watts", "cpu", "fan", "memory", "clock", "sensor_watts")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/palettes.js": ("palettes.js", "text/javascript; charset=utf-8"),
    "/region.js": ("region.js", "text/javascript; charset=utf-8"),
}
# Region and units (Settings): the same choices as hub/dashboard/region.js. The first one is the default.
REGION_CHOICES = {
    "units": ("metric", "imperial"),
    "dates": ("auto", "mdy", "dmy", "ymd"),
    "clock": ("auto", "12", "24"),
    "sizes": ("binary", "decimal"),
    "speed": ("bytes", "bits"),
}
REGION_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "JPY",
                     "CNY", "INR", "BRL", "MXN", "ZAR", "KRW", "SGD", "HKD", "PHP", "ILS", "TRY")
_TIMEZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$")
# Host allowlist (A-055): names that can only point at the owner's own network. A hostile web page can make a
# public name point at the hub (DNS rebinding), but it can't use these, an IP address or a one-word name.
LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localhost", ".ts.net")
HOST_REFUSED = ("This hub only answers to its own address, such as its IP address or its name.local. To open it by "
                "another name, add that name to MINDFUL_FLEET_ALLOWED_HOSTS in /etc/mindful-fleet/hub.env "
                "(names separated by commas; .example.com allows every name under it) and restart the hub.\n")


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def host_allowed(header, extra=()):
    """A-055: answer browsers only at an address that can't belong to another website."""
    if not header:
        return True  # Every browser sends Host; a request without one didn't come from a web page.
    host = header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return end > 1 and _is_ip(host[1:end])
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.rstrip(".")
    if not name:
        return False
    if _is_ip(name) or name == "localhost" or "." not in name or name.endswith(LOCAL_SUFFIXES):
        return True
    # An entry starting with a dot, such as .trycloudflare.com for a tunnel, allows every name under it.
    return name in extra or any(entry.startswith(".") and name.endswith(entry) for entry in extra)


def allowed_hosts_setting(text):
    """MINDFUL_FLEET_ALLOWED_HOSTS: extra names the owner uses for the hub, separated by commas."""
    return tuple(part.strip().lower().rstrip(".") for part in (text or "").split(",") if part.strip())
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


def _ups(value):
    """UPS through NUT (#58). A malformed one is dropped rather than refusing the whole report."""
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str) or not re.fullmatch(r"[A-Z ]{1,40}", status.strip()):
        return None
    try:
        return {"name": _text(value.get("name"), 40) or "UPS", "status": status.strip(),
                "charge": _number(value.get("charge"), 0, 100), "runtime_s": _number(value.get("runtime_s"), 0, 10**7),
                "load": _number(value.get("load"), 0, 1000)}
    except BadReport:
        return None


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
        "page_kb": _number(raw.get("page_kb"), 0, 1 << 20),
        "huge_pages": _number(raw.get("huge_pages"), 0, 1 << 30),
        "thp": raw.get("thp") if raw.get("thp") in ("always", "madvise", "never") else None,
        "ups": _ups(raw.get("ups")),
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

    def names(self):
        """{Pi id: hostname} for every Pi on the dashboard."""
        with self._lock:
            return {pi_id: p["report"]["hostname"] for pi_id, p in self._pis.items()}

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

    def settle_region(self):
        """D-090: a hub upgraded from an older version keeps °C until its owner changes it. Only a brand-new hub
        (no computers yet) lets the first browser match its own country. Runs once at start-up."""
        layout = self.load_layout()
        if not isinstance(layout, dict) or layout.get("region") or not self._pis:
            return
        try:
            settled = clean_layout(layout)
        except BadReport:
            settled = clean_layout({})
        settled["region"] = clean_region({})
        self.save_layout(settled)

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
    return {"order": ids, "groups": groups, "members": members, "hidden": hidden, "price": price,
            "region": clean_region(raw.get("region"))}


def clean_region(raw):
    """Region and units: known choices only, so a bad value can't reach another browser. None means not set yet."""
    if not isinstance(raw, dict):
        return None
    region = {key: raw.get(key) if raw.get(key) in choices else choices[0] for key, choices in REGION_CHOICES.items()}
    country = raw.get("country")
    region["country"] = country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else ""
    currency = raw.get("currency")
    region["currency"] = currency if currency in REGION_CURRENCIES else "USD"
    zone = raw.get("timezone")
    region["timezone"] = zone if isinstance(zone, str) and len(zone) <= 40 and _TIMEZONE_RE.match(zone) else "auto"
    return region


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


def _whole(value, low, high):
    """A whole number from a query string or JSON, inside the range, or None."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, bool) or not math.isfinite(number) or not low <= number <= high:
        return None
    return int(number)


def _ids(text):
    ids = [part for part in str(text or "").split(",") if part][:MAX_PIS]
    return ids if ids and all(_ID_RE.match(part) for part in ids) else None


def _local_text(seconds):
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def make_handler(fleet, token, static_dir=DASHBOARD_DIR, passwords=None, discovery=None, history=None, plan=None,
                 allowed_hosts=()):
    """passwords is an auth.Auth. Without one, or before a password is set, the dashboard is open, as before 0.3.0.
    discovery is a discover.Discovery for the Find my Pis button. history is a history_store.HistoryStore and
    plan a plan.Plan: History needs Plus, and exporting it needs Pro."""
    plan = plan or plans.Plan(fixed="free")
    exporting = threading.Semaphore(1)  # One export at a time: each can take a minute and a lot of space on a Pi.
    class Handler(BaseHTTPRequestHandler):
        server_version = "MindfulFleet"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 15

        def log_message(self, *args):
            pass  # Reports arrive constantly. Logging them would wear out SD cards.

        def _host_ok(self):
            """A-055: the Pis' reports carry the fleet token, so they're checked by that instead of their address."""
            path = self.path.split("?", 1)[0]
            if path in ("/api/report", "/healthz") or host_allowed(self.headers.get("Host"), allowed_hosts):
                return True
            self.close_connection = True
            self._send(403, HOST_REFUSED.encode(), "text/plain; charset=utf-8")
            return False

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
            if passwords is not None and not passwords.has_password():
                # Without a password anyone on the network, or a web page they open, could ask for this list.
                return self._json(403, {"error": "Give the dashboard a password first. On the hub, run: "
                                                 "sudo mindful-fleet password"})
            try:
                results = discovery.run(fleet.known_addresses())
            except Exception as error:  # noqa: BLE001 - always answer the browser
                print(f"Find my Pis failed: {error!r}", file=sys.stderr, flush=True)
                return self._json(500, {"error": "The hub couldn't look just now. Try again in a minute."})
            if results is None:
                return self._json(409, {"error": "Already looking. Press Look now again in a few seconds."})
            if "error" in results:
                return self._json(503, results)
            self._json(200, results)

        def _history_status(self):
            if history is None:
                return None
            return {"settings": history.settings(), "used_bytes": history.used_bytes(), "paused": history.paused,
                    "full_days": history_store.FULL_DAYS, "rolled_days": history_store.ROLLUP_DAYS,
                    "intervals": list(history_store.INTERVALS), "min_interval": history_store.MIN_INTERVAL,
                    "max_interval": history_store.MAX_INTERVAL}

        def _history_refusal(self, needs_pro=False):
            """Sends the refusal and returns True when History can't be used right now."""
            if history is None:
                self._json(404, {"error": "History isn't available on this hub."})
                return True
            current = plan.current()
            if not current["plus"] or (needs_pro and not current["pro"]):
                self._json(403, {"error": NEEDS_PRO if needs_pro and current["plus"] else NEEDS_PLUS, "plan": current})
                return True
            return False

        def _history_get(self, path, query):
            def one(name):
                return (query.get(name) or [""])[0]
            now = time.time()
            start = _whole(one("from"), 0, now + 86400)
            end = _whole(one("to"), 0, now + 86400)
            if path == "/api/history/range":
                ids = _ids(one("ids"))
                names = [name for name in one("readings").split(",") if name in history_store.READINGS]
                points = _whole(one("points") or 600, 10, history_store.MAX_POINTS)
                if not ids or not names or start is None or end is None or end <= start or points is None:
                    return self._json(400, {"error": "bad request"})
                if self._history_refusal():
                    return
                return self._json(200, {"series": history.series(ids, start, end, names, points)})
            if path == "/api/history/markers":
                if start is None or end is None or end <= start:
                    return self._json(400, {"error": "bad request"})
                if self._history_refusal():
                    return
                return self._json(200, {"markers": history.markers(start, end)})
            if path == "/api/history/compare":
                pi_id = one("id")
                when = _whole(one("t"), 0, now + 86400)
                days = _whole(one("days"), 1, 90)
                if not _ID_RE.match(pi_id) or when is None or days is None:
                    return self._json(400, {"error": "bad request"})
                if self._history_refusal():
                    return
                found = history.compare(pi_id, when, days)
                return self._json(200, found) if found else self._json(404, {"error": "No history for that Pi yet."})
            if path == "/api/history/export":
                ids = _ids(one("ids"))
                every = _whole(one("every") or 0, 0, 86400)
                kind = one("format")
                if not ids or start is None or end is None or end <= start or every not in EXPORT_EVERY \
                        or kind not in ("csv", "xlsx"):
                    return self._json(400, {"error": "bad request"})
                if self._history_refusal(needs_pro=True):
                    return
                if self.command == "HEAD":
                    return self._json(405, {"error": "Use GET to download."})
                if passwords is not None and not passwords.has_password():
                    # Without a password, any web page the owner visits could start big exports.
                    return self._json(403, {"error": "Give the dashboard a password first. On the hub, run: "
                                                     "sudo mindful-fleet password"})
                if every:
                    start = history_store.bucket_start(start, every)  # Hours start on the hour, days at midnight.
                return self._export(ids, start, end, every, one("lows") == "1", kind)
            self._json(404, {"error": "not found"})

        def _export(self, ids, start, end, every, lows, kind):
            if not exporting.acquire(blocking=False):
                return self._json(429, {"error": "Another export is being made. Try again in a minute."})
            try:
                return self._export_now(ids, start, end, every, lows, kind)
            finally:
                exporting.release()

        def _export_now(self, ids, start, end, every, lows, kind):
            try:
                heading, kept, sheets = history.export_plan(ids, start, end, every, lows)
            except ValueError as error:
                return self._json(413, {"error": str(error)})
            if not sheets:
                return self._json(404, {"error": "No history for those Pis in that time."})
            if history.disk_low():
                return self._json(507, {"error": history.paused})
            rows = [(name, history.export_rows(n, start, end, kept, every, lows)) for name, n in sheets]
            stamp = datetime.date.today().isoformat()
            # Big files go next to History on the hub's disk, which the free-space guard watches.
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, dir=history.folder()) as out:
                if kind == "csv":
                    text = io.TextIOWrapper(out, encoding="utf-8-sig", newline="")
                    writer = csv.writer(text)
                    writer.writerow(["Pi", *heading])
                    for name, lines in rows:
                        for row in lines:
                            writer.writerow([name, _local_text(row[0]), *("" if v is None else v for v in row[1:])])
                    text.flush()
                    text.detach()
                    content_type = "text/csv; charset=utf-8"
                else:
                    xlsx.write(out, heading, rows, history_store.utc_offset)
                    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                size = out.tell()
                out.seek(0)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="MindfulFleet history {stamp}.{kind}"')
                self.send_header("Cache-Control", "no-store")
                for name, value in SECURITY_HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    while chunk := out.read(64 * 1024):
                        self.wfile.write(chunk)

        def _history_put(self):
            body = self._read_json(MAX_HISTORY_BYTES)
            if body is None:
                return
            if self._history_refusal():
                return
            ids = body.get("ids") if isinstance(body, dict) else None
            if not isinstance(ids, list) or not ids or len(ids) > MAX_PIS or not all(isinstance(i, str) and _ID_RE.match(i) for i in ids):
                return self._json(400, {"error": "bad request"})
            enabled = body.get("enabled")
            every = body.get("every")
            if enabled is not None and not isinstance(enabled, bool):
                return self._json(400, {"error": "bad request"})
            names = fleet.names()
            try:
                for pi_id in ids:
                    if pi_id not in names:
                        return self._json(404, {"error": "unknown computer"})
                    history.set(pi_id, names[pi_id], enabled=enabled, every=every)
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            self._json(200, {"settings": history.settings()})

        def _history_post(self, path):
            limit = MAX_IMPORT_BYTES if path == "/api/history/import" else MAX_HISTORY_BYTES
            body = self._read_json(limit)
            if body is None:
                return
            if self._history_refusal():
                return
            if not isinstance(body, dict):
                return self._json(400, {"error": "bad request"})
            names = fleet.names()
            if path == "/api/history/import" and passwords is not None and not passwords.has_password():
                return self._json(403, {"error": "Give the dashboard a password first. On the hub, run: "
                                                 "sudo mindful-fleet password"})
            try:
                if path == "/api/history/import":
                    known = {name: pi_id for pi_id, name in names.items()}
                    return self._json(200, {"added": history.import_saved(body, known)})
                if "delete" in body:
                    marker = _whole(body.get("delete"), 1, 2**62)
                    if marker is None:
                        return self._json(400, {"error": "bad request"})
                    return self._json(200, {"deleted": history.delete_marker(marker)})
                pi_id = body.get("pi") or None
                if pi_id is not None and (not isinstance(pi_id, str) or pi_id not in names):
                    return self._json(404, {"error": "unknown computer"})
                when = _whole(body.get("t"), 0, time.time() + 366 * 86400)
                if when is None:
                    return self._json(400, {"error": "The marker needs a time."})
                return self._json(200, history.add_marker(pi_id, names.get(pi_id), when, body.get("text")))
            except ValueError as error:
                return self._json(400, {"error": str(error)})

        def do_GET(self):
            if not self._host_ok():
                return None
            path = self.path.split("?", 1)[0]
            if path == "/api/auth":
                return self._json(200, {"password_set": bool(passwords and passwords.has_password()),
                                        "signed_in": self._signed_in()})
            if path.startswith("/api/") and not self._signed_in():
                return self._refuse_signed_out()
            if path == "/api/fleet":
                self._json(200, {**fleet.snapshot(), "password_set": bool(passwords and passwords.has_password()),
                                 "plan": plan.current(), "history": self._history_status()})
            elif path.startswith("/api/history/"):
                self._history_get(path, urllib.parse.parse_qs(self.path.partition("?")[2]))
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
            if not self._host_ok():
                return None
            # Browsers can't send this from another site without asking first, and the hub never says yes.
            if self.path == "/api/history/settings":
                if not self._signed_in():
                    self.close_connection = True
                    return self._refuse_signed_out()
                return self._history_put()
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
            if not self._host_ok():
                return None
            # Read errors close the connection, since the body may be unread.
            if passwords is not None and self.path in ("/api/sign-in", "/api/sign-out", "/api/password"):
                return self._auth_post(self.path)
            if discovery is not None and self.path == "/api/discover":
                return self._discover()
            if self.path in ("/api/history/markers", "/api/history/import"):
                if not self._signed_in():
                    self.close_connection = True
                    return self._refuse_signed_out()
                return self._history_post(self.path)
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
            if history is not None and plan.current()["plus"]:
                history.add(report)
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
        "page_kb": 16, "huge_pages": 0, "thp": "madvise",
        "ups": {"name": "tripplite", "status": "OL CHRG", "charge": 100, "runtime_s": 2400, "load": 13}
        if name == "attic-pi" else None,
    }


def seed_demo_history(fleet, history, now):
    """A pretend week of History on two Pis, with an ice cooler fitted on one of them 3 days ago."""
    names = {name: pi_id for pi_id, name in fleet.names().items()}
    cooler = now - 3 * 86400
    for name in ("garage-pi", "attic-pi"):
        history.set(names[name], name, enabled=True, every=300)
    for name, temp in (("garage-pi", 52), ("attic-pi", 70)):
        report = clean_report(demo_report(name, temp))
        for step in range(7 * 288, 0, -1):
            when = now - step * 300
            swing = 4 * math.sin(when / 86400 * 2 * math.pi)  # Warmer in the afternoon, cooler at night.
            cooled = -9 if name == "attic-pi" and when >= cooler else 0
            history.add({**report, "temp_c": temp + swing + cooled + random.uniform(-0.5, 0.5),
                         "hashrate": (1080 if cooled else 930) + random.uniform(-20, 20)}, when)
            history.add({**report, "temp_c": temp + swing + cooled + random.uniform(-0.5, 0.5),
                         "hashrate": (1080 if cooled else 930) + random.uniform(-20, 20)}, when + 150)
    history.tick(now + 3600)
    history.add_marker(names["attic-pi"], "attic-pi", cooler, "Ice cooler installed")


def run_demo(fleet, stop, history=None):
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
    if history is not None:
        seed_demo_history(fleet, history, now)
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
    if not args.demo:
        try:
            fleet.settle_region()
        except OSError as error:
            print(f"Couldn't save the units setting: {error}", file=sys.stderr, flush=True)
    stop = threading.Event()
    finder = (lambda known: discover.demo_results()) if args.demo else discover.find_pis
    if args.demo:
        history, plan = history_store.HistoryStore(None), plans.Plan(fixed="demo")
    else:
        where = Path(args.state).with_name("history.db") if args.state else None
        plan = plans.Plan()
        try:
            history = history_store.HistoryStore(where)
        except (sqlite3.Error, OSError) as error:
            # The dashboard matters more than History: start without it and say why.
            print(f"History is off: couldn't open {where} ({error}). Move that file aside and restart the hub "
                  "to start History afresh.", file=sys.stderr, flush=True)
            history = None
    allowed = allowed_hosts_setting(os.environ.get("MINDFUL_FLEET_ALLOWED_HOSTS"))
    handler = make_handler(fleet, token, passwords=passwords, discovery=discover.Discovery(finder), allowed_hosts=allowed,
                           history=history, plan=plan)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True

    def shut_down(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shut_down)
    signal.signal(signal.SIGINT, shut_down)

    if args.demo:
        fleet.latest_version = VERSION
        threading.Thread(target=run_demo, args=(fleet, stop, history), daemon=True).start()
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

    def keep_history():
        while not stop.wait(history_store.FLUSH_SECONDS):
            try:
                history.tick()
            except Exception as error:  # noqa: BLE001 - History must keep saving after any one bad minute
                print(f"Couldn't save History: {error!r}", file=sys.stderr, flush=True)
    if history is not None:
        threading.Thread(target=keep_history, daemon=True).start()

    print(f"MindfulFleet {VERSION} dashboard: http://{args.host}:{args.port}/", flush=True)
    if passwords and not passwords.has_password():
        print("No dashboard password yet. Set one with: sudo mindful-fleet password", flush=True)
    server.serve_forever()
    try:
        fleet.save()
    except OSError as error:
        print(f"Couldn't save state: {error}", file=sys.stderr)
    try:
        if history is not None:
            history.flush_now()
    except (OSError, sqlite3.Error) as error:
        print(f"Couldn't save History: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
