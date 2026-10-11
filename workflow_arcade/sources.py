"""Event sources: files in the project, Git, terminal commands and Switchboard calls.

Every source is read-only. None of them executes anything from the project,
and file *contents* never leave this module: only names and line counts do.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .redact import redact

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".idea",
    "dist", "build", ".next", ".turbo", "target", "site-packages",
}
SKIP_FILE = re.compile(r"(~$|\.swp$|\.swx$|\.tmp$|^\.#|^#.*#$|\.pyc$|^\.DS_Store$|^Thumbs\.db$)")
# Files whose contents are never read, even for line counts.
SENSITIVE = re.compile(
    r"(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|\.keystore$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|credential|secret|\.kdbx$|^\.netrc$|^\.npmrc$|^\.pypirc$)", re.I)
TEXT_MAX_BYTES = 200_000
READ_MAX = 1_000_000
MAX_FILES = 5000


def is_sensitive(rel: str) -> bool:
    return bool(SENSITIVE.search(rel.rsplit("/", 1)[-1]))


def _read_lines(path: Path) -> list[str] | None:
    """Text lines of a small text file, or None for binary/large/unreadable files."""
    try:
        if path.stat().st_size > TEXT_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace").splitlines()


def _line_delta(old: list[str], new: list[str]) -> tuple[int, int]:
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


class FileWatcher:
    """Polls the project tree and reports created, changed and deleted files."""

    def __init__(self, root: Path):
        self.root = root
        self.state: dict[str, tuple[int, int]] = {}
        self.lines: dict[str, list[str]] = {}
        self.truncated = False

    def _scan(self) -> dict[str, tuple[int, int]]:
        found: dict[str, tuple[int, int]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):  # never follows symlinks
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in filenames:
                if SKIP_FILE.search(name):
                    continue
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = Path(full).relative_to(self.root).as_posix()
                found[rel] = (st.st_mtime_ns, st.st_size)
                if len(found) >= MAX_FILES:
                    self.truncated = True
                    return found
        return found

    def _remember(self, rel: str) -> list[str] | None:
        if is_sensitive(rel):
            return None
        lines = _read_lines(self.root / rel)
        if lines is None:
            self.lines.pop(rel, None)
        else:
            self.lines[rel] = lines
        return lines

    def snapshot(self) -> list[dict]:
        self.state = self._scan()
        files = []
        for rel, (_, size) in sorted(self.state.items()):
            lines = self._remember(rel)
            files.append({"path": rel, "size": size, "lines": len(lines) if lines is not None else None,
                          "sensitive": is_sensitive(rel)})
        return files

    def poll(self) -> list[dict]:
        now = self._scan()
        events = []
        for rel, sig in now.items():
            old = self.state.get(rel)
            if old == sig:
                continue
            before = self.lines.get(rel, [])
            after = self._remember(rel)
            ev = {"type": "file_created" if old is None else "file_changed", "path": rel,
                  "size": sig[1], "sensitive": is_sensitive(rel)}
            if after is not None:
                ev["added"], ev["removed"] = _line_delta(before if old else [], after)
                ev["lines"] = len(after)
                if old is not None and ev["added"] == ev["removed"] == 0:
                    continue  # touched but no content change (e.g. saved twice)
            events.append(ev)
        for rel in self.state.keys() - now.keys():
            gone = self.lines.pop(rel, None)
            events.append({"type": "file_deleted", "path": rel, "removed": len(gone) if gone else 0,
                           "sensitive": is_sensitive(rel)})
        self.state = now
        return events


class GitWatcher:
    """Reports new commits and branch switches. Runs only read-only git commands."""

    # Repo-local config can name programs for git to run (fsmonitor, hooks).
    # Switch those off so a cloned repo can't execute anything through us.
    SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull]

    def __init__(self, root: Path):
        self.root = root
        self.git = shutil.which("git")
        self.head: str | None = None
        self.branch: str | None = None
        self.next_check = 0.0

    def _run(self, *args: str) -> str | None:
        if not self.git:
            return None
        try:
            r = subprocess.run([self.git, *self.SAFE, "-C", str(self.root), *args],
                               capture_output=True, text=True, timeout=5,
                               env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    def available(self) -> bool:
        return self.git is not None and (self.root / ".git").exists()

    def prime(self) -> None:
        if self.available():
            self.head = self._run("rev-parse", "HEAD")
            self.branch = self._run("rev-parse", "--abbrev-ref", "HEAD")

    def poll(self) -> list[dict]:
        if time.monotonic() < self.next_check or not self.available():
            return []
        self.next_check = time.monotonic() + 2.0
        events = []
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD")
        if branch and self.branch and branch != self.branch:
            events.append({"type": "branch", "branch": redact(branch, 80), "previous": redact(self.branch, 80)})
        self.branch = branch or self.branch
        head = self._run("rev-parse", "HEAD")
        if head and head != self.head:
            info = self._run("show", "-s", "--format=%s", head) or ""
            stat = self._run("show", "--no-ext-diff", "--no-textconv", "--shortstat", "--format=", head) or ""
            nums = {k: int(v) for v, k in re.findall(r"(\d+) (file|insertion|deletion)", stat)}
            events.append({"type": "commit", "sha": head[:7], "message": redact(info, 140),
                           "files_changed": nums.get("file", 0), "insertions": nums.get("insertion", 0),
                           "deletions": nums.get("deletion", 0), "first": self.head is None})
            self.head = head
        return events


class Tail:
    """Reads lines appended to a file since it was opened (survives truncation/rotation)."""

    def __init__(self, path: Path | None):
        self.path = path
        self.offset = 0
        self.skipping = False  # inside a line longer than one read: drop it
        if path and path.exists():
            self.offset = path.stat().st_size  # only new lines

    def lines(self) -> list[str]:
        if not self.path or not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset, self.skipping = 0, False
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read(READ_MAX)
        if self.skipping:
            nl = data.find(b"\n")
            if nl < 0:
                self.offset += len(data)
                return []
            self.offset += nl + 1
            data, self.skipping = data[nl + 1:], False
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) == READ_MAX:  # one line bigger than a whole read (e.g. an inline image)
                self.offset += len(data)
                self.skipping = True
            return []
        self.offset += end + 1
        return data[: end + 1].decode("utf-8", "replace").splitlines()


class CommandWatcher:
    """Terminal commands recorded by the optional shell hook, limited to this project."""

    def __init__(self, root: Path, log: Path):
        self.root = root
        self.tail = Tail(log)

    def poll(self) -> list[dict]:
        events = []
        for line in self.tail.lines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or not parts[2].strip():
                continue
            _, cwd, cmd = parts
            try:
                rel = Path(cwd).resolve().relative_to(self.root)
            except (ValueError, OSError):
                continue  # typed somewhere else: not part of this project
            events.append({"type": "command", "cmd": redact(cmd.strip(), 400),
                           "cwd": rel.as_posix() if str(rel) != "." else "."})
        return events


def load_prices(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")}


class SwitchboardWatcher:
    """AI calls from Switchboard's decision log (metadata only — no prompt text)."""

    def __init__(self, log: Path | None, prices: dict):
        self.tail = Tail(log)
        self.prices = prices

    def poll(self) -> list[dict]:
        events = []
        for line in self.tail.lines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            usage = r.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            model = r.get("model")
            cost = None
            if r.get("local"):
                cost = 0.0
            elif model in self.prices and isinstance(tin, int) and isinstance(tout, int):
                p = self.prices[model]
                cost = round((tin * float(p.get("input", 0)) + tout * float(p.get("output", 0))) / 1e6, 6)
            events.append({
                "type": "ai_call",
                "route": str(r.get("route") or "?")[:40],
                "model": str(model or "none")[:60],
                "local": r.get("local"),
                "status": r.get("status"),
                "latency_ms": r.get("latency_ms"),
                "tokens_in": tin if isinstance(tin, int) else None,
                "tokens_out": tout if isinstance(tout, int) else None,
                "cost": cost,
                "sensitive": [str(s)[:30] for s in (r.get("sensitive") or [])][:10],
                "fallbacks": len(r.get("failed_attempts") or []),
                "reasons": [redact(str(x))[:160] for x in (r.get("reasons") or [])][:20],
                "attempts": [_attempt(a) for a in (r.get("failed_attempts") or [])][:10],
            })
        return events


