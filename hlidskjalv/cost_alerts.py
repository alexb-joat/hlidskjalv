"""Alerts from the cost layer: caps nearly or fully reached, rate limits exhausted.

Polled every CHECK_EVERY_S from the same read-only sources the Details view
uses (usage.budget). Each condition alerts once per crossing: the Alerter
folds repeats, and the fingerprint includes the day or month so a cap that
resets alerts again next period.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import usage as u

CHECK_EVERY_S = 60
WARN_AT = 0.75


class CostWatcher:
    def __init__(self, home: Path, alerter, every_s: float = CHECK_EVERY_S):
        self.home, self.alerter, self.every = home, alerter, every_s
        self._next = 0.0

    def poll(self) -> list[dict]:
        if time.monotonic() < self._next or not self.alerter.enabled("cost"):
            return []
        self._next = time.monotonic() + self.every
        b = u.budget(self.home)
        if not b.get("available"):
            return []
        day = time.strftime("%Y-%m-%d")
        month = day[:7]
        self._cap("Daily cap", b.get("today", 0.0), b["caps"].get("daily_usd"), "day", day)
        self._cap("Monthly cap", b.get("month", 0.0), b["caps"].get("monthly_usd"), "month", month)
        for name, p in (b.get("providers") or {}).items():
            caps = p.get("caps") or {}
            self._cap(f"{name}: daily cap", p.get("today", 0.0), caps.get("daily_usd"), "day", day, name)
            self._cap(f"{name}: monthly cap", p.get("month", 0.0), caps.get("monthly_usd"), "month", month, name)
        n = int(b.get("unpriced_today") or 0)
        if n and getattr(self, "_unpriced_day", None) != day:
            self._unpriced_day = day
            self.alerter.raise_("info", "cost", f"{n} cloud call{'s' if n != 1 else ''} today without a price",
                                "Those models aren't in ~/.hlidskjalv/prices.json, so they count as $0 toward the caps (unless "
                                "assume_unpriced_usd is set in budget.json). Add their prices to see real spend.",
                                fingerprint=f"unpriced-{day}", expected=True)
        for lim in b.get("limits") or []:
            if lim.get("remaining") == 0 and lim.get("reset_at") and lim["reset_at"] > time.time():
                self.alerter.raise_("medium", "cost", f"{lim['provider']}: {str(lim['name']).replace('_', ' ')} limit used up",
                                    f"The provider reported 0 left; it refills at {time.strftime('%H:%M:%S', time.localtime(lim['reset_at']))}. "
                                    "Requests until then get 429s and fall through to the next model.",
                                    fingerprint=f"ratelimit-{lim['provider']}-{lim['name']}-{int(lim['reset_at'])}")
        return []

    def _cap(self, label: str, spent: float, cap, period: str, stamp: str, provider: str | None = None) -> None:
        if not cap or cap <= 0:
            return
        share = spent / cap
        if share >= 1:
            self.alerter.raise_("high", "cost", f"{label} reached ({u_money(spent)} of {u_money(cap)})",
                                (f"Cloud calls through {provider} are skipped until the {period} ends; " if provider
                                 else f"Cloud calls are skipped until the {period} ends; ")
                                + "local models answer instead. Raise the cap in ~/.hlidskjalv/budget.json if that's not what you want.",
                                fingerprint=f"cap-{label}-{stamp}-full", data={"spent": spent, "cap": cap, "provider": provider})
        elif share >= WARN_AT:
            self.alerter.raise_("medium", "cost", f"{label}: {int(share * 100)}% used ({u_money(spent)} of {u_money(cap)})",
                                f"At 100% cloud calls{' through ' + provider if provider else ''} stop for the rest of the {period} and local models take over.",
                                fingerprint=f"cap-{label}-{stamp}-warn", data={"spent": spent, "cap": cap, "provider": provider})


def u_money(v: float) -> str:
    return f"${v:.4f}" if 0 < v < 0.01 else f"${v:.2f}"
