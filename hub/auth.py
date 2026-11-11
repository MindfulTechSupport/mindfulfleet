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
FORGET_MISSES_AFTER = 3600  # An address's wrong guesses are forgotten after an hour without one.
FLEET_WIDE_LIMIT = 50  # Wrong guesses from everyone within FLEET_WIDE_WINDOW ...
FLEET_WIDE_WINDOW = 600
FLEET_WIDE_WAIT = 30  # ... before every address that has guessed wrong waits this long between tries.
BUSY_WAIT = 2  # Seconds to come back when another password is being checked.
MAX_ADDRESSES = 1024
MAX_TRUSTED = 64
COOKIE = "mf_session"
BROKEN = object()  # A password file exists but can't be read: refuse everything rather than open up.


def _encodable(text):
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def password_problem(password):
    """Plain words for why a new password won't do, or None when it's fine."""
    if not isinstance(password, str) or not _encodable(password):
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


def valid_record(record):
    try:
        return (record.get("algorithm") == "pbkdf2_sha256" and 1000 <= int(record["iterations"]) <= 10_000_000
                and len(bytes.fromhex(record["salt"])) >= 8 and len(bytes.fromhex(record["hash"])) == 32)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def check_password(password, record):
    if not valid_record(record) or not isinstance(password, str) or len(password) > MAX_LENGTH:
        return False
    if not _encodable(password):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), int(record["iterations"]))
    return hmac.compare_digest(digest, bytes.fromhex(record["hash"]))


