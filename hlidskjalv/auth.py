"""Who may change things: sessions, pairing codes and the password.

Everything Hlidskjalv shows is readable by any local process (it binds
loopback, like any dev server). Anything that *changes* something (settings,
the terminal, acknowledging alerts) needs a session token, which a browser
gets by pairing and keeps until the session runs out:

- A session lasts as long as it is used; after `security.session_minutes`
  without a request it ends. Tokens are stored hashed in
  ~/.hlidskjalv/web-sessions.json (owner-only) so they survive restarts.
- Pairing, two ways: a password set with `python -m hlidskjalv password`
  (stored as a salted scrypt hash in ~/.hlidskjalv/password, owner-only;
  five wrong tries in ten minutes lock it for a minute), or a one-time code
  that expires after PAIR_TTL_S. Codes come from the link Hlidskjalv opens at
  launch, or from `python -m hlidskjalv pair`, which writes one to
  ~/.hlidskjalv/pair (owner-only), so only your own user can pair a browser.
  Launch links are visible to other users in /proc/<pid>/cmdline, which is
  why they must stop working once used.
- The owner key in ~/.hlidskjalv/owner.key never leaves the machine's files:
  sessions are bound to it, so `pair --rotate` ends every session at once,
  without a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

PAIR_TTL_S = 600
SESSION_TTL_S = 4 * 3600  # default; Settings (security.session_minutes) override it
PASSWORD_MIN = 8
PASSWORD_TRIES = 5  # wrong passwords within PASSWORD_WINDOW_S ...
PASSWORD_WINDOW_S = 600
PASSWORD_LOCK_S = 60  # ... lock pairing by password this long


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, atomically."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _same(a, b: str | None) -> bool:
    return isinstance(a, str) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password_hash(stored: str, password: str) -> bool:
    try:
        kind, salt, h = stored.strip().split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


class Owner:
    def __init__(self, home: Path):
        self.home = home
        self.key_file = home / "owner.key"
        self.pair_file = home / "pair"
        self.password_file = home / "password"
        self.sessions_file = home / "web-sessions.json"
        self.ttl_provider = None  # callable -> seconds; the server wires Settings in
        self._lock = threading.RLock()  # redeem() starts a session while holding it
        self._launch: tuple[str, float] | None = None
        self._stamp: tuple | None = None
        self._key = ""
        self._sessions: dict[str, dict] = {}
        self._sessions_key = ""  # digest of the key the sessions belong to
        self._saved = 0.0
        self._fails: list[float] = []
        if len(self.key) < 40:
            write_private(self.key_file, new_token())
        self._load_sessions()

    # ------------------------------------------------------------- the key
    @property
    def key(self) -> str:
        """Re-read when the file changes, so `pair --rotate` applies without a restart."""
        try:
            st = self.key_file.stat()
            # mtime alone is not enough: Windows stamps files with the ~1-15 ms system tick, so a
            # rotation right after the previous write can look unchanged. write_private replaces
            # the file, which gives it a new inode / file id, so that is compared too.
            stamp = (st.st_mtime_ns, st.st_ino, st.st_size)
            if stamp != self._stamp:
                self._key, self._stamp = self.key_file.read_text(encoding="utf-8").strip(), stamp
        except OSError:
            self._key, self._stamp = "", None
        return self._key

    # ------------------------------------------------------------- sessions
    @property
    def ttl_s(self) -> float:
        try:
            return float(self.ttl_provider()) if self.ttl_provider else SESSION_TTL_S
        except Exception:
            return SESSION_TTL_S

    def _load_sessions(self) -> None:
        try:
            data = json.loads(self.sessions_file.read_text(encoding="utf-8"))
            self._sessions = {k: v for k, v in data.get("sessions", {}).items()
                              if isinstance(k, str) and isinstance(v, dict) and "seen" in v and "created" in v}
            self._sessions_key = str(data.get("key", ""))
        except (OSError, ValueError, AttributeError):
            self._sessions, self._sessions_key = {}, ""

    def _save_sessions(self) -> None:
        try:
            write_private(self.sessions_file, json.dumps({"key": self._sessions_key, "sessions": self._sessions}))
        except OSError:
            pass
        self._saved = time.time()

    def _prune(self, now: float) -> None:
        """Drop expired sessions, and every session if the key was rotated (by any process)."""
        current = _digest(self.key)[:16]
        if current != self._sessions_key:
            self._sessions, self._sessions_key = {}, current
        ttl = self.ttl_s
        for k in [k for k, s in self._sessions.items() if now - float(s.get("seen", 0)) > ttl]:
            del self._sessions[k]

    def start_session(self) -> str:
        token = new_token()
        now = time.time()
        with self._lock:
            self._prune(now)
            self._sessions[_digest(token)] = {"created": now, "seen": now}
            self._save_sessions()
        return token

    def check(self, given) -> bool:
        """True for a live session token; using it keeps the session alive."""
        if not isinstance(given, str) or not 20 <= len(given) <= 100:
            return False
        now = time.time()
        with self._lock:
            self._prune(now)
            s = self._sessions.get(_digest(given))
            if s is None:
                return False
            s["seen"] = now
            if now - self._saved > 30:  # the file only needs to be roughly right
                self._save_sessions()
            return True

    def session_info(self, given) -> dict | None:
        if not self.check(given):
            return None
        s = self._sessions[_digest(given)]
        return {"expires_in": int(self.ttl_s - (time.time() - s["seen"])), "since": s["created"]}

    def end_session(self, given) -> None:
        if not isinstance(given, str):
            return
        with self._lock:
            if self._sessions.pop(_digest(given), None) is not None:
                self._save_sessions()

    def end_all(self) -> None:
        with self._lock:
            self._sessions = {}
            self._save_sessions()

    # ------------------------------------------------------------- pairing
    def launch_code(self) -> str:
        """A one-time code for the link Hlidskjalv opens itself."""
        code = new_token()
        with self._lock:
            self._launch = (code, time.time())
        return code

    def redeem(self, code) -> str | None:
        """Trade a pairing code for a session token. Each code works once."""
        if not isinstance(code, str) or len(code) > 100:
            return None
        with self._lock:
            if self._launch and _same(code, self._launch[0]):
                fresh = time.time() - self._launch[1] < PAIR_TTL_S
                self._launch = None
                return self.start_session() if fresh else None
            try:
                st = self.pair_file.stat()
                stored = self.pair_file.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not _same(code, stored):
                return None
            try:
                self.pair_file.unlink()
            except OSError:
                pass
            return self.start_session() if time.time() - st.st_mtime < PAIR_TTL_S else None

    # ------------------------------------------------------------- password
    def has_password(self) -> bool:
        return self.password_file.exists()

    def set_password(self, password: str | None) -> None:
        """Set (at least PASSWORD_MIN characters) or, with None, remove the password."""
        if not password:
            try:
                self.password_file.unlink()
            except OSError:
                pass
            return
        if not isinstance(password, str) or len(password) < PASSWORD_MIN:
            raise ValueError(f"the password needs at least {PASSWORD_MIN} characters")
        write_private(self.password_file, hash_password(password))

    def password_lock_s(self) -> int:
        """Seconds until a password may be tried again; 0 when it may be tried now."""
        now = time.time()
        with self._lock:
            self._fails = [t for t in self._fails if now - t < PASSWORD_WINDOW_S]
            if len(self._fails) >= PASSWORD_TRIES:
                return max(0, int(PASSWORD_LOCK_S - (now - self._fails[-1])) + 1)
        return 0

    def check_password(self, password) -> bool:
        if self.password_lock_s():
            return False
        try:
            stored = self.password_file.read_text(encoding="utf-8")
        except OSError:
            return False
        ok = isinstance(password, str) and len(password) <= 1000 and check_password_hash(stored, password)
        if not ok:
            with self._lock:
                self._fails.append(time.time())
        return ok

    def login(self, password) -> str | None:
        """A session token for the right password, else None."""
        return self.start_session() if self.check_password(password) else None


def issue_pair_code(home: Path) -> str:
    """For the `pair` command: a fresh one-time code the running Hlidskjalv will accept."""
    code = new_token()
    write_private(home / "pair", code)
    return code


def rotate_key(home: Path) -> None:
    """A new owner key: every session ends (the running Hlidskjalv notices by itself)."""
    write_private(home / "owner.key", new_token())
    try:
        (home / "web-sessions.json").unlink()
    except OSError:
        pass
