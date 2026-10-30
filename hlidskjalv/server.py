"""Local web server: serves Hlidskjalv page and streams events to it.

Security: binds loopback only, rejects foreign Host headers (DNS rebinding)
and never serves anything from the watched project. Reading is open to the
local machine; every change (settings, restart, the terminal) needs this
page's own Origin plus the owner key (see auth.py). Writes must also be JSON:
a cross-site page can't send that without a CORS preflight, which is never
answered.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from itertools import islice
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .auth import Owner, write_private
from .settings import Settings, SettingsError
from .sso import SsoError
from .sync import SyncError
from .terminal import Terminal, WebSocket, accept_key

SESSION_NAME = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9._-]{1,60}\.jsonl$")
HEALTH_EVERY_S = 15.0  # source health on the event stream
MAX_EVENTS = 20_000
MAX_POST = 64 * 1024

# Static files served: the page's script (static/app.js) and these vendored terminal files (see static/vendor/README.md).
VENDOR = {"xterm.js": "text/javascript; charset=utf-8", "addon-fit.js": "text/javascript; charset=utf-8",
          "xterm.css": "text/css; charset=utf-8", "qrcode.js": "text/javascript; charset=utf-8"}


NICE_HOST = "hlidskjalv.localhost"
# The logo as icons. SVG for Chrome, Edge and Firefox; PNG for Safari (no SVG favicons) and for
# iOS home screens; a one-colour SVG for Safari's pinned tabs. PNGs were rendered from the SVG.
ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect x='2' y='2' width='28' height='28' rx='8' fill='#3b5bdb'/>"
            "<path d='M9 21l4-10 3 7 2-4 5 7' fill='none' stroke='#fff' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>")
MASK_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect x='2' y='2' width='28' height='28' rx='8'/>"
            "<path d='M9 21l4-10 3 7 2-4 5 7' fill='none' stroke='#fff' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>")
ICON_PNG_32 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABRElEQVR42mNgGAU4gHX0bQMgng/E94H4P5n4PtQMA1IsFgDifgosxYVBZgoQY/l5GlgOw+fxOoJGPscICXxx/p9O2ACbA+bT0QHzsTngPh0dcB+bA4jSnFD1CIwpdQRZDgBZDAOUOoIsB2w79AnuABCbrg5wT737//PXv/+RAUiM3Ggh2QEtM178Rwdz1779n9PyBM4HsWnmgFsPfoAtAYXCs1e/wGwQfe7aN7gDQHxYqFDVAciJDxT3IJ/jAofOfKG+A5ATH8gxIF+iA1iogAAouqjmAOTEB4oGbI4CRUNIwQO4OhAN4lPFAciJD9lnyNECS3zIapEdS5EDkBMfegLDlv1AaQA5l1DkAPTER0ziAjkSOT3gKh9IdgApBQ1y2UCRAyipfAjpG5TV8YA3SAa2STbgjdJB0Swf8I7JoOiajTgAAGsakyxUKeJdAAAAAElFTkSuQmCC")  # noqa: E501 -- embedded icon
ICON_PNG_180 = base64.b64decode("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")  # noqa: E501 -- embedded icon
ICONS = {"/icon.svg": ("image/svg+xml", ICON_SVG.encode()), "/mask.svg": ("image/svg+xml", MASK_SVG.encode()),
         "/icon-32.png": ("image/png", ICON_PNG_32), "/icon-180.png": ("image/png", ICON_PNG_180)}


def csp(port: int) -> str:
    # No [::1]: the server binds 127.0.0.1 only, and WebKit rejects an IPv6 literal in a CSP source list.
    ws = " ".join(f"ws://{h}:{port}" for h in ("127.0.0.1", "localhost", NICE_HOST))
    # script-src 'self': the page's script is static/app.js, so an injected inline script or handler
    # cannot run. Styles keep 'unsafe-inline' for now (the style="" attributes the page sets).
    return ("default-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src https://fonts.gstatic.com; connect-src 'self' {ws}; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def private_file(path: Path):
    """Open for append, creating with owner-only permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1)


# Same as XP_RULES / xpOf in the page; a test keeps the two identical.
XP_RULES = {"file_created": 10, "file_changed": 5, "file_deleted": 2, "command": 3, "commit": 50, "branch": 10, "ai_call": 2}
HISTORY_EVENTS = 3000  # how much of earlier sessions the live view starts with ...
HISTORY_DAYS = 7  # ... and no older than this