def write_record(path, record):
    """Save atomically and durably, readable by the hub's own account only.

    When root runs this (the `mindful-fleet password` command), the new file is handed to the folder's owner
    before it's put in place, so root never changes ownership of a path the hub's account could swap out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        os.fchmod(f.fileno(), 0o600)
        if os.geteuid() == 0:
            folder = os.stat(path.parent)
            os.fchown(f.fileno(), folder.st_uid, folder.st_gid)
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)
    try:
        folder_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)
    except OSError:
        pass  # The file itself is safely written; this only hardens the rename against power loss.


class Guard:
    """Slows down guessing. Each address gets a few free tries, then a wait that doubles each time, up to
    5 minutes; misses are forgotten after an hour. Only one guess per address is checked at a time. If many
    guesses fail overall, every address waits a little between tries, except addresses that have signed in
    before (the owner's own computers), so switching addresses doesn't help a guesser and the owner isn't
    locked out."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._misses = {}  # address -> (count, wait until, last miss)
        self._recent = []  # times of recent misses from anyone
        self._checking = set()  # addresses with a guess being checked right now
        self._trusted = {}  # addresses that have signed in, oldest first

    def _wait_locked(self, address, now):
        count, until, last = self._misses.get(address, (0, 0, 0))
        if count and now - last > FORGET_MISSES_AFTER:
            del self._misses[address]
            return 0
        wait = until - now
        self._recent = [t for t in self._recent if now - t < FLEET_WIDE_WINDOW]
        if address not in self._trusted and len(self._recent) >= FLEET_WIDE_LIMIT:
            wait = max(wait, self._recent[-1] + FLEET_WIDE_WAIT - now)
        return max(0, int(wait + 0.999))

    def wait_seconds(self, address):
        with self._lock:
            return self._wait_locked(address, self._clock())

    def begin(self, address):
        """Claim the right to check one guess. Returns 0 when claimed, or seconds to wait."""
        with self._lock:
            wait = self._wait_locked(address, self._clock())
            if wait:
                return wait
            if address in self._checking:
                return BUSY_WAIT
            self._checking.add(address)
            return 0

    def finish(self, address, good):
        now = self._clock()
        with self._lock:
            self._checking.discard(address)
            if good:
                self._misses.pop(address, None)
                self._trusted.pop(address, None)
                self._trusted[address] = now
                while len(self._trusted) > MAX_TRUSTED:
                    self._trusted.pop(next(iter(self._trusted)))
                return
            if address not in self._misses and len(self._misses) >= MAX_ADDRESSES:
                self._misses.pop(next(iter(self._misses)))
            count = self._misses.get(address, (0, 0, 0))[0] + 1
            wait = 0 if count < FREE_TRIES else min(MAX_WAIT_SECONDS, 2 ** (count - FREE_TRIES + 1))
            self._misses[address] = (count, now + wait, now)
            self._recent.append(now)

    def release(self, address):
        """Give up a claim without counting a guess (the hub was busy)."""
        with self._lock:
            self._checking.discard(address)

    def clear(self):
        with self._lock:
            self._misses.clear()
            self._recent.clear()


class Auth:
    """The hub's password and who's signed in. Sessions live in memory, so a hub restart signs everyone out."""

    def __init__(self, path, clock=time.time, iterations=ITERATIONS):
        self.path = Path(path) if path else None
        self._clock = clock
        self._iterations = iterations
        self._lock = threading.Lock()
        self._hashing = threading.Lock()  # One hash at a time; others are told to come back, not queued.
        self._sessions = {}  # token -> expiry time
        self._seen = None  # (file identity, record) of the last read
        self.guard = Guard(clock)

    def _record(self):
        """None when no password is set, the record when it reads cleanly, BROKEN otherwise."""
        if not self.path:
            return None
        try:
            info = os.stat(self.path)
        except FileNotFoundError:
            self._changed(None)
            return None
        except OSError:
            return BROKEN
        with self._lock:
            if self._seen and self._seen[0] == self._identity(info):
                return self._seen[1]
        try:
            # Identity comes from the file actually read, so a change mid-read can't pair old and new.
            with open(self.path, encoding="utf-8") as f:
                identity = self._identity(os.fstat(f.fileno()))
                record = json.loads(f.read())
        except (OSError, ValueError):
            return BROKEN
        if not valid_record(record):
            return BROKEN
        self._changed(identity, record)
        return record

    @staticmethod
    def _identity(info):
        return info.st_ino, info.st_mtime_ns, info.st_size

    def _changed(self, identity, record=None):
        """Any change to the password file, including from the hub's command, signs everyone out."""
        with self._lock:
            if self._seen is not None and self._seen[0] == identity:
                return
            if self._seen is not None:
                self._sessions.clear()
                self.guard.clear()
            self._seen = (identity, record)

    def has_password(self):
        return self._record() is not None

    def unreadable(self):
        return self._record() is BROKEN

    def session_ok(self, token):
        record = self._record()
        if record is None:
            return True  # No password set: the dashboard is open, as before 0.3.0.
        if record is BROKEN or not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if expires < self._clock():
                del self._sessions[token]
                return False
            return True

    def _new_session(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._sessions = {t: e for t, e in self._sessions.items() if e > now}
            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(min(self._sessions, key=self._sessions.get))
            self._sessions[token] = now + SESSION_SECONDS
        return token

    def _check(self, password, record, address):
        """Returns True or False, or a number of seconds to wait before trying."""
        wait = self.guard.begin(address)
        if wait:
            return wait
        if not self._hashing.acquire(blocking=False):
            self.guard.release(address)
            return BUSY_WAIT
        try:
            good = check_password(password, record)
        finally:
            self._hashing.release()
        self.guard.finish(address, good)
        return good

    def sign_in(self, password, address):
        """Returns (token, None) on success, or (None, seconds to wait) on failure."""
        record = self._record()
        if record is None or record is BROKEN:
            return None, 0
        result = self._check(password, record, address)
        if result is True:
            return self._new_session(), None
        return None, result or self.guard.wait_seconds(address)

    def sign_out(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def change(self, current, new, address):
        """Returns (token for this browser, None) or (None, (problem text, seconds to wait))."""
        record = self._record()
        if record is None:
            return None, ("Set the first password on the hub itself: sudo mindful-fleet password", 0)
        if record is BROKEN:
            return None, ("The hub can't read its password file. Run sudo mindful-fleet password on the hub.", 0)
        problem = password_problem(new)
        if problem:
            return None, (problem, 0)
        result = self._check(current, record, address)
        if result is not True:
            if result is False:
                return None, ("That isn't the current password.", self.guard.wait_seconds(address))
            return None, ("Too many tries at once. Wait a moment and try again.", result)
        new_record = hash_password(new, self._iterations)
        write_record(self.path, new_record)
        self._record()  # Notices the new file, which signs out every other browser.
        return self._new_session(), None

    def set_password(self, new):
        """For the hub's own command. The running hub notices the new file and signs everyone out."""
        problem = password_problem(new)
        if problem:
            raise ValueError(problem)
        write_record(self.path, hash_password(new, self._iterations))
