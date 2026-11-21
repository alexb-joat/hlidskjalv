"""Ids and clocks for the change log: UUIDv7 and a hybrid logical clock (standard library only).

- uuid7(): RFC 9562 version 7. 48 bits of Unix milliseconds, then a 12-bit counter (the spec's
  "method 1", fixed-length dedicated counter), then 62 random bits. Ids made by one process sort
  in the order they were made, even within one millisecond and when the wall clock steps back.
- Hlc: the hybrid logical clock of Kulkarni et al. (2014). Stamps are strings that sort as
  they compare, "<13-digit ms>.<6-digit counter>.<device>". They order changes for display;
  they never decide a conflict (the store's seq does).
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid

_COUNTER_MAX = 0xFFF


class _Uuid7:
    def __init__(self, now_ms=None):
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = threading.Lock()
        self._ms = -1
        self._counter = 0

    def __call__(self) -> uuid.UUID:
        with self._lock:
            ms = self._now_ms()
            if ms > self._ms:
                self._ms = ms
                self._counter = int.from_bytes(os.urandom(2), "big") & 0x7FF  # random start, top bit clear: room to count
            else:  # same millisecond, or the clock stepped back: keep the last ms and count on
                self._counter += 1
                if self._counter > _COUNTER_MAX:  # 4096 ids in one ms: borrow the next ms (RFC 9562 6.2)
                    self._ms += 1
                    self._counter = 0
            ms, counter = self._ms, self._counter
        rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | counter << 64 | 0b10 << 62 | rand_b
        return uuid.UUID(int=value)


uuid7 = _Uuid7()


def uuid7_ms(u: uuid.UUID | str) -> int:
    """The Unix milliseconds a UUIDv7 carries."""
    return uuid.UUID(str(u)).int >> 80


def is_uuid7(text: str) -> bool:
    """True for a canonical lowercase UUIDv7 string (the only form the log stores)."""
    try:
        u = uuid.UUID(text)
    except (ValueError, TypeError, AttributeError):
        return False
    return str(u) == text and u.version == 7 and u.variant == uuid.RFC_4122


_STAMP = re.compile(r"^(\d{13})\.(\d{6})\.([A-Za-z0-9_-]{1,64})$")


class Hlc:
    """One device's hybrid logical clock. `now()` stamps a local change; `seen(stamp)` folds in a
    stamp from elsewhere so the next local stamp sorts after it."""

    def __init__(self, device: str, now_ms=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
            raise ValueError(f"device id {device!r}: use 1 to 64 letters, digits, - or _")
        self.device = device
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = threading.Lock()
        self._l = 0
        self._c = 0

    def _stamp(self) -> str:
        if self._c > 999_999:  # a million changes in one ms: move to the next ms
            self._l += 1
            self._c = 0
        return f"{self._l:013d}.{self._c:06d}.{self.device}"

    def now(self) -> str:
        with self._lock:
            pt = self._now_ms()
            if pt > self._l:
                self._l, self._c = pt, 0
            else:
                self._c += 1
            return self._stamp()

    def seen(self, stamp: str) -> None:
        l_m, c_m, _ = parse_stamp(stamp)
        with self._lock:
            pt = self._now_ms()
            l_new = max(self._l, l_m, pt)
            if l_new == self._l and l_new == l_m:
                self._c = max(self._c, c_m) + 1
            elif l_new == self._l:
                self._c += 1
            elif l_new == l_m:
                self._c = c_m + 1
            else:
                self._c = 0
            self._l = l_new


def parse_stamp(stamp: str) -> tuple[int, int, str]:
    m = _STAMP.match(stamp or "")
    if not m:
        raise ValueError(f"not a clock stamp: {stamp!r} (expected <13-digit ms>.<6-digit counter>.<device>)")
    return int(m.group(1)), int(m.group(2)), m.group(3)
