"""Dashboard password: a salted hash on the hub, sign-in sessions, and a brake on guessing.

Standard library only. The password itself is never stored, only a PBKDF2-SHA256 hash with a random salt.
"""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

ITERATIONS = 600_000  # OWASP's figure for PBKDF2-SHA256. About a second on a busy Pi, and sign-ins are rare.
MIN_LENGTH = 10
MAX_LENGTH = 200
SESSION_SECONDS = 30 * 24 * 3600
MAX_SESSIONS = 64
FREE_TRIES = 5  # Wrong guesses allowed from one address before it has to wait.
MAX_WAIT_SECONDS = 300
FLEET_WIDE_LIMIT = 50  # Wrong guesses from everyone within FLEET_WIDE_WINDOW before everyone waits.
FLEET_WIDE_WINDOW = 600
FLEET_WIDE_WAIT = 30
MAX_ADDRESSES = 1024
COOKIE = "mf_session"


def password_problem(password):
    """Plain words for why a new password won't do, or None when it's fine."""
    if not isinstance(password, str):
        return "Type a password."
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters. A short sentence you'll remember works well."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if password.strip() != password:
        return "Leave out spaces at the start and end."
    return None


def hash_password(password, iterations=ITERATIONS, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return {"algorithm": "pbkdf2_sha256", "iterations": iterations, "salt": salt.hex(), "hash": digest.hex()}


def check_password(password, record):
    try:
        if record.get("algorithm") != "pbkdf2_sha256" or not isinstance(password, str):
            return False
        salt, expected = bytes.fromhex(record["salt"]), bytes.fromhex(record["hash"])
        iterations = int(record["iterations"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    if len(password) > MAX_LENGTH or not 1000 <= iterations <= 10_000_000:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(digest, expected)


def write_record(path, record):
    """Save atomically, readable by the hub's own account only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        os.chmod(f.name, 0o600)
        json.dump(record, f)
    os.replace(f.name, path)


class Guard:
    """Slows down guessing: a few free tries per address, then a wait that doubles each time, up to 5 minutes.
    If many guesses fail across all addresses, everyone waits a little, so changing addresses doesn't help."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._misses = {}  # address -> (count, wait until)
        self._recent = []  # times of recent misses from anyone

    def wait_seconds(self, address):
        now = self._clock()
        with self._lock:
            self._recent = [t for t in self._recent if now - t < FLEET_WIDE_WINDOW]
            wait = 0.0
            if len(self._recent) >= FLEET_WIDE_LIMIT:
                wait = max(wait, self._recent[-1] + FLEET_WIDE_WAIT - now)
            _, until = self._misses.get(address, (0, 0))
            return max(0, round(max(wait, until - now)))

    def missed(self, address):
        now = self._clock()
        with self._lock:
            if address not in self._misses and len(self._misses) >= MAX_ADDRESSES:
                self._misses.pop(next(iter(self._misses)))
            count = self._misses.get(address, (0, 0))[0] + 1
            wait = 0 if count < FREE_TRIES else min(MAX_WAIT_SECONDS, 2 ** (count - FREE_TRIES + 1))
            self._misses[address] = (count, now + wait)
            self._recent.append(now)

    def passed(self, address):
        with self._lock:
            self._misses.pop(address, None)


class Auth:
    """The hub's password and who's signed in. Sessions live in memory, so a hub restart signs everyone out."""

    def __init__(self, path, clock=time.time, iterations=ITERATIONS):
        self.path = Path(path) if path else None
        self._clock = clock
        self._iterations = iterations
        self._lock = threading.Lock()
        self._hashing = threading.Lock()  # One hash at a time, so a flood of sign-ins can't hog the CPU.
        self._sessions = {}  # token -> (expires, password hash it was made with)
        self.guard = Guard(clock)

    def _record(self):
        if not self.path:
            return None
        try:
            record = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) and record.get("hash") else None

    def has_password(self):
        return self._record() is not None

    def session_ok(self, token):
        record = self._record()
        if record is None:
            return True  # No password set: the dashboard is open, as before 0.3.0.
        if not token:
            return False
        with self._lock:
            found = self._sessions.get(token)
            if not found:
                return False
            expires, stamp = found
            # A new password (from the dashboard or the hub's command) signs out every old session.
            if expires < self._clock() or not hmac.compare_digest(stamp, record["hash"]):
                del self._sessions[token]
                return False
            return True

    def _new_session(self, record):
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._sessions = {t: s for t, s in self._sessions.items() if s[0] > now}
            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(min(self._sessions, key=lambda t: self._sessions[t][0]))
            self._sessions[token] = (now + SESSION_SECONDS, record["hash"])
        return token

    def sign_in(self, password, address):
        """Returns (token, None) on success, or (None, seconds to wait) on failure."""
        wait = self.guard.wait_seconds(address)
        if wait:
            return None, wait
        record = self._record()
        if record is None:
            return None, 0
        with self._hashing:
            good = check_password(password, record)
        if not good:
            self.guard.missed(address)
            return None, self.guard.wait_seconds(address)
        self.guard.passed(address)
        return self._new_session(record), None

    def sign_out(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def change(self, current, new, address):
        """Returns (token for this browser, None) or (None, (problem text, seconds to wait))."""
        wait = self.guard.wait_seconds(address)
        if wait:
            return None, ("Too many wrong tries. Wait a moment and try again.", wait)
        record = self._record()
        if record is None:
            return None, ("Set the first password on the hub itself: sudo mindful-fleet password", 0)
        with self._hashing:
            good = check_password(current, record)
        if not good:
            self.guard.missed(address)
            return None, ("That isn't the current password.", self.guard.wait_seconds(address))
        problem = password_problem(new)
        if problem:
            return None, (problem, 0)
        self.guard.passed(address)
        with self._hashing:
            record = hash_password(new, self._iterations)
        write_record(self.path, record)
        with self._lock:
            self._sessions.clear()
        return self._new_session(record), None

    def set_password(self, new):
        """For the hub's own command. Signs everyone out."""
        problem = password_problem(new)
        if problem:
            raise ValueError(problem)
        write_record(self.path, hash_password(new, self._iterations))
        with self._lock:
            self._sessions.clear()
