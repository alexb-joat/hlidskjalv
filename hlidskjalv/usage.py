"""Cost and usage, for the Details view. All read-only, metadata only.

- budget(): today's and this month's cloud spend against the caps, from
  Trioditis's ~/.hlidskjalv/metrics.db (opened read-only) and budget.json.
- hardware(): Trioditis's local-model report, ~/.hlidskjalv/hardware.json.
- claude_usage(): per Claude Code session for this project: turns, tokens
  in / cached / out, the biggest context of any one turn, models, and how
  many shell commands the AI ran. Never prompt or response text.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

DEFAULT_CAPS = {"daily_usd": 1.0, "monthly_usd": 15.0}  # Trioditis's defaults (trioditis_metrics/guard.py)


def _start_of_day(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _start_of_month(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def budget(home: Path, now: float | None = None) -> dict:
    now = now or time.time()
    caps = dict(DEFAULT_CAPS)
    try:
        data = json.loads((home / "budget.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in caps:
                if isinstance(data.get(k), (int, float)) and not isinstance(data.get(k), bool):
                    caps[k] = float(data[k])
    except (OSError, ValueError):
        pass
    db = home / "metrics.db"
    out = {"available": db.exists(), "caps": caps, "caps_file": (home / "budget.json").exists()}
    if not db.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            def q(sql, *args):
                return con.execute(sql, args).fetchone()
            day, month = _start_of_day(now), _start_of_month(now)
            out.update(
                today=float(q("SELECT COALESCE(SUM(cost),0) FROM calls WHERE ts>=? AND local=0", day)[0]),
                month=float(q("SELECT COALESCE(SUM(cost),0) FROM calls WHERE ts>=? AND local=0", month)[0]),
                calls_today=q("SELECT COUNT(*), COALESCE(SUM(local),0) FROM calls WHERE ts>=?", day),
                unpriced_today=int(q("SELECT COUNT(*) FROM calls WHERE ts>=? AND local=0 AND cost IS NULL AND status<400", day)[0]),
                unpriced_models=[r[0] for r in con.execute(
                    "SELECT DISTINCT model FROM calls WHERE ts>=? AND local=0 AND cost IS NULL AND status<400", (month,))][:20],
            )
            total, local = out.pop("calls_today")
            out["calls_today"] = {"total": int(total), "local": int(local or 0)}
        finally:
            con.close()
    except sqlite3.Error as e:
        out.update(available=False, error=str(e)[:200])
    return out


def hardware(home: Path) -> dict:
    try:
        data = json.loads((home / "hardware.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    if not isinstance(data, dict):
        return {"available": False}
    return {"available": True, **data}


class ClaudeUsage:
    """Per-session token use from Claude Code's transcripts, re-parsed only when a file changes."""

    def __init__(self, watcher_dir: Path):
        self.dir = watcher_dir
        self._cache: dict[Path, tuple[tuple[int, int], dict]] = {}

    def _parse(self, p: Path) -> dict:
        s = {"turns": 0, "in": 0, "cached": 0, "out": 0, "peak": 0, "models": Counter(), "commands": 0,
             "first": None, "last": None}
        seen, tools = set(), set()
        with p.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("type") != "assistant":
                    continue
                msg = r.get("message") or {}
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash" and b.get("id") not in tools:
                        tools.add(b.get("id"))
                        s["commands"] += 1
                mid, model = msg.get("id"), msg.get("model")
                if not mid or mid in seen or not isinstance(model, str) or model.startswith("<"):
                    continue
                seen.add(mid)
                u = msg.get("usage") or {}
                tin = u.get("input_tokens") or 0
                written, cached = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
                s["turns"] += 1
                s["in"] += tin + written + cached
                s["cached"] += cached
                s["out"] += u.get("output_tokens") or 0
                s["peak"] = max(s["peak"], tin + written + cached)
                s["models"][model[:60]] += 1
                ts = r.get("timestamp")
                if isinstance(ts, str):
                    s["first"] = s["first"] or ts
                    s["last"] = ts
        return s

    def report(self) -> dict:
        if not self.dir.is_dir():
            return {"available": False, "sessions": []}
        sessions: dict[str, dict] = {}
        files = [*self.dir.glob("*.jsonl"), *self.dir.glob("*/subagents/*.jsonl")]
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            hit = self._cache.get(p)
            if not hit or hit[0] != key:
                try:
                    hit = (key, self._parse(p))
                except OSError:
                    continue
                self._cache[p] = hit
            part = hit[1]
            sub = p.parent.name == "subagents"
            sid = (p.parent.parent.name if sub else p.stem)[:80]
            s = sessions.setdefault(sid, {"id": sid, "turns": 0, "in": 0, "cached": 0, "out": 0, "peak": 0,
                                          "models": Counter(), "commands": 0, "subagent_turns": 0, "first": None, "last": None})
            for k in ("turns", "in", "cached", "out", "commands"):
                s[k] += part[k]
            s["peak"] = max(s["peak"], part["peak"])
            s["models"].update(part["models"])
            if sub:
                s["subagent_turns"] += part["turns"]
            for k, pick in (("first", min), ("last", max)):
                vals = [v for v in (s[k], part[k]) if v]
                s[k] = pick(vals) if vals else None
        out = []
        for s in sessions.values():
            if not s["turns"]:
                continue
            s["models"] = dict(s["models"].most_common(5))
            out.append(s)
        out.sort(key=lambda s: s["last"] or "", reverse=True)
        return {"available": True, "sessions": out[:200]}
