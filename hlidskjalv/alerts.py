"""Alerts: things you didn't do yourself, sorted by how much they matter.

Every alert is an event {"type": "alert", "severity", "source", "title",
"detail", "fingerprint", "expected"} published like any other event, and
appended to ~/.hlidskjalv/alerts.jsonl so it survives restarts.

Sources (each can be switched off in Settings → Alerts, no restart needed):

- persistence: something set itself up to run later. Login items (systemd
  user units, launchd agents, ~/.config/autostart, Windows Run/Startup),
  crontab, shell startup files, SSH authorized_keys, ~/.gitconfig and this
  project's Git hooks. The baseline is taken when Hlidskjalv starts, so
  only changes after that alert.
- system_log: sudo use, SSH logins and new login sessions from the system
  journal (Linux; needs the adm or systemd-journal group).
- refused_access: requests Hlidskjalv or Trioditis turned away (see
  server.py and TrioditisSecurityWatcher).
- ai_commands: risky commands an AI agent ran are flagged by the page from
  the command events (sources.ClaudeCodeWatcher).
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .programs import which
from .redact import redact

SEVERITIES = ("info", "medium", "high", "critical")
REPEAT_WINDOW_S = 300
HISTORY_MAX_LINES = 2000


class Alerter:
    """Publishes alerts, folding repeats of the same thing within REPEAT_WINDOW_S into one."""

    def __init__(self, publish, settings=None, history: Path | None = None):
        self.publish = publish
        self.settings = settings
        self.history = history
        self._seen: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def enabled(self, source: str) -> bool:
        if self.settings is None:
            return True
        return bool(self.settings.data.get("alerts", {}).get(source, True))

    def raise_(self, severity: str, source: str, title: str, detail: str = "", fingerprint: str | None = None,
               expected: bool = False, data: dict | None = None) -> dict | None:
        if not self.enabled(source):
            return None
        fp = fingerprint or hashlib.sha256(f"{source}|{title}|{detail}".encode()).hexdigest()[:16]
        now = time.time()
        with self._lock:
            last, repeats = self._seen.get(fp, (0.0, 0))
            if now - last < REPEAT_WINDOW_S:
                self._seen[fp] = (last, repeats + 1)
                return None
            self._seen[fp] = (now, 0)
        if repeats:
            detail = f"{detail} (and {repeats} more time{'s' if repeats != 1 else ''} in the last {REPEAT_WINDOW_S // 60} minutes)".strip()
        ev = {"type": "alert", "severity": severity if severity in SEVERITIES else "medium", "source": source,
              "title": title[:200], "detail": redact(detail, 2000), "fingerprint": fp, "expected": expected,
              "uid": hashlib.sha256(f"{fp}|{now}".encode()).hexdigest()[:20]}
        if data:
            ev["data"] = data
        self.publish(ev)
        self._remember(ev)
        return ev

    def _remember(self, ev: dict) -> None:
        if not self.history:
            return
        try:
            fd = os.open(self.history, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps({**ev, "ts": time.time()}, ensure_ascii=False) + "\n")
            if self.history.stat().st_size > HISTORY_MAX_LINES * 1500:  # keep the newest half
                lines = self.history.read_text(encoding="utf-8", errors="replace").splitlines()[-HISTORY_MAX_LINES // 2:]
                tmp = self.history.with_name(self.history.name + ".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                os.replace(tmp, self.history)
        except OSError:
            pass


def read_history(path: Path, limit: int = 500) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "alert":
            out.append(ev)
    return out


class Acks:
    """Which alerts you've marked as seen (~/.hlidskjalv/alerts-ack.json)."""

    def __init__(self, path: Path):
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.ids = [str(x) for x in data][-5000:] if isinstance(data, list) else []
        except (OSError, ValueError):
            self.ids = []

    def add(self, ids) -> list[str]:
        new = [str(i)[:40] for i in ids if isinstance(i, str)][:1000]
        self.ids = list(dict.fromkeys(self.ids + new))[-5000:]
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.ids, f)
        os.replace(tmp, self.path)
        return self.ids


# ---------------------------------------------------------------- persistence

