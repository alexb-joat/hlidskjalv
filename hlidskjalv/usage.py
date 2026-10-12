"""Cost and usage, for the Details view. All read-only, metadata only.

- budget(): today's and this month's cloud spend against the caps, from
  Trioditis's ~/.hlidskjalv/metrics.db (opened read-only) and budget.json.
- hardware(): Trioditis's local-model report, ~/.hlidskjalv/hardware.json.
- claude_usage(): per Claude Code session for this project: turns, tokens
  in / cached / out, the biggest context of any one turn, models, and how
  many shell commands the AI ran. Never prompt or response text.
- ClaudeUsage.windows(): usage in the same rolling windows Claude Code's own
  plan meter uses (a 5-hour window that starts with your first message and a
  7-day one), estimated from the transcripts. Anthropic doesn't publish how
  the caps are counted, so this is usage, not a percentage of the cap.
- accounts(): what each provider reports about your account, written by
  Trioditis to ~/.hlidskjalv/accounts.json (see trioditis/accounts.py).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_CAPS = {"daily_usd": 1.0, "monthly_usd": 15.0}  # Trioditis's defaults (trioditis_metrics/guard.py)


def _start_of_day(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _start_of_month(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def _epoch(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _family(model: str) -> str:
    m = model.lower()
    for name in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if name in m:
            return name
    return "other"


def budget(home: Path, now: float | None = None) -> dict:
    now = now or time.time()
    caps = dict(DEFAULT_CAPS)
    caps_per: dict = {}
    try:
        data = json.loads((home / "budget.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in caps:
                if isinstance(data.get(k), (int, float)) and not isinstance(data.get(k), bool):
                    caps[k] = float(data[k])
            for name, c in (data.get("providers") or {}).items():
                if isinstance(c, dict):
                    caps_per[str(name)] = {k: float(c[k]) for k in ("daily_usd", "monthly_usd")
                                           if isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool)}
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
            cols = {r[1] for r in con.execute("PRAGMA table_info(calls)")}
            providers: dict = {}
            if "provider" in cols:  # Trioditis 0.3+
                for name, t, m_, n in con.execute(
                        "SELECT provider, COALESCE(SUM(CASE WHEN ts>=? THEN cost END),0), COALESCE(SUM(cost),0), COUNT(*) "
                        "FROM calls WHERE ts>=? AND local=0 AND provider IS NOT NULL GROUP BY provider", (day, month)):
                    providers[name] = {"today": float(t), "month": float(m_), "calls_month": int(n), "caps": caps_per.get(name, {})}
            for name, c in caps_per.items():
                providers.setdefault(name, {"today": 0.0, "month": 0.0, "calls_month": 0, "caps": c})
            out["providers"] = providers
            try:
                out["limits"] = [dict(zip(("provider", "name", "limit", "remaining", "reset_at", "seen"), r))
                                 for r in con.execute("SELECT provider, name, lim, remaining, reset_at, seen FROM provider_limits")]
            except sqlite3.Error:
                out["limits"] = []
        finally:
            con.close()
    except sqlite3.Error as e:
        out.update(available=False, error=str(e)[:200])
    return out


def accounts(home: Path) -> dict:
    try:
        data = json.loads((home / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "providers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return {"available": False, "providers": {}}
    return {"available": True, "generated_at": data.get("generated_at"), "providers": data["providers"]}


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

    def __init__(self, watcher_dir: Path, all_projects: bool = False):
        self.dir = watcher_dir
        # Plan windows are per account, not per project: for them, read every project's transcripts.
        self.scope = watcher_dir.parent if all_projects else watcher_dir
        self._cache: dict[Path, tuple[tuple[int, int], dict]] = {}

    def _parse(self, p: Path) -> dict:
        s = {"turns": 0, "in": 0, "cached": 0, "out": 0, "peak": 0, "models": Counter(), "commands": 0,
             "first": None, "last": None, "turn_list": []}
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
                    epoch = _epoch(ts)
                    if epoch:
                        s["turn_list"].append((epoch, model[:60], tin + written + cached, cached, u.get("output_tokens") or 0))
        return s

    def _parts(self) -> list[tuple[Path, dict]]:
        """(file, parsed) for every transcript, parsing only files that changed."""
        if not self.scope.is_dir():
            return []
        out = []
        pats = ("*.jsonl", "*/subagents/*.jsonl") if self.scope == self.dir else ("*/*.jsonl", "*/*/subagents/*.jsonl")
        for p in (q for pat in pats for q in self.scope.glob(pat)):
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
            out.append((p, hit[1]))
        return out

    def windows(self, now: float | None = None, session_h: float = 5.0, week_d: float = 7.0,
                week_anchor: float | None = None) -> dict:
        """Usage in Claude Code's plan windows. Each window starts with the first turn after the
        previous one ended (what "Resets in …" counts down to) and ends session_h / week_d later.
        `week_anchor`: a known reset time for the weekly window (from the official panel); the
        window is then anchored to it instead of derived from the transcripts."""
        now = now if now is not None else time.time()
        turns = sorted(t for _, part in self._parts() for t in part["turn_list"])
        out = {"available": self.scope.is_dir(), "now": now, "scope": "all projects" if self.scope != self.dir else "this project",
               "week_anchored": week_anchor is not None}
        for key, length in (("session", session_h * 3600), ("week", week_d * 86400)):
            start = None
            if key == "week" and week_anchor is not None:
                end = week_anchor
                while end <= now:  # roll the known reset forward week by week
                    end += length
                start = end - length
            else:
                for t in turns:
                    if start is None or t[0] >= start + length:
                        start = t[0]
            active = start is not None and now < start + length
            inside = [t for t in turns if start is not None and start <= t[0] < start + length] if active else []
            fam: Counter = Counter()
            for _, model, tin, cached, tout in inside:
                fam[_family(model)] += tin + tout
            out[key] = {"active": active, "started_at": start if active else None,
                        "resets_at": start + length if active else None, "length_s": length,
                        "turns": len(inside), "in": sum(t[2] for t in inside), "cached": sum(t[3] for t in inside),
                        "out": sum(t[4] for t in inside), "by_family": dict(fam.most_common())}
        return out

    def report(self) -> dict:
        if not self.dir.is_dir():
            return {"available": False, "sessions": []}
        sessions: dict[str, dict] = {}
        for p, part in self._parts():
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
        return {"available": True, "sessions": out[:200],
                "windows": (getattr(self, "plan", None) or ClaudeUsage(self.dir, all_projects=True)).windows(
                    week_anchor=getattr(self, "week_anchor", None))}
