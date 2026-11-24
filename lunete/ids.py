"""Ids and clocks for the change log: UUIDv7 and a hybrid logical clock (standard library only).

- uuid7(): RFC 9562 version 7. 48 bits of Unix milliseconds, then a 12-bit counter (the spec's
  "method 1", fixed-length dedicated counter), then 62 random bits. Ids made by one process sort
  in the order they were made, even within one millisecond and when the wall clock steps back.
- Hlc: the hybrid logical clock of Kulkarni et al. (2014). Stamps are strings that sort as
  they compare, "<13-digit ms>.<6-digit counter>.<device>". They order changes for display;
  they never decide a conflict (the store's seq does). A stamp from elsewhere may be at most
  10 minutes ahead of the wall clock, so one bad clock can't drag every later stamp into the future.
- item_uuid5(): the derived id of a connector item (architecture.md section 8), so two devices
  that see the same commit make the same id.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid

_COUNTER_MAX = 0xFFF
MAX_AHEAD_MS = 10 * 60 * 1000  # a stamp from elsewhere may be this far ahead of the wall clock


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


def item_uuid5(org: str, source: str, external_id: str) -> uuid.UUID:
    """A connector item's id: UUIDv5, namespace = the store's org id, name = UTF-8 "source\nexternal_id"."""
    if not is_uuid7(org):
        raise ValueError(f"org id {org!r}: expected the store's lowercase UUIDv7 (meta.org)")
    if not source or "\n" in source:
        raise ValueError(f"connector name {source!r}: must be non-empty and on one line")
    if not external_id:
        raise ValueError(f"{source}: external_id is empty; a connector item needs its source's own id")
    return uuid.uuid5(uuid.UUID(org), f"{source}\n{external_id}")


def is_item_id(text: str) -> bool:
    """True for an id an item may have: a canonical UUIDv7 (typed) or UUIDv5 (derived, connector)."""
    try:
        u = uuid.UUID(text)
    except (ValueError, TypeError, AttributeError):
        return False
    return str(u) == text and u.version in (5, 7) and u.variant == uuid.RFC_4122


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

    def seen(self, stamp: str, max_ahead_ms: int | None = MAX_AHEAD_MS) -> None:
        """Fold in a stamp from elsewhere. Raises ValueError ("clock ahead") when it is more than
        `max_ahead_ms` ahead of the wall clock; pass None only to seed from stamps the store already
        accepted (the highest hlc in the file, on open)."""
        l_m, c_m, _ = parse_stamp(stamp)
        with self._lock:
            pt = self._now_ms()
            if max_ahead_ms is not None and l_m > pt + max_ahead_ms:
                raise ValueError(
                    f"clock ahead: stamp {stamp} is {(l_m - pt) / 60000:.1f} min ahead of this machine's clock "
                    f"(the limit is {max_ahead_ms / 60000:g} min); check the sending device's clock"
                )
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