def _system() -> str:
    s = platform.system().lower()
    return "windows" if s.startswith("win") else s


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ssh_fingerprints(text: str) -> list[str]:
    """SHA256 fingerprints like `ssh-keygen -lf` prints; the keys themselves are never shown."""
    out = []
    for ln in text.splitlines():
        parts = ln.split()
        for i, p in enumerate(parts):
            if p.startswith(("ssh-", "ecdsa-", "sk-")) and i + 1 < len(parts):
                try:
                    blob = base64.b64decode(parts[i + 1], validate=True)
                except ValueError:
                    break
                out.append("SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("="))
                break
    return out


class Target:
    """One place where something can set itself up to run later."""

    def __init__(self, label: str, severity: str, why: str, path: Path | None = None, glob: str | None = None,
                 command: list[str] | None = None, reader=None, kind: str = "text"):
        self.label, self.severity, self.why = label, severity, why
        self.path, self.glob, self.command, self.reader, self.kind = path, glob, command, reader, kind

    def read(self) -> dict[str, bytes]:
        """name -> content, for everything this target covers right now."""
        if self.reader:
            return self.reader()
        if self.command:
            exe = which(self.command[0])
            if not exe:
                return {}
            try:
                r = subprocess.run([exe, *self.command[1:]], capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return {}
            return {" ".join(self.command): r.stdout} if r.returncode == 0 and r.stdout.strip() else {}
        if self.path is None:
            return {}
        files = []
        if self.glob:
            if self.path.is_dir():
                files = [p for p in self.path.glob(self.glob) if p.is_file() or p.is_symlink()]
        elif self.path.is_file():
            files = [self.path]
        out = {}
        for p in files[:500]:
            try:
                out[str(p)] = (b"-> " + os.readlink(p).encode()) if p.is_symlink() else p.read_bytes()[:200_000]
            except OSError:
                continue
        return out


def _windows_run_keys() -> dict[str, bytes]:
    try:
        import winreg
    except ImportError:
        return {}
    out = {}
    for sub in (r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out[f"HKCU\\{sub}\\{name}"] = str(value).encode()
                    i += 1
        except OSError:
            continue
    return out


def persistence_targets(root: Path, home: Path | None = None) -> list[Target]:
    home = home or Path.home()
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    t = [
        Target("SSH login keys", "critical", "A new key here lets someone log in to this computer as you.",
               home / ".ssh" / "authorized_keys", kind="ssh"),
        Target("Git settings", "medium", "Git runs hooks and helpers named here (hooksPath, fsmonitor, sshCommand).",
               home / ".gitconfig", kind="gitconfig"),
        Target("Git hooks in this project", "high", "Hooks run automatically on commit, checkout, push…",
               root / ".git" / "hooks", glob="*"),
    ]
    for name in (".bashrc", ".bash_profile", ".profile", ".bash_login", ".bash_logout", ".zshrc", ".zprofile", ".zshenv"):
        t.append(Target(f"Shell startup ({name})", "high", "Runs every time a terminal opens.", home / name))
    t.append(Target("Shell startup (fish)", "high", "Runs every time a terminal opens.", cfg / "fish" / "config.fish"))
    sysname = _system()
    if sysname == "linux":
        t += [Target("Login services (systemd user units)", "high", "Programs that start at login or on a timer.",
                     cfg / "systemd" / "user", glob="**/*"),
              Target("Desktop autostart", "high", "Programs your desktop starts at login.", cfg / "autostart", glob="*.desktop"),
              Target("Scheduled jobs (crontab)", "high", "Commands that run on a schedule.", command=["crontab", "-l"])]
    elif sysname == "darwin":
        t += [Target("Login agents (launchd)", "high", "Programs macOS starts at login.", home / "Library" / "LaunchAgents", glob="*.plist"),
              Target("Scheduled jobs (crontab)", "high", "Commands that run on a schedule.", command=["crontab", "-l"])]
    elif sysname == "windows":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        t += [Target("Startup folder", "high", "Programs Windows starts at sign-in.",
                     appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup", glob="*"),
              Target("Run keys (registry)", "high", "Programs Windows starts at sign-in.", reader=_windows_run_keys)]
    return t


def _is_sample_hook(name: str) -> bool:
    return name.endswith(".sample")


RISKY_GIT = re.compile(r"^\s*(hookspath|fsmonitor|sshcommand|askpass|pager|editor|helper)\s*=", re.I | re.M)


class PersistenceWatcher:
    def __init__(self, root: Path, alerter: Alerter, every_s: float = 15.0, home: Path | None = None,
                 expected: Path | None = None):
        self.alerter, self.every = alerter, every_s
        self.targets = persistence_targets(root, home)
        self.expected = expected  # ~/.hlidskjalv/expected.json: {path: sha256} Hlidskjalv wrote itself
        self.state = {id(t): self._snap(t) for t in self.targets}
        self._next = time.monotonic() + every_s

    def _snap(self, t: Target) -> dict[str, bytes]:
        snap = t.read()
        if t.glob == "*" and t.path and t.path.name == "hooks":
            snap = {k: v for k, v in snap.items() if not _is_sample_hook(k)}
        return snap

    def _expected(self, name: str, data: bytes) -> bool:
        if not self.expected:
            return False
        try:
            known = json.loads(self.expected.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(known, dict) and known.get(name) == _sha(data)

    def poll(self) -> list[dict]:
        if time.monotonic() < self._next or not self.alerter.enabled("persistence"):
            return []
        self._next = time.monotonic() + self.every
        for t in self.targets:
            before, after = self.state[id(t)], self._snap(t)
            if before == after:
                continue
            self.state[id(t)] = after
            for name in sorted(set(before) | set(after)):
                old, new = before.get(name), after.get(name)
                if old == new:
                    continue
                self._report(t, name, old, new)
        return []  # alerts go out through the Alerter

    def _report(self, t: Target, name: str, old: bytes | None, new: bytes | None) -> None:
        change = "added" if old is None else "removed" if new is None else "changed"
        sev = t.severity if change != "removed" else "medium"
        home = str(Path.home())
        shown = "~" + name[len(home):] if name.startswith(home + os.sep) else name
        short = shown if len(shown) < 120 else "…" + shown[-118:]
        if t.kind == "ssh":
            before = set(_ssh_fingerprints((old or b"").decode("utf-8", "replace")))
            now = set(_ssh_fingerprints((new or b"").decode("utf-8", "replace")))
            added, gone = sorted(now - before), sorted(before - now)
            sev = "critical" if added else "medium"
            detail = (f"{len(now)} key(s) now. " + (f"New: {', '.join(added)}. " if added else "")
                      + (f"Removed: {', '.join(gone)}." if gone else "")).strip()
            title = f"SSH login keys {'gained a key' if added else 'changed'}"
        else:
            detail = self._diff(old, new)
            if t.kind == "gitconfig":
                sev = "high" if RISKY_GIT.search(detail) else "medium"
            title = f"{t.label}: {change} {Path(short).name if '/' in short or os.sep in short else short}"
        expected = new is not None and self._expected(name, new)
        self.alerter.raise_("info" if expected else sev, "persistence", title,
                            f"{short}\n{t.why}\n{detail}".strip(), fingerprint=None, expected=expected,
                            data={"path": short, "change": change, "what": t.label})

    @staticmethod
    def _diff(old: bytes | None, new: bytes | None) -> str:
        def lines(b):
            if b is None:
                return []
            if b"\0" in b[:4096]:
                return [f"(binary, {len(b)} bytes, sha256 {_sha(b)[:16]})"]
            return b.decode("utf-8", "replace").splitlines()
        diff = [ln for ln in difflib.unified_diff(lines(old), lines(new), lineterm="", n=0)
                if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
        shown = "\n".join(redact(ln, 300) for ln in diff[:40])
        return shown + (f"\n… {len(diff) - 40} more changed lines" if len(diff) > 40 else "")


def remember_expected(home: Path, path: Path, data: bytes) -> None:
    """Called right before Hlidskjalv writes a login item itself, so it isn't reported as foreign."""
    f = home / "expected.json"
    try:
        known = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(known, dict):
            known = {}
    except (OSError, ValueError):
        known = {}
    known[str(path)] = _sha(data)
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(known, fh)
    os.replace(tmp, f)


# ---------------------------------------------------------------- system journal

class SystemLogWatcher:
    """sudo, SSH and login sessions from the system journal (Linux)."""

    MATCHES = ["SYSLOG_IDENTIFIER=sudo", "+", "SYSLOG_IDENTIFIER=sshd", "+", "SYSLOG_IDENTIFIER=sshd-session",
               "+", "SYSLOG_IDENTIFIER=systemd-logind"]

    def __init__(self, alerter: Alerter, every_s: float = 10.0, runner=None):
        self.alerter, self.every = alerter, every_s
        self.run = runner or self._journalctl
        self.cursor: str | None = None
        self.available, self.why = False, "only on Linux"
        self._next = 0.0
        if _system() == "linux" and (runner or which("journalctl")):
            out = self.run(["-n", "1", "-o", "json", "--system", *self.MATCHES])
            if out is None:
                self.why = "journalctl failed"
            elif "insufficient permissions" in out.lower() or "no journal files" in out.lower():
                self.why = "no permission: add yourself to the adm or systemd-journal group"
            else:
                self.available, self.why = True, ""
                for ln in out.splitlines():
                    try:
                        self.cursor = json.loads(ln).get("__CURSOR") or self.cursor
                    except ValueError:
                        continue

    @staticmethod
    def _journalctl(args: list[str]) -> str | None:
        exe = which("journalctl")
        if not exe:
            return None
        try:
            r = subprocess.run([exe, "--no-pager", "-q", *args], capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout + (r.stderr if r.returncode else "")

    def poll(self) -> list[dict]:
        if not self.available or time.monotonic() < self._next or not self.alerter.enabled("system_log"):
            return []
        self._next = time.monotonic() + self.every
        args = ["-o", "json", "--system", *(["--after-cursor", self.cursor] if self.cursor else ["-n", "0"]), *self.MATCHES]
        out = self.run(args) or ""
        for ln in out.splitlines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            self.cursor = rec.get("__CURSOR") or self.cursor
            self.classify(str(rec.get("SYSLOG_IDENTIFIER") or ""), str(rec.get("MESSAGE") or ""))
        return []

    def classify(self, ident: str, msg: str) -> None:
        a = self.alerter.raise_
        if ident == "sudo":
            if "incorrect password" in msg or "authentication failure" in msg or "NOT in sudoers" in msg:
                a("high", "system_log", "sudo: failed attempt", msg)
            elif "COMMAND=" in msg:
                user = msg.split(" :", 1)[0].strip()
                cmd = msg.split("COMMAND=", 1)[1].strip()
                a("medium", "system_log", f"sudo by {user}", cmd, data={"user": user, "command": redact(cmd, 300)})
        elif ident.startswith("sshd"):
            if msg.startswith("Accepted"):
                a("high", "system_log", "SSH login", msg)
            elif msg.startswith(("Failed password", "Invalid user", "Failed publickey")):
                a("high", "system_log", "SSH login failed", msg, fingerprint="ssh-failed")
        elif ident == "systemd-logind" and msg.startswith("New session"):
            a("info", "system_log", "New login session", msg)


# ---------------------------------------------------------------- Trioditis refusals

class TrioditisSecurityWatcher:
    """Requests Trioditis refused (wrong or missing API key), from its logs/security.jsonl."""

    def __init__(self, path: Path | None, alerter: Alerter):
        from .sources import Tail
        self.tail = Tail(path)
        self.alerter = alerter

    def poll(self) -> list[dict]:
        for ln in self.tail.lines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            client = str(r.get("client") or "?")[:60]
            local = client in ("127.0.0.1", "::1", "localhost") or client.startswith("172.17.")
            self.alerter.raise_("medium" if local else "high", "refused_access",
                                f"Trioditis refused a request ({r.get('reason', 'refused')})",
                                f"{r.get('method', '?')} {str(r.get('path', '?'))[:120]} from {client}",
                                fingerprint="sb-" + hashlib.sha256(f"{client}{r.get('reason')}".encode()).hexdigest()[:12],
                                data={"client": client})
        return []
