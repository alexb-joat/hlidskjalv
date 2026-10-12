"""One thread per event source, each on its own cadence, each with a health record.

A source that is slow (a big folder scan, a journal that takes a second to answer)
must not hold the others back, and a source that keeps failing must say so once,
not fill the log. Every poller keeps when it last finished a poll and the last error,
and the page shows a "stalled" pill for a source that has been silent for STALL_S.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

STALL_S = 30.0   # silent this long: shown as stalled
WARN_EVERY_S = 60.0  # one stderr line per source per minute, at most


class Poller(threading.Thread):
    def __init__(self, name: str, source, interval: float, publish: Callable[[dict], None],
                 warn: Callable[[str], None] | None = None):
        super().__init__(name=f"poll-{name}", daemon=True)
        self.source_name = name
        self.source = source
        self.interval = max(float(interval), 0.05)
        self.publish = publish
        self.warn = warn or (lambda msg: print(msg, file=sys.stderr))
        self.last_ok = time.time()  # starting counts: nothing is stalled at boot
        self.last_error: str | None = None
        self.error_at = 0.0
        self.polls = 0
        self.errors = 0
        self.busy_since: float | None = None
        self._warned_at = -WARN_EVERY_S
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            self.once()
            self.stop.wait(self.interval)

    def once(self) -> int:
        """One poll; publishes what it found. Never raises: an error is recorded and said once a minute."""
        self.busy_since = time.time()
        n = 0
        try:
            for ev in self.source.poll():
                self.publish(ev)
                n += 1
            self.last_ok = time.time()
            self.last_error = None
        except Exception as e:  # one bad poll must not stop the source, let alone the others
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            self.error_at = time.time()
            if time.monotonic() - self._warned_at >= WARN_EVERY_S:
                self._warned_at = time.monotonic()
                self.warn(f"warning: {self.source_name}: {self.last_error}")
        finally:
            self.polls += 1
            self.busy_since = None
        return n

    def health(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        silent = now - self.last_ok
        return {
            "ok": round(self.last_ok, 3), "silent_s": round(max(silent, 0.0), 1),
            "stalled": silent > STALL_S,
            "error": self.last_error, "error_at": round(self.error_at, 3) if self.last_error else None,
            "busy": self.busy_since is not None, "polls": self.polls, "errors": self.errors,
        }


def start_all(sources: dict[str, object], interval: float, publish: Callable[[dict], None],
              warn: Callable[[str], None] | None = None) -> dict[str, Poller]:
    """A running Poller per source, keyed by name. Sources that are None are left out."""
    pollers = {name: Poller(name, src, interval, publish, warn) for name, src in sources.items() if src is not None}
    for p in pollers.values():
        p.start()
    return pollers


def health_of(pollers: dict[str, Poller], now: float | None = None) -> dict[str, dict]:
    return {name: p.health(now) for name, p in pollers.items()}
