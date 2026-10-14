"""Opt-in terminal: a real shell in the browser, over a WebSocket (stdlib only).

Off unless switched on (`watch --terminal`, or Settings). A shell in a web
page is remote code execution by design, so every connection must pass all of these:

- the server binds loopback only and checks the Host header (DNS rebinding);
- the Origin header must be this Hlidskjalv's own origin. Browsers don't apply
  CORS to WebSockets, so this is what stops other websites from connecting;
- the first message must carry the owner key (auth.py). Loopback ports are
  reachable by every user and process on the computer, so Origin alone is not
  enough;
- at most MAX_SESSIONS shells at a time; each one dies with its connection.

Linux and macOS use a pty; Windows 10 1809+ uses ConPTY (conpty.py, experimental).

Root (sudo) in the shell, Linux only, Settings > Features > terminal_root:
- off: the shell gets the kernel's no-new-privileges flag before it starts, so sudo
  and other setuid programs can't elevate, whether Hlidskjalv was autostarted or not;
- on: if Hlidskjalv itself carries that flag (the login service's sandbox sets it),
  the shell is started through the user service manager (systemd-run --user --pty),
  which the flag deliberately doesn't reach, so the service keeps its sandbox and only
  the person's shell can sudo, with the usual password prompt. Refused when sudo
  needs no password on this computer, or when the session hasn't proved its second
  factor recently (if one is set up).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import signal
import struct
import subprocess
import sys
import threading
from collections import namedtuple
from pathlib import Path

from .programs import which

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
_LINUX = platform.system() == "Linux"

Plan = namedtuple("Plan", "argv root why set_nnp unit")  # how the next shell starts


def sandboxed() -> bool:
    """Does this process carry the no-new-privileges flag (the login service's sandbox sets it)?"""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            return any(line.startswith("NoNewPrivs:") and line.split()[1] == "1" for line in f)
    except OSError:
        return False


def sudo_passwordless() -> bool:
    """True when sudo elevates without asking: then root in the browser would cost nothing."""
    sudo = which("sudo")
    if not sudo:
        return False
    try:
        return subprocess.run([sudo, "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def user_manager_ok() -> str | None:
    """Path of systemd-run when the user service manager is reachable, else None."""
    run = which("systemd-run")
    rt = os.environ.get("XDG_RUNTIME_DIR")
    return run if run and rt and os.path.exists(os.path.join(rt, "systemd", "private")) else None


OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def supported() -> bool:
    if pty is not None:
        return True
    from . import conpty
    return conpty.available()


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


class WebSocket:
    """Minimal RFC 6455 server side: masked client frames in, unmasked frames out."""

    def __init__(self, rfile, wfile, sock=None):
        self.rfile, self.wfile, self.sock = rfile, wfile, sock
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

    def close(self, code: int = 1000, reason: str = "", drain: bool = False) -> None:
        """Send a close frame. With drain (only from the thread that reads this socket), wait briefly
        for the browser's own close frame: dropping the connection with it still in flight makes
        the kernel reset the socket, and the browser then reports 1006 instead of our code."""
        try:
            self.send(OP_CLOSE, struct.pack("!H", code) + reason.encode()[:120])
        except OSError:
            pass
        if drain and self.sock is not None and not self.closed:
            try:
                self.sock.settimeout(1.0)
                for _ in range(8):
                    if self.recv()[0] == OP_CLOSE:
                        break
            except (OSError, ConnectionError, ValueError):
                pass
        self.closed = True


class Terminal:
    """Shells for one Hlidskjalv launch: who may open them, the folder they start in, the slot count."""

    def __init__(self, root, owner, on_refused=None, on_alert=None, root_allowed=None):
        self.root = str(root)
        self.owner = owner
        self.on_refused = on_refused or (lambda *a: None)
        self.on_alert = on_alert or (lambda *a, **k: None)  # (severity, source, title, detail, fingerprint, expected)
        self.root_allowed = root_allowed or (lambda: False)  # reads Settings live: no restart to toggle
        self.slots = threading.BoundedSemaphore(MAX_SESSIONS)
        self._n = 0

    def plan(self) -> Plan:
        """Decide how the next shell starts (see the module docstring). Linux only; elsewhere unchanged."""
        base = self.shell()
        if not _LINUX or pty is None:
            return Plan(base, None, "", False, None)
        if not self.root_allowed():
            return Plan(base, False, "root not allowed (Settings > Features)", True, None)
        if sudo_passwordless():
            return Plan(base, False, "sudo needs no password on this computer, so root in the browser is refused", True, None)
        if not sandboxed():
            return Plan(base, True, "", False, None)
        run = user_manager_ok()
        if not run:
            return Plan(base, False, "Hlidskjalv runs sandboxed and the user service manager isn't reachable", True, None)
        self._n += 1
        unit = f"hlidskjalv-shell-{self._n}-{os.getpid()}"
        argv = [run, "--user", "--quiet", "--pty", "--wait", "--collect", f"--unit={unit}",
                "--description=Hlidskjalv terminal shell (root allowed)", f"--working-directory={self.root}",
                "-E", "TERM=xterm-256color", "-E", "COLORTERM=truecolor", "-E", "HLIDSKJALV_TERMINAL=1",
                "-p", "MemoryMax=2G", "--", *base]
        return Plan(argv, True, "", False, unit)

    def shell(self) -> list[str]:
        if pty is None:
            from .conpty import default_shell
            return default_shell()
        sh = os.environ.get("SHELL") or "/bin/bash"
        if not os.path.isabs(sh) or not os.access(sh, os.X_OK):
            sh = "/bin/sh"
        return [sh, "-i"]

    def run(self, ws: WebSocket, sock) -> None:
        """Authenticate, then bridge the WebSocket and a fresh pty shell until either ends."""
        if not self.slots.acquire(blocking=False):
            self.on_refused("info", "A terminal was refused: too many open", f"At most {MAX_SESSIONS} at a time.", "term-slots")
            return ws.close(1013, "too many terminals open", drain=True)
        try:
            sock.settimeout(AUTH_TIMEOUT_S)
            try:
                op, data = ws.recv()
                hello = json.loads(data) if op == OP_TEXT else {}
            except (ValueError, OSError):
                hello = {}
            if not isinstance(hello, dict) or not self.owner.check(hello.get("key")):
                self.on_refused("medium", "A terminal connection without the owner key was refused",
                                "Only a paired browser may open a shell.", "term-key")
                return ws.close(1008, "not paired", drain=True)
            sock.settimeout(None)
            plan = self.plan()
            if plan.root and not self.owner.fresh_factor(hello.get("key")):
                return ws.close(1008, "second factor needed", drain=True)  # the page asks for a code and reconnects
            self._bridge(ws, hello.get("size"), plan)
        finally:
            self.slots.release()

    def _bridge(self, ws: WebSocket, size, plan: Plan | None = None) -> None:
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "HLIDSKJALV_TERMINAL": "1", "PWD": self.root}
        plan = plan or Plan(self.shell(), None, "", False, None)
        try:
            if pty is not None:
                shell = PosixShell(plan.argv, self.root, env, size, set_nnp=plan.set_nnp, unit=plan.unit)
            else:
                from .conpty import ConPtyShell
                shell = ConPtyShell(plan.argv, self.root, env, size)
        except Exception as e:  # OSError, or a ctypes error from the Windows pseudo-console
            ws.send(OP_TEXT, json.dumps({"error": f"couldn't start a shell: {getattr(e, 'strerror', None) or e}"}).encode())
            return ws.close(1011, drain=True)
        ws.send(OP_TEXT, json.dumps({"root": plan.root, "why": plan.why}).encode())
        if plan.root:
            self.on_alert("info", "security", "A root-capable shell was opened in the Terminal tab",
                          "sudo asks for your password as usual. Switch this off in Settings > Features.", "root-shell", True)

        def pump():  # shell -> browser
            try:
                while True:
                    data = shell.read()
                    if not data:
                        break
                    ws.send(OP_BIN, data)
            except OSError:
                pass
            code = shell.wait()
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
                    shell.write(data)
                elif op == OP_TEXT:
                    try:
                        msg = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and "size" in msg:
                        shell.resize(msg["size"])
        except (ConnectionError, OSError):
            pass
        finally:
            shell.stop()
            reader.join(timeout=2)
            shell.close()
            ws.closed = True


class PosixShell:
    """A shell on a pty: its own session, the pty as its controlling terminal."""

    def __init__(self, argv: list[str], cwd: str, env: dict, size, set_nnp: bool = False, unit: str | None = None):
        master, slave = pty.openpty()
        _resize(master, size)
        self.unit = unit

        # No preexec_fn: it is unsafe in a threaded server (the child can deadlock on a lock another thread
        # held when it forked). A small single-threaded stub sets up the tty and, when root is off,
        # no-new-privileges, then execs the shell; the flag travels on argv, never in the environment.
        stub = [sys.executable, "-I", str(Path(__file__).with_name("_ptyexec.py")), "--nnp" if set_nnp else "--", *argv]
        try:
            self.proc = subprocess.Popen(stub, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                                         start_new_session=True, close_fds=True)
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.fd = master

    def read(self) -> bytes:
        return os.read(self.fd, 65536)

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def resize(self, size) -> None:
        _resize(self.fd, size)

    def wait(self) -> int:
        return self.proc.wait()

    def stop(self) -> None:
        _stop(self.proc)
        if self.unit:  # belt and braces: the hangup above normally ends the transient unit already
            ctl = which("systemctl")
            if ctl:
                try:
                    subprocess.run([ctl, "--user", "stop", "--no-block", self.unit], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


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
