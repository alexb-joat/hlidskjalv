"""The owner key: who may change settings, use the terminal and acknowledge alerts.

Everything Hlidskjalv shows is readable by any local process (it binds
loopback, like any dev server). Anything that *changes* something needs the
owner key, which a browser gets once by redeeming a pairing code:

- The key is 32 random bytes in ~/.hlidskjalv/owner.key (owner-only). It survives
  restarts, so an autostarted Hlidskjalv doesn't need re-pairing at every login.
  `pair --rotate` replaces it and un-pairs every browser.
- Pairing codes work once and expire after PAIR_TTL_S. They come from the link
  Hlidskjalv opens at launch, or from `python -m hlidskjalv pair`, which
  writes one to ~/.hlidskjalv/pair (owner-only), so only your own user can pair a
  browser. Launch links are visible to other users in /proc/<pid>/cmdline,
  which is why they must stop working once used.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from pathlib import Path

PAIR_TTL_S = 600


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


class Owner:
    def __init__(self, home: Path):
        self.home = home
        self.key_file = home / "owner.key"
        self.pair_file = home / "pair"
        self._lock = threading.Lock()
        self._launch: tuple[str, float] | None = None
        self._stamp: tuple | None = None
        self._key = ""
        if len(self.key) < 40:
            write_private(self.key_file, new_token())

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

    def check(self, given) -> bool:
        key = self.key
        return len(key) >= 40 and _same(given, key)

    def launch_code(self) -> str:
        """A one-time code for the link Hlidskjalv opens itself."""
        code = new_token()
        with self._lock:
            self._launch = (code, time.time())
        return code

    def redeem(self, code) -> str | None:
        """Trade a pairing code for the owner key. Each code works once."""
        if not isinstance(code, str) or len(code) > 100:
            return None
        with self._lock:
            if self._launch and _same(code, self._launch[0]):
                fresh = time.time() - self._launch[1] < PAIR_TTL_S
                self._launch = None
                return self.key if fresh else None
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
            return self.key if time.time() - st.st_mtime < PAIR_TTL_S else None


def issue_pair_code(home: Path) -> str:
    """For the `pair` command: a fresh one-time code the running Hlidskjalv will accept."""
    code = new_token()
    write_private(home / "pair", code)
    return code


def rotate_key(home: Path) -> None:
    write_private(home / "owner.key", new_token())
