"""Local web server: serves the Arcade page and streams events to it.

Security: binds loopback only, rejects foreign Host headers (DNS rebinding)
and never serves anything from the watched project. Reading is open to the
local machine; every change (settings, restart, the terminal) needs this
page's own Origin plus the owner key (see auth.py). Writes must also be JSON:
a cross-site page can't send that without a CORS preflight, which is never
answered.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .auth import Owner
from .settings import Settings, SettingsError
from .terminal import Terminal, WebSocket, accept_key

SESSION_NAME = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9._-]{1,60}\.jsonl$")
MAX_EVENTS = 20_000
MAX_POST = 64 * 1024

# Vendored terminal files (see static/vendor/README.md): the only static files served.
VENDOR = {"xterm.js": "text/javascript; charset=utf-8", "addon-fit.js": "text/javascript; charset=utf-8",
          "xterm.css": "text/css; charset=utf-8"}


def csp(port: int) -> str:
    ws = " ".join(f"ws://{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]"))
    return ("default-src 'none'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src https://fonts.gstatic.com; connect-src 'self' {ws}; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def private_file(path: Path):
    """Open for append, creating with owner-only permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1)


class Hub:
    """Holds this session's events, writes them to the session file, wakes listeners."""

    def __init__(self, project: str, snapshot: list[dict], session_path: Path | None, meta: dict):
        self.cond = threading.Condition()
        self.events: list[dict] = []
        self.snapshot = {"type": "snapshot", "ts": time.time(), "project": project,
                         "files": snapshot, **meta}
        self.out = private_file(session_path) if session_path else None
        if self.out:
            self.out.write(json.dumps(self.snapshot, ensure_ascii=False) + "\n")

    def publish(self, ev: dict) -> None:
        with self.cond:
            ev = {"id": len(self.events) + 1, "ts": round(time.time(), 3), **ev}
            if len(self.events) < MAX_EVENTS:
                self.events.append(ev)
            if self.out:
                self.out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.cond.notify_all()

    def after(self, last_id: int, timeout: float) -> list[dict]:
        with self.cond:
            if len(self.events) <= last_id:
                self.cond.wait(timeout)
            return self.events[last_id:]


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
    path = folder / sid
    if path.parent.resolve() != folder.resolve() or not path.is_file():
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


class AlertsContext:
    """What the alert endpoints need: the publisher, the history file, acknowledgements, journal status."""

    def __init__(self, alerter, history: Path, acks, system_log=None):
        self.alerter, self.history, self.acks, self.system_log = alerter, history, acks, system_log


def make_handler(hub: Hub, sessions: Path, port: int, terminal: Terminal | None = None,
                 owner: Owner | None = None, settings: Settings | None = None, restart=None,
                 alerts: AlertsContext | None = None):
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
            b'<link rel="icon" href="data:,"></head><body>'
            + resources.files("workflow_arcade").joinpath("static/index.html").read_bytes()
            + b"</body></html>")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    allowed_origins = {"http://" + h for h in allowed_hosts}
    policy = csp(port)

    class Handler(BaseHTTPRequestHandler):
        server_version = "arcade"
        sys_version = ""

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
            refused("high", f"A web page tried to {what}", f"Origin: {str(origin)[:100]!r}. Only this Arcade's own page may do that.",
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
                    self._json({"snapshot": hub.snapshot, "events": list(hub.events)})
            elif url.path == "/api/events":
                self._stream(int((q.get("after") or ["0"])[0] or 0))
            elif url.path == "/api/sessions":
                self._json(list_sessions(sessions))
            elif url.path.startswith("/static/vendor/") and url.path[15:] in VENDOR:
                data = resources.files("workflow_arcade").joinpath("static/vendor/" + url.path[15:]).read_bytes()
                self._headers(200, VENDOR[url.path[15:]], len(data))
                self.wfile.write(data)
            elif url.path == "/api/term":
                self._terminal()
            elif url.path == "/api/alerts":
                if alerts is None:
                    return self._json({"alerts": [], "acks": [], "system_log": None})
                from .alerts import read_history
                self._json({"alerts": read_history(alerts.history), "acks": alerts.acks.ids,
                            "system_log": alerts.system_log() if alerts.system_log else None})
            elif url.path == "/api/settings":
                if settings is None:
                    return self._json({"error": "settings are off"}, HTTPStatus.NOT_FOUND)
                self._json({"settings": settings.data})
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
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            try:
                terminal.run(WebSocket(self.rfile, self.wfile), self.connection)
            except (ConnectionError, OSError):
                pass

        def _stream(self, last: int):
            self._headers(200, "text/event-stream")
            try:
                while True:
                    new = hub.after(last, timeout=15)
                    if not new:
                        self.wfile.write(b": keep-alive\n\n")
                    for ev in new:
                        self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        last = ev["id"]
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        def do_POST(self):
            if self._foreign_host():
                return
            path = urlparse(self.path).path
            if path not in ("/api/pair", "/api/ping", "/api/settings", "/api/restart", "/api/autostart",
                            "/api/alerts/ack") or owner is None:
                return self._json({"error": "read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)
            if self._foreign_origin("change the Arcade"):
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
                key = owner.redeem(body.get("code"))
                if not key:
                    refused("medium", "A pairing code was refused", "It was wrong, already used or expired. Opening an old pairing link twice does this too.", "pair")
                    return self._json({"error": "pairing code is wrong, used or expired"}, HTTPStatus.FORBIDDEN)
                return self._json({"key": key})
            if not owner.check(self.headers.get("X-Arcade-Key")):
                if self.headers.get("X-Arcade-Key"):
                    refused("medium", "A request with a wrong owner key was refused",
                            f"{path}. A browser paired before `pair --rotate` does this once; anything else is someone guessing.", "key")
                return self._json({"error": "this browser isn't paired"}, HTTPStatus.UNAUTHORIZED)
            if path == "/api/ping":
                return self._json({"ok": True})
            if path == "/api/settings":
                if settings is None:
                    return self._json({"error": "settings are off"}, HTTPStatus.NOT_FOUND)
                try:
                    if body.get("reset") is True:
                        return self._json({"settings": settings.reset(), "restart": True})
                    data, again = settings.update(body.get("change"))
                except SettingsError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                except OSError:
                    return self._json({"error": "couldn't save settings"}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
          alerts: AlertsContext | None = None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                make_handler(hub, sessions, port, terminal, owner, settings, restart, alerts))
    httpd.daemon_threads = True
    return httpd
