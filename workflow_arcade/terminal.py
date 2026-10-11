"""Opt-in terminal: a real shell in the browser, over a WebSocket (stdlib only).

Off unless `watch --terminal` is given. A shell in a web page is remote code
execution by design, so every connection must pass all of these:

- the server binds loopback only and checks the Host header (DNS rebinding);
- the Origin header must be this Arcade's own origin. Browsers don't apply
  CORS to WebSockets, so this is what stops other websites from connecting;
- the first message must carry the per-launch secret token. Loopback ports are
  reachable by every user and process on the computer, so Origin alone is not
  enough. The browser gets the token by trading a one-time pairing code from
  the link the Arcade opens: launch arguments are visible to other users
  (/proc/<pid>/cmdline), so the link itself must stop working once used;
- at most MAX_SESSIONS shells at a time; each one dies with its connection.

POSIX only (pty). On Windows the terminal reports itself unavailable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import signal
import struct
import threading

try:
    import fcntl
    import pty
    import termios
except ImportError:  # Windows
    pty = None

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1 << 20
MAX_SESSIONS = 4
AUTH_TIMEOUT_S = 10

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def supported() -> bool:
    return pty is not None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


class WebSocket:
    """Minimal RFC 6455 server side: masked client frames in, unmasked frames out."""

    def __init__(self, rfile, wfile):
        self.rfile, self.wfile = rfile, wfile
        self.lock = threading.Lock()
        self.closed = False

    def _read(self, n: int) -> bytes:
        data = self.rfile.read(n)
        if data is None or len(data) < n:
            raise ConnectionError("socket closed")
        return data

    def recv(self) -> tuple[int, bytes]:
        """Next complete message as (opcode, payload); answers pings itself."""
        message, first_op = b"", None
        while True:
            b1, b2 = self._read(2)
            fin, op, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if not masked:
                raise ConnectionError("client frames must be masked")
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if n > MAX_FRAME or len(message) + n > MAX_FRAME:
                raise ConnectionError("frame too large")
            mask = self._read(4)
            payload = _unmask(self._read(n), mask) if n else b""
            if op == OP_PING:
                self.send(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                return OP_CLOSE, payload
            if op != OP_CONT:
                first_op = op
            message += payload
            if fin:
                return first_op or OP_BIN, message

    def send(self, op: int, payload: bytes = b"") -> None:
        n = len(payload)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([n])
        elif n < 1 << 16:
            head += bytes([126]) + struct.pack("!H", n)
        else:
            head += bytes([127]) + struct.pack("!Q", n)
        with self.lock:
            if self.closed:
                return
            self.wfile.write(head + payload)
            self.wfile.flush()

    def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            self.send(OP_CLOSE, struct.pack("!H", code) + reason.encode()[:120])
        except OSError:
            pass
        self.closed = True


class Terminal:
    """Shells for one Arcade launch: the token, the folder they start in, the slot count."""

    def __init__(self, root, token: str | None = None):
        self.root = str(root)
        self.token = token or new_token()
        self.pair_code: str | None = new_token()  # one use, then None
        self.slots = threading.BoundedSemaphore(MAX_SESSIONS)
        self._lock = threading.Lock()

    def check_token(self, given) -> bool:
        return isinstance(given, str) and hmac.compare_digest(given.encode(), self.token.encode())

    def redeem(self, code) -> bool:
        """Trade the one-time pairing code for the token. Works exactly once."""
        with self._lock:
            if not isinstance(code, str) or not self.pair_code:
                return False
            if not hmac.compare_digest(code.encode(), self.pair_code.encode()):
                return False
            self.pair_code = None
            return True

    def shell(self) -> list[str]:
        sh = os.environ.get("SHELL") or "/bin/bash"
        if not os.path.isabs(sh) or not os.access(sh, os.X_OK):
            sh = "/bin/sh"
        return [sh, "-i"]

    def run(self, ws: WebSocket, sock) -> None:
        """Authenticate, then bridge the WebSocket and a fresh pty shell until either ends."""
        if not self.slots.acquire(blocking=False):
            return ws.close(1013, "too many terminals open")
        try:
            sock.settimeout(AUTH_TIMEOUT_S)
            try:
                op, data = ws.recv()
                hello = json.loads(data) if op == OP_TEXT else {}
            except (ValueError, OSError):
                hello = {}
            if not isinstance(hello, dict):
                return ws.close(1008, "bad token")
            if self.redeem(hello.get("pair")):
                ws.send(OP_TEXT, json.dumps({"token": self.token}).encode())
            elif not self.check_token(hello.get("token")):
                return ws.close(1008, "bad token")
            sock.settimeout(None)
            self._bridge(ws, hello.get("size"))
        finally:
            self.slots.release()

    def _bridge(self, ws: WebSocket, size) -> None:
        import subprocess
        master, slave = pty.openpty()
        _resize(master, size)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "ARCADE_TERMINAL": "1", "PWD": self.root}

        def take_tty():  # runs in the child: own session, pty as controlling terminal
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            proc = subprocess.Popen(self.shell(), stdin=slave, stdout=slave, stderr=slave, cwd=self.root,
                                    env=env, start_new_session=True, preexec_fn=take_tty, close_fds=True)
        except OSError as e:
            os.close(master), os.close(slave)
            ws.send(OP_TEXT, json.dumps({"error": f"couldn't start a shell: {e.strerror}"}).encode())
            return ws.close(1011)
        os.close(slave)

        def pump():  # shell -> browser
            try:
                while True:
                    data = os.read(master, 65536)
                    if not data:
                        break
                    ws.send(OP_BIN, data)
            except OSError:
                pass
            code = proc.wait()
            try:
                ws.send(OP_TEXT, json.dumps({"exit": code}).encode())
                ws.close(1000)
            except OSError:
                pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:  # browser -> shell
            while True:
                op, data = ws.recv()
                if op == OP_CLOSE:
                    break
                if op == OP_BIN:
                    os.write(master, data)
                elif op == OP_TEXT:
                    try:
                        msg = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and "size" in msg:
                        _resize(master, msg["size"])
        except (ConnectionError, OSError):
            pass
        finally:
            _stop(proc)
            reader.join(timeout=2)
            try:
                os.close(master)
            except OSError:
                pass
            ws.closed = True


def _unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def _resize(fd: int, size) -> None:
    try:
        cols, rows = (max(2, min(int(v), 1000)) for v in size)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (TypeError, ValueError, OSError):
        pass


def _stop(proc) -> None:
    for sig in (signal.SIGHUP, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=1.5)
        except Exception:
            pass