def _attempt(a) -> dict:
    """One failed or skipped model from Switchboard's log, e.g. a budget block."""
    if not isinstance(a, dict):
        return {"model": str(a)[:60]}
    out = {"model": str(a.get("model") or "?")[:60]}
    for k in ("error", "reason"):
        if a.get(k) is not None:
            out[k] = str(a[k])[:160]
    if isinstance(a.get("status"), int):
        out["status"] = a["status"]
    return out


def claude_code_project_dir(root: Path, claude_home: Path | None = None) -> Path:
    """Claude Code's own transcript folder for a project root.

    Claude Code names each project's folder after its absolute path with every
    character that isn't a letter or digit replaced by "-" (so /home/me/My App
    becomes -home-me-My-App, C:\\code\\app becomes C--code-app). `root` must
    already be resolved, as watch() does. If Claude Code ever changes that scheme, the
    folder simply isn't found and this source reports nothing, like GitWatcher
    does for a non-git folder.
    """
    home = claude_home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))


class ClaudeCodeWatcher:
    """AI turns from Claude Code's own session transcripts (metadata only).

    Claude Code talks to Anthropic directly on your subscription, not through
    Switchboard, so those calls never reach Switchboard's decision log. This
    tails Claude Code's per-session JSONL transcripts for the watched project
    instead (including its subagents' transcripts) and reports each finished
    assistant turn: model and token counts only. Prompt and response text are
    never read into an event.
    """

    MAX_SEEN = 20_000

    def __init__(self, root: Path, claude_home: Path | None = None):
        self.root = root
        self.dir = claude_code_project_dir(root, claude_home)
        self.tails: dict[Path, Tail] = {}
        self.seen: dict[str, None] = {}  # insertion-ordered, so trimming drops the oldest
        for p in self._transcripts():
            self.tails[p] = Tail(p)  # skip transcript history predating this watch

    def available(self) -> bool:
        return self.dir.is_dir()

    def _transcripts(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        # <session>.jsonl, plus <session>/subagents/*.jsonl for agents it starts
        return sorted([*self.dir.glob("*.jsonl"), *self.dir.glob("*/subagents/*.jsonl")])

    def _events(self, line: str, subagent: bool) -> list[dict]:
        if '"assistant"' not in line:  # cheap pre-filter: most lines are tool results
            return []
        try:
            r = json.loads(line)
        except ValueError:
            return []
        if not isinstance(r, dict) or r.get("type") != "assistant":
            return []
        out = [self._command(b, r.get("cwd"), subagent) for b in (r.get("message") or {}).get("content") or []
               if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash"]
        turn = self._event(r, subagent)
        return [e for e in [turn, *out] if e]

    def _command(self, block: dict, cwd, subagent: bool) -> dict | None:
        """A shell command the AI ran (its Bash tool). Masked like typed commands; never its output."""
        bid, inp = block.get("id"), block.get("input") or {}
        cmd = inp.get("command")
        if not bid or bid in self.seen or not isinstance(cmd, str) or not cmd.strip():
            return None
        self.seen[bid] = None
        where = "?"
        if isinstance(cwd, str) and cwd:
            try:
                rel = Path(cwd).resolve().relative_to(self.root)
                where = "." if str(rel) == "." else rel.as_posix()
            except (ValueError, OSError):
                home = str(Path.home())
                where = "~" + cwd[len(home):] if cwd.startswith(home) else cwd
        return {"type": "command", "cmd": redact(cmd, 1000), "cwd": where[:300], "actor": "ai",
                "agent": "Claude Code" + (" (subagent)" if subagent else ""),
                "why": redact(str(inp.get("description") or ""), 200) or None}

    def _event(self, r: dict, subagent: bool) -> dict | None:
        msg = r.get("message") or {}
        mid, model = msg.get("id"), msg.get("model")
        # One message is split over several lines (one per content block); count it once.
        # "<synthetic>" marks messages Claude Code wrote itself (errors, notices), not a model call.
        if not mid or not isinstance(model, str) or model.startswith("<") or mid in self.seen:
            return None
        self.seen[mid] = None
        if len(self.seen) > self.MAX_SEEN:
            for old in list(self.seen)[: self.MAX_SEEN // 2]:
                del self.seen[old]
        usage = msg.get("usage") or {}
        tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
        written = usage.get("cache_creation_input_tokens") or 0
        cached = usage.get("cache_read_input_tokens") or 0
        return {
            "type": "ai_call",
            "route": "claude-code" + (" (subagent)" if subagent else ""),
            "model": model[:60],
            "local": False,
            "subscription": True,
            "status": 200,
            "latency_ms": None,
            # Everything the model read this turn; the cached share is re-read context, cheaper to serve.
            "tokens_in": tin + written + cached if isinstance(tin, int) else None,
            "tokens_cached": cached if isinstance(tin, int) else None,
            "tokens_out": tout if isinstance(tout, int) else None,
            "cost": None,
            "sensitive": [],
            "fallbacks": 0,
        }

    def poll(self) -> list[dict]:
        events = []
        for p in self._transcripts():
            if p not in self.tails:
                t = Tail(p)
                t.offset = 0  # a transcript that started after we began watching: read it all
                self.tails[p] = t
            sub = p.parent.name == "subagents"
            for line in self.tails[p].lines():
                events.extend(self._events(line, sub))
        return events