def xp_of(ev: dict) -> int:
    if ev.get("actor") == "ai":
        return 0
    return XP_RULES.get(ev.get("type"), 0) + (3 if ev.get("type") == "ai_call" and ev.get("local") else 0)


def load_history(folder: Path, limit: int = HISTORY_EVENTS, days: int = HISTORY_DAYS) -> list[dict]:
    """Events of earlier sessions, newest files first until `limit`, in time order: a restart
    (Settings do one) must not wipe the activity log."""
    out: list[dict] = []
    oldest = time.time() - days * 86400
    for s in list_sessions(folder):
        if len(out) >= limit:
            break
        evs = [e for e in (read_session(folder, s["id"]) or []) if e.get("type") != "snapshot" and e.get("ts", 0) >= oldest]
        out = evs[-(limit - len(out)):] + out if evs else out
    return out


class Progress:
    """Lifetime XP, in ~/.hlidskjalv/progress.json: the sessions the activity log starts with are
    pruned after a while, but what you earned stays."""

    def __init__(self, home: Path, sessions: Path):
        self.path = home / "progress.json"
        self.lock = threading.Lock()
        self._saved = 0.0
        self._timer: threading.Timer | None = None
        self.academy: dict = {}  # finished Learn levels (moved here from the browser so sync can carry them)
        self.remote_xp = 0  # XP earned on your other computers (sync)
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.xp, self.events, self.since = int(d["xp"]), int(d["events"]), float(d["since"])
            self.academy = {str(k): True for k, v in (d.get("academy") or {}).items() if v}
            self.remote_xp = int(d.get("remote_xp") or 0)
        except (OSError, ValueError, KeyError, TypeError):
            # First run with a ledger: count what the kept sessions already hold.
            self.xp = self.events = 0
            self.since = time.time()
            for s in list_sessions(sessions):
                for e in read_session(sessions, s["id"]) or []:
                    if e.get("type") != "snapshot":
                        self.xp += xp_of(e)
                        self.events += 1
                        self.since = min(self.since, float(e.get("ts") or self.since))
            self.save()

    def add(self, ev: dict) -> None:
        with self.lock:
            self.xp += xp_of(ev)
            self.events += 1
            if time.time() - self._saved > 5:
                self.save()
            elif self._timer is None:  # a burst of events: one write, a few seconds later
                self._timer = threading.Timer(5, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self.lock:
            self._timer = None
            self.save()

    def save(self) -> None:
        try:
            write_private(self.path, json.dumps({"xp": self.xp, "events": self.events, "since": self.since,
                                                 "academy": self.academy, "remote_xp": self.remote_xp}))
        except OSError:
            pass
        self._saved = time.time()


class Hub:
    """Holds this session's events (after those of recent earlier sessions), writes the new ones to
    the session file, wakes listeners."""

    def __init__(self, project: str, snapshot: list[dict], session_path: Path | None, meta: dict,
                 history: list[dict] | None = None, progress: Progress | None = None):
        self.cond = threading.Condition()
        hist = [{**e, "id": i + 1} for i, e in enumerate((history or [])[-MAX_EVENTS:])]
        # A ring: the oldest events fall off; ids keep counting; the session file keeps everything.
        self.events: deque = deque(hist, maxlen=MAX_EVENTS)
        self.next_id = len(hist) + 1
        self.history_n = len(hist)
        self.progress = progress
        self.boot = round(time.time(), 3)  # a restarted Hlidskjalv has a new stamp: the page reloads on it
        self.health: Callable[[], dict] | None = None  # per-source poll health (poller.py), for /api/state and the stream
        self.snapshot = {"type": "snapshot", "ts": time.time(), "project": project,
                         "files": snapshot, "boot": self.boot, **meta}
        self.out = private_file(session_path) if session_path else None
        if self.out:
            self.out.write(json.dumps(self.snapshot, ensure_ascii=False) + "\n")

    def publish(self, ev: dict) -> None:
        with self.cond:
            ev = {"id": self.next_id, "ts": round(time.time(), 3), **ev}
            self.next_id += 1
            self.events.append(ev)
            if self.out:
                self.out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            if self.progress:
                self.progress.add(ev)
            self.cond.notify_all()

    def archived_xp(self) -> int:
        """XP earned before the events the live view holds (lifetime minus what is loaded)."""
        if not self.progress:
            return 0
        return max(0, self.progress.xp - sum(xp_of(e) for e in self.events)) + self.progress.remote_xp

    def after(self, last_id: int, timeout: float) -> list[dict]:
        """Events newer than last_id. If those fell off the ring, everything held, the first marked
        gap=True: the page then reloads the state instead of missing events silently."""
        with self.cond:
            if self.next_id - 1 <= last_id:
                self.cond.wait(timeout)
            if not self.events or self.events[-1]["id"] <= last_id:
                return []
            first = self.events[0]["id"]
            if last_id < first - 1:
                out = list(self.events)
                out[0] = {**out[0], "gap": True}
                return out
            return list(islice(self.events, last_id + 1 - first, None))

    def close(self) -> None:
        if self.out:
            self.out.close()
            self.out = None


def list_sessions(folder: Path) -> list[dict]:
    out = []
    for p in sorted(folder.glob("*.jsonl"), reverse=True):
        if SESSION_NAME.match(p.name):
            st = p.stat()
            out.append({"id": p.name, "bytes": st.st_size, "modified": st.st_mtime})
    return out


def read_session(folder: Path, sid: str) -> list[dict] | None:
    if not SESSION_NAME.match(sid):
        return None
    # The file is picked from the folder's own listing by exact name, never built from the request,
    # so no value from the client ever becomes part of a path.
    path = next((p for p in folder.iterdir() if p.name == sid and p.is_file()), None) if folder.is_dir() else None
    if path is None:
        return None
    events = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
            if len(events) > MAX_EVENTS:
                break
    return events


class UsageContext:
    """What the cost and usage endpoints read: ~/.hlidskjalv and Claude Code's transcripts for this project."""

    def __init__(self, home: Path, claude):
        self.home, self.claude = home, claude


class AlertsContext:
    """What the alert endpoints need: the publisher, the history file, acknowledgements, journal status."""

    def __init__(self, alerter, history: Path, acks, system_log=None):
        self.alerter, self.history, self.acks, self.system_log = alerter, history, acks, system_log


def make_handler(hub: Hub, sessions: Path, port: int, terminal: Terminal | None = None,
                 owner: Owner | None = None, settings: Settings | None = None, restart=None,
                 alerts: AlertsContext | None = None, usage: UsageContext | None = None, syncer=None, sso=None):
    def refused(severity: str, title: str, detail: str, fp: str):
        if alerts:
            alerts.alerter.raise_(severity, "refused_access", title, detail[:300], fingerprint=fp)

    def autostart_status():
        from .autostart import status
        return status(sessions.parent)

    # The page file is written without <html>/<head> (it's also published as a
    # hosted demo, which adds them); add them here for the local server.
    page = (b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            b'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            b'<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">'
            b'<link rel="icon" type="image/svg+xml" href="/icon.svg">'
            b'<link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png">'
            b'<link rel="mask-icon" href="/mask.svg" color="#3b5bdb">'
            b'<meta name="theme-color" content="#3b5bdb"></head><body>'
            + resources.files("hlidskjalv").joinpath("static/index.html").read_bytes()
            + b"</body></html>")
    app_js = resources.files("hlidskjalv").joinpath("static/app.js").read_bytes()
    # *.localhost resolves to loopback in Chrome, Edge and Firefox without any setup.
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"{NICE_HOST}:{port}"}
    allowed_origins = {"http://" + h for h in allowed_hosts}
    policy = csp(port)

    class Handler(BaseHTTPRequestHandler):
        server_version = "hlidskjalv"
        sys_version = ""
        timeout = 60  # a half-open local connection can't hold a thread forever (the terminal lifts this once paired)

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _headers(self, status: int, ctype: str, length: int | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", policy)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _json(self, obj, status=200):
            data = json.dumps(obj, ensure_ascii=False).encode()
            self._headers(status, "application/json; charset=utf-8", len(data))
            self.wfile.write(data)

        def _foreign_host(self) -> bool:
            host = self.headers.get("Host", "")
            if host in allowed_hosts:
                return False
            refused("high", "Blocked a request for another host name",
                    f"Host: {host[:100]!r}. A web page using DNS rebinding looks like this; so does typing another name for this computer.",
                    "host-" + host[:60])
            self._json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
            return True

        def _foreign_origin(self, what: str) -> bool:
            origin = self.headers.get("Origin")
            if origin in allowed_origins:
                return False
            refused("high", f"A web page tried to {what}", f"Origin: {str(origin)[:100]!r}. Only this Hlidskjalv's own page may do that.",
                    "origin-" + str(origin)[:60])
            self._json({"error": "forbidden origin"}, HTTPStatus.FORBIDDEN)
            return True

        def do_GET(self):
            if self._foreign_host():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/":
                self._headers(200, "text/html; charset=utf-8", len(page))
                self.wfile.write(page)
            elif url.path == "/api/state":
                with hub.cond:
                    self._json({"snapshot": hub.snapshot, "events": list(hub.events),
                                "sources": hub.health() if hub.health else {},
                                "progress": {"archived_xp": hub.archived_xp(), "history": hub.history_n,
                                             "academy": dict(hub.progress.academy) if hub.progress else {}}})
            elif url.path == "/api/events":
                after = (q.get("after") or ["0"])[0]
                if not after.isdigit():
                    return self._json({"error": "after must be a number"}, HTTPStatus.BAD_REQUEST)
                self._stream(int(after))
            elif url.path == "/api/sessions":
                self._json(list_sessions(sessions))
            elif url.path in ICONS:
                ctype, data = ICONS[url.path]
                self._headers(200, ctype, len(data))
                self.wfile.write(data)
            elif url.path == "/static/app.js":
                self._headers(200, "text/javascript; charset=utf-8", len(app_js))
                self.wfile.write(app_js)
            elif url.path.startswith("/static/vendor/") and url.path[15:] in VENDOR:
                data = resources.files("hlidskjalv").joinpath("static/vendor/" + url.path[15:]).read_bytes()
                self._headers(200, VENDOR[url.path[15:]], len(data))
                self.wfile.write(data)
            elif url.path == "/api/term":
                self._terminal()
            elif url.path in ("/api/budget", "/api/hardware", "/api/claude-usage", "/api/accounts"):
                from . import usage as u
                if usage is None:
                    return self._json({"available": False})
                if url.path == "/api/budget":
                    self._json(u.budget(usage.home))
                elif url.path == "/api/accounts":
                    self._json(u.accounts(usage.home))
                elif url.path == "/api/hardware":
                    self._json(u.hardware(usage.home))
                else:
                    if usage.claude and settings is not None:
                        anchor = settings.data.get("usage", {}).get("week_anchor")
                        usage.claude.week_anchor = u._epoch(anchor) if anchor else None
                    self._json(usage.claude.report() if usage.claude else {"available": False, "sessions": []})
            elif url.path == "/api/alerts":
                if alerts is None:
                    return self._json({"alerts": [], "acks": [], "system_log": None})
                from .alerts import read_history
                self._json({"alerts": read_history(alerts.history), "acks": alerts.acks.ids,
                            "system_log": alerts.system_log() if alerts.system_log else None})
            elif url.path == "/api/settings":
                if settings is None:
                    return self._json({"error": "settings are off"}, HTTPStatus.NOT_FOUND)
                self._json({"settings": settings.data, "password_set": bool(owner and owner.has_password()),
                            "totp": bool(owner and owner.totp_enabled()), "system": platform.system()})
            elif url.path == "/api/sync":
                self._json(syncer.status() if syncer else {"configured": False, "available": False})
            elif url.path == "/api/sso":  # which providers can pair a browser: what an unpaired page may know
                self._json({"providers": sso.available() if sso else {}})
            elif url.path == "/api/sso/poll":
                flow = sso.poll((q.get("id") or [""])[0]) if sso else None
                if flow is None:
                    return self._json({"error": "unknown sign-in"}, HTTPStatus.NOT_FOUND)
                if flow["status"] == "ok" and flow["purpose"] == "pair" and owner is not None:
                    done = sso.consume((q.get("id") or [""])[0])
                    if done:
                        flow["token"] = owner.start_session(second_factor=True)
                        flow["expires_in"] = int(owner.ttl_s)
                        flow["password_set"] = owner.has_password()
                self._json(flow)
            elif url.path == "/api/session":
                events = read_session(sessions, (q.get("id") or [""])[0])
                if events is None:
                    return self._json({"error": "unknown session"}, HTTPStatus.NOT_FOUND)
                self._json(events)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _terminal(self):
            if terminal is None:
                return self._json({"error": "terminal is off; start with --terminal"}, HTTPStatus.NOT_FOUND)
            # Browsers don't apply CORS to WebSockets: only our own page may connect.
            if self._foreign_origin("open the terminal"):
                return
            key = self.headers.get("Sec-WebSocket-Key", "")
            if (self.headers.get("Upgrade", "").lower() != "websocket"
                    or "upgrade" not in self.headers.get("Connection", "").lower() or len(key) != 24):
                return self._json({"error": "websocket upgrade required"}, HTTPStatus.BAD_REQUEST)
            # The handshake reply must say HTTP/1.1: Firefox drops a "HTTP/1.0 101" at once
            # (the browser then reports "connection lost"); Chromium happens to accept it.
            self.protocol_version = "HTTP/1.1"
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            try:
                terminal.run(WebSocket(self.rfile, self.wfile, self.connection), self.connection)
            except (ConnectionError, OSError):
                pass

        def _stream(self, last: int):
            self._headers(200, "text/event-stream")
            try:
                self.wfile.write(f"event: hello\ndata: {json.dumps({'boot': hub.boot})}\n\n".encode())  # which process this is
                self.wfile.flush()
                next_health = time.monotonic() + HEALTH_EVERY_S
                while True:
                    new = hub.after(last, timeout=15)
                    if not new:
                        self.wfile.write(b": keep-alive\n\n")
                    for ev in new:
                        self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        last = ev["id"]
                    if hub.health and time.monotonic() >= next_health:  # how each source is doing, now and then
                        next_health = time.monotonic() + HEALTH_EVERY_S
                        self.wfile.write(f"event: health\ndata: {json.dumps(hub.health())}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        def do_POST(self):
            if self._foreign_host():
                return
            path = urlparse(self.path).path
            if path not in ("/api/pair", "/api/ping", "/api/logout", "/api/password", "/api/settings", "/api/restart",
                            "/api/autostart", "/api/alerts/ack", "/api/2fa/begin", "/api/2fa/confirm", "/api/2fa/disable",
                            "/api/2fa/verify", "/api/sync/setup", "/api/sync/now", "/api/sync/off", "/api/progress",
                            "/api/sso/start", "/api/sso/configure", "/api/sso/unlink", "/api/sso/info") or owner is None:
                return self._json({"error": "read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)
            if self._foreign_origin("change Hlidskjalv"):
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._json({"error": "send JSON"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > MAX_POST:
                return self._json({"error": "body missing or too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                body = json.loads(self.rfile.read(int(length)) or b"{}")
            except ValueError:
                return self._json({"error": "body must be JSON"}, HTTPStatus.BAD_REQUEST)
            if not isinstance(body, dict):
                return self._json({"error": "body must be a JSON object"}, HTTPStatus.BAD_REQUEST)

            if path == "/api/pair":
                if "password" in body:
                    if owner.totp_enabled() and not body.get("code"):
                        return self._json({"error": "second-factor code needed", "needs_code": True}, HTTPStatus.FORBIDDEN)
                    token = owner.login(body.get("password"), body.get("code"))
                    if not token:
                        wait = owner.password_lock_s()
                        refused("medium", "A wrong password was entered",
                                "Pairing with a password needs the one set with `python3 -m hlidskjalv password`."
                                if owner.has_password() else "No password is set: pair with the link once, then set one in Settings > Pairing.",
                                "password")
                        return self._json({"error": ("no password is set" if not owner.has_password() else
                                                     "wrong password or code" if owner.totp_enabled() else "wrong password")
                                           + (f"; wait {wait} s" if wait else ""), "wait": wait}, HTTPStatus.FORBIDDEN)
                else:
                    token = owner.redeem(body.get("code"))
                    if not token:
                        refused("medium", "A pairing code was refused",
                                "It was wrong, already used or expired. Opening an old pairing link twice does this too.", "pair")
                        return self._json({"error": "pairing code is wrong, used or expired"}, HTTPStatus.FORBIDDEN)
                return self._json({"token": token, "expires_in": int(owner.ttl_s), "password_set": owner.has_password()})
            if path == "/api/sso/start" and body.get("purpose") != "link":  # pairing: no session yet, by design
                if sso is None:
                    return self._json({"error": "sign-in isn't available here"}, HTTPStatus.NOT_FOUND)
                try:
                    return self._json(sso.start(str(body.get("provider") or ""), "pair"))
                except SsoError as e:
                    refused("info", "A provider sign-in couldn't start", str(e), "sso-start")
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            given = self.headers.get("X-Hlidskjalv-Key")
            if not owner.check(given):
                if given:
                    refused("medium", "A request with an ended or wrong session was refused",
                            f"{path}. A browser whose session ran out, or paired before `pair --rotate`, does this once; "
                            "anything else is someone guessing.", "key")
                return self._json({"error": "this browser isn't paired, or its session ended"}, HTTPStatus.UNAUTHORIZED)
            if path == "/api/ping":
                info = owner.session_info(given) or {}
                return self._json({"ok": True, "expires_in": info.get("expires_in"), "password_set": owner.has_password(),
                                   "totp": owner.totp_enabled(), "fresh": owner.fresh_factor(given)})

            def step_up_needed():  # security changes and root shells want a code from the app within STEP_UP_S
                if owner.fresh_factor(given):
                    return None
                return self._json({"error": "second-factor code needed", "step_up": True}, HTTPStatus.FORBIDDEN)

            if path.startswith("/api/sso/"):
                if sso is None:
                    return self._json({"error": "sign-in isn't available here"}, HTTPStatus.NOT_FOUND)
                if path == "/api/sso/info":
                    return self._json({"providers": sso.status()})
                if (r := step_up_needed()) is not None:  # linking, configuring and unlinking are security changes
                    return r
                try:
                    if path == "/api/sso/start":
                        return self._json(sso.start(str(body.get("provider") or ""), "link"))
                    if path == "/api/sso/configure":
                        sso.configure(str(body.get("provider") or ""), body.get("client_id"), body.get("client_secret"))
                    elif path == "/api/sso/unlink":
                        sso.unlink(str(body.get("provider") or ""))
                except SsoError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json({"providers": sso.status()})
            if path == "/api/progress":  # finished Learn levels, kept here so sync can carry them
                academy = body.get("academy")
                if hub.progress is not None and isinstance(academy, dict):
                    for k, v in academy.items():
                        if v and isinstance(k, str) and len(k) <= 40:
                            hub.progress.academy[k] = True
                    hub.progress.save()
                return self._json({"academy": dict(hub.progress.academy) if hub.progress else {}})
            if path.startswith("/api/sync/"):
                if syncer is None:
                    return self._json({"error": "sync isn't available here"}, HTTPStatus.NOT_FOUND)
                if path in ("/api/sync/setup", "/api/sync/off") and (r := step_up_needed()) is not None:
                    return r
                try:
                    if path == "/api/sync/setup":
                        st = syncer.setup(str(body.get("kind") or ""), body.get("passphrase"), body.get("device"),
                                          path=body.get("path"), token=body.get("token"), gist_id=body.get("gist_id"))
                    elif path == "/api/sync/now":
                        st = syncer.sync_now()
                    else:
                        syncer.disconnect()
                        st = syncer.status()
                except SyncError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json(st)
            if path == "/api/2fa/begin":
                import getpass
                import socket as _socket
                return self._json(owner.totp_begin(f"{getpass.getuser()}@{_socket.gethostname()}"))
            if path == "/api/2fa/confirm":
                codes = owner.totp_confirm(body.get("code"))
                if codes is None:
                    refused("medium", "A wrong second-factor code was entered", "While setting up the authenticator app.", "totp")
                    return self._json({"error": "that code didn't match; try the next one the app shows", "wait": owner.password_lock_s()},
                                      HTTPStatus.FORBIDDEN)
                owner.mark_fresh(given)
                if alerts:
                    alerts.alerter.raise_("medium", "security", "Two-factor authentication switched on",
                                          "Pairing with the password now also needs a code from the authenticator app.",
                                          fingerprint="totp-on", expected=True)
                return self._json({"recovery": codes})
            if path == "/api/2fa/disable":
                if not owner.totp_verify(body.get("code")):
                    refused("medium", "A wrong second-factor code was entered", "While switching two-factor authentication off.", "totp")
                    return self._json({"error": "wrong code", "wait": owner.password_lock_s()}, HTTPStatus.FORBIDDEN)
                owner.totp_disable()
                if alerts:
                    alerts.alerter.raise_("medium", "security", "Two-factor authentication switched off",
                                          "Pairing with the password alone works again.", fingerprint="totp-off", expected=True)
                return self._json({"totp": False})
            if path == "/api/2fa/verify":
                if not owner.step_up(given, body.get("code")):
                    refused("medium", "A wrong second-factor code was entered", f"{path}", "totp")
                    return self._json({"error": "wrong code", "wait": owner.password_lock_s()}, HTTPStatus.FORBIDDEN)
                return self._json({"fresh": True})
            if path == "/api/logout":
                owner.end_session(given)
                return self._json({"ok": True})
            if path == "/api/password":
                if (r := step_up_needed()) is not None:
                    return r
                if owner.has_password() and not owner.check_password(body.get("current")):
                    refused("medium", "A wrong password was entered", "Changing the password needs the current one.", "password")
                    return self._json({"error": "the current password is wrong"}, HTTPStatus.FORBIDDEN)
                try:
                    owner.set_password(body.get("new") or None)
                except ValueError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json({"password_set": owner.has_password()})
            if path == "/api/settings":
                if settings is None:
                    return self._json({"error": "settings are off"}, HTTPStatus.NOT_FOUND)
                change = body.get("change") if isinstance(body.get("change"), dict) else {}
                feats = change.get("features") if isinstance(change.get("features"), dict) else {}
                root_change = feats.get("terminal_root")
                if root_change is not None:  # a privilege change: the guard rails live here, not in the schema
                    if (r := step_up_needed()) is not None:
                        return r
                    if root_change is True and not owner.has_password():
                        return self._json({"error": "set a pairing password first (Settings > Pairing): "
                                                    "a root-capable shell deserves more than a one-time link"},
                                          HTTPStatus.BAD_REQUEST)
                    if root_change is True:
                        from .terminal import sudo_passwordless
                        if sudo_passwordless():
                            return self._json({"error": "sudo works without a password on this computer, so root in the browser "
                                                        "would need no password at all; refused until sudo asks for one"},
                                              HTTPStatus.BAD_REQUEST)
                try:
                    if body.get("reset") is True:
                        return self._json({"settings": settings.reset(), "restart": True})
                    data, again = settings.update(change)
                except SettingsError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                except OSError:
                    return self._json({"error": "couldn't save settings"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                if root_change is not None and alerts:
                    alerts.alerter.raise_("medium", "security",
                                          "Root allowed in the Terminal tab" if root_change else "Root no longer allowed in the Terminal tab",
                                          "The shell in the browser may use sudo (with your password); Hlidskjalv itself stays sandboxed."
                                          if root_change else "The shell in the browser can't elevate any more.",
                                          fingerprint="root-toggle", expected=True)
                return self._json({"settings": data, "restart": again})
            if path == "/api/alerts/ack":
                if alerts is None:
                    return self._json({"error": "alerts are off"}, HTTPStatus.NOT_FOUND)
                ids = body.get("ids")
                if not isinstance(ids, list):
                    return self._json({"error": "ids must be a list"}, HTTPStatus.BAD_REQUEST)
                try:
                    return self._json({"acks": alerts.acks.add(ids)})
                except OSError:
                    return self._json({"error": "couldn't save"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            if path == "/api/autostart":  # owner-only: it includes recent log lines
                return self._json(autostart_status())
            if path == "/api/restart":
                if restart is None:
                    return self._json({"error": "restart isn't available here"}, HTTPStatus.NOT_FOUND)
                self._json({"restarting": True}, HTTPStatus.ACCEPTED)
                threading.Timer(0.3, restart).start()  # after this answer is sent

        def do_PUT(self):
            self._json({"error": "read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)

        do_DELETE = do_PATCH = do_PUT

    return Handler


def serve(hub: Hub, sessions: Path, port: int, terminal: Terminal | None = None,
          owner: Owner | None = None, settings: Settings | None = None, restart=None,
          alerts: AlertsContext | None = None, usage: UsageContext | None = None, syncer=None, sso=None) -> ThreadingHTTPServer:
    if owner is not None and settings is not None:
        owner.ttl_provider = lambda: settings.data["security"]["session_minutes"] * 60
    httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                make_handler(hub, sessions, port, terminal, owner, settings, restart, alerts, usage, syncer, sso))
    httpd.daemon_threads = True
    return httpd
