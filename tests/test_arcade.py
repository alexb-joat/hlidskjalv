import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from workflow_arcade.__main__ import HOOKS, check_folder
from workflow_arcade.redact import redact
from workflow_arcade import terminal as term
from workflow_arcade.server import Hub, read_session, serve
from workflow_arcade.sources import (
    ClaudeCodeWatcher, CommandWatcher, FileWatcher, SwitchboardWatcher, claude_code_project_dir,
)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RedactTest(unittest.TestCase):
    def test_masks_common_secrets(self):
        cases = [
            "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx",
            "curl -H 'Authorization: Bearer abcdefghijklmnop' https://x",
            "git clone https://alex:hunter2@github.com/x/y",
            "mysql --password=hunter2",
            "echo ghp_abcdefghijklmnopqrstuvwxyz0123",
        ]
        for c in cases:
            out = redact(c)
            for secret in ("sk-abcdefghijklmnopqrstuvwx", "abcdefghijklmnop", "hunter2", "ghp_abcdefghijklmnopqrstuvwxyz0123"):
                self.assertNotIn(secret, out, c)

    def test_leaves_normal_commands(self):
        for c in ("git commit -m \"Fix bug\"", "python -m pytest -q", "pip install -r requirements.txt"):
            self.assertEqual(redact(c), c)


class FileWatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.py").write_text("one\ntwo\n")
        (self.tmp / ".venv").mkdir()
        (self.tmp / ".venv" / "lib.py").write_text("x")
        self.w = FileWatcher(self.tmp)
        self.snap = {f["path"]: f for f in self.w.snapshot()}

    def test_snapshot_skips_library_folders(self):
        self.assertEqual(set(self.snap), {"a.py"})
        self.assertEqual(self.snap["a.py"]["lines"], 2)

    def test_create_change_delete(self):
        (self.tmp / "b.md").write_text("hi\n")
        (self.tmp / "a.py").write_text("one\nTWO\nthree\n")
        evs = {e["path"]: e for e in self.w.poll()}
        self.assertEqual(evs["b.md"]["type"], "file_created")
        self.assertEqual((evs["a.py"]["added"], evs["a.py"]["removed"]), (2, 1))
        (self.tmp / "b.md").unlink()
        evs = self.w.poll()
        self.assertEqual([(e["type"], e["path"]) for e in evs], [("file_deleted", "b.md")])

    def test_secret_files_are_never_read(self):
        (self.tmp / ".env").write_text("API_KEY=supersecret\n")
        ev = self.w.poll()[0]
        self.assertTrue(ev["sensitive"])
        self.assertNotIn("added", ev)
        self.assertNotIn(".env", self.w.lines)

    @unittest.skipIf(os.name == "nt", "symlinks need admin rights on Windows")
    def test_symlinks_are_ignored(self):
        outside = Path(tempfile.mkdtemp()) / "x.txt"
        outside.write_text("outside\n")
        os.symlink(outside, self.tmp / "link.txt")
        self.assertEqual(self.w.poll(), [])


class CommandAndAiTest(unittest.TestCase):
    def test_commands_only_from_project_and_redacted(self):
        proj = Path(tempfile.mkdtemp()).resolve()
        log = Path(tempfile.mkdtemp()) / "commands.log"
        log.write_text("")
        w = CommandWatcher(proj, log)
        with log.open("a") as f:
            f.write(f"1\t{proj}\tgit status\n")
            f.write(f"2\t{tempfile.gettempdir()}\tls elsewhere\n")
            f.write(f"3\t{proj}\texport API_KEY=abc123\n")
        evs = w.poll()
        self.assertEqual([e["cmd"] for e in evs], ["git status", "export API_KEY=•••"])

    def test_ai_calls_with_prices(self):
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        log.write_text("")
        w = SwitchboardWatcher(log, {"big": {"input": 3, "output": 15}})
        recs = [
            {"route": "code", "model": "big", "local": False, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
            {"route": "quick", "model": "small", "local": True, "sensitive": ["email"], "usage": None},
        ]
        log.write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        evs = w.poll()
        self.assertEqual(evs[0]["cost"], 0.0045)
        self.assertEqual(evs[1]["cost"], 0.0)
        self.assertEqual(evs[1]["sensitive"], ["email"])


class ClaudeCodeWatcherTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.claude_home = Path(tempfile.mkdtemp())

    def test_project_dir_encoding(self):
        got = claude_code_project_dir(Path("/home/alexb/projects/hello-test"), self.claude_home)
        self.assertEqual(got, self.claude_home / "projects" / "-home-alexb-projects-hello-test")
        got = claude_code_project_dir(Path("/home/alexb/Workflow Arcade/my.app"), self.claude_home)
        self.assertEqual(got.name, "-home-alexb-Workflow-Arcade-my-app")

    def test_subagents_and_synthetic_messages(self):
        proj_dir = claude_code_project_dir(self.root, self.claude_home)
        (proj_dir / "s1" / "subagents").mkdir(parents=True)
        w = ClaudeCodeWatcher(self.root, self.claude_home)
        self._write_turn(proj_dir / "s1" / "subagents" / "agent-a.jsonl", "msg_a", "claude-haiku-5-5", ["text"], 7)
        self._write_turn(proj_dir / "s1.jsonl", "msg_s", "<synthetic>", ["text"], 0)
        evs = w.poll()
        self.assertEqual([(e["route"], e["model"]) for e in evs], [("claude-code (subagent)", "claude-haiku-5-5")])
        self.assertEqual(evs[0]["tokens_cached"], 50)

    def test_unavailable_when_no_transcripts_yet(self):
        w = ClaudeCodeWatcher(self.root, self.claude_home)
        self.assertFalse(w.available())
        self.assertEqual(w.poll(), [])

    def _write_turn(self, log: Path, msg_id: str, model: str, content_types: list[str], out_tokens: int):
        usage = {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50,
                  "output_tokens": out_tokens}
        with log.open("a") as f:
            for ct in content_types:
                rec = {"type": "assistant", "message": {"id": msg_id, "model": model,
                       "content": [{"type": ct}], "usage": usage}}
                f.write(json.dumps(rec) + "\n")

    def test_dedupes_split_turns_and_skips_history(self):
        proj_dir = claude_code_project_dir(self.root, self.claude_home)
        proj_dir.mkdir(parents=True)
        log = proj_dir / "session1.jsonl"
        log.write_text("")
        self._write_turn(log, "msg_1", "claude-opus-5-5", ["thinking"], 500)  # pre-existing, not watched

        w = ClaudeCodeWatcher(self.root, self.claude_home)
        self.assertTrue(w.available())
        self.assertEqual(w.poll(), [])  # history before the watch started is not replayed

        self._write_turn(log, "msg_2", "claude-opus-5-5", ["thinking", "text"], 266)
        evs = w.poll()
        self.assertEqual(len(evs), 1)  # one event per message id, not per content block
        ev = evs[0]
        self.assertEqual(ev["type"], "ai_call")
        self.assertEqual(ev["route"], "claude-code")
        self.assertEqual(ev["model"], "claude-opus-5-5")
        self.assertFalse(ev["local"])
        self.assertTrue(ev["subscription"])
        self.assertIsNone(ev["cost"])
        self.assertEqual(ev["tokens_in"], 5 + 100 + 50)
        self.assertEqual(ev["tokens_out"], 266)

    def test_new_session_file_is_read_from_its_start(self):
        proj_dir = claude_code_project_dir(self.root, self.claude_home)
        proj_dir.mkdir(parents=True)
        w = ClaudeCodeWatcher(self.root, self.claude_home)
        log = proj_dir / "session2.jsonl"
        log.write_text("")
        self._write_turn(log, "msg_1", "claude-sonnet-5", ["text"], 10)
        evs = w.poll()
        self.assertEqual([e["model"] for e in evs], ["claude-sonnet-5"])

    def test_ignores_non_assistant_lines(self):
        proj_dir = claude_code_project_dir(self.root, self.claude_home)
        proj_dir.mkdir(parents=True)
        log = proj_dir / "session3.jsonl"
        log.write_text("")
        w = ClaudeCodeWatcher(self.root, self.claude_home)
        with log.open("a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
            f.write("not json\n")
        self.assertEqual(w.poll(), [])


class TailTest(unittest.TestCase):
    def test_skips_a_line_longer_than_one_read(self):
        log = Path(tempfile.mkdtemp()) / "t.jsonl"
        log.write_text("")
        from workflow_arcade.sources import READ_MAX, Tail
        t = Tail(log)
        with log.open("a") as f:
            f.write("x" * (READ_MAX + 10) + "\n" + '{"ok": 1}\n')
        got = []
        for _ in range(4):
            got += t.lines()
        self.assertEqual(got, ['{"ok": 1}'])


class SwitchboardDetailsTest(unittest.TestCase):
    def test_reasons_and_budget_blocks_are_carried(self):
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        log.write_text("")
        w = SwitchboardWatcher(log, {})
        rec = {"route": "code", "model": "coder", "local": True, "status": 200,
               "reasons": ["keywords matched route code (score 2)", "x" * 500],
               "failed_attempts": [{"model": "big", "error": "budget_blocked", "reason": "daily cap reached"},
                                   {"model": "gpt", "status": 503}, "weird"]}
        log.write_text(json.dumps(rec) + "\n")
        [ev] = w.poll()
        self.assertEqual(ev["reasons"][0], "keywords matched route code (score 2)")
        self.assertLessEqual(len(ev["reasons"][1]), 160)
        self.assertEqual(ev["attempts"][0], {"model": "big", "error": "budget_blocked", "reason": "daily cap reached"})
        self.assertEqual(ev["attempts"][1], {"model": "gpt", "status": 503})
        self.assertEqual(ev["attempts"][2], {"model": "weird"})


class WsClient:
    """Just enough of a browser WebSocket to test the terminal endpoint."""
    open: list = []

    def __init__(self, port, origin=None, host=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        WsClient.open.append(self)
        origin = origin or f"http://127.0.0.1:{port}"
        req = (f"GET /api/term HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               f"Sec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n")
        self.sock.sendall(req.encode())
        self.f = self.sock.makefile("rb")
        self.status = int(self.f.readline().split()[1])
        while self.f.readline() not in (b"\r\n", b""):
            pass

    def send(self, op, data: bytes):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big"))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def hello(self, **msg):
        self.send(term.OP_TEXT, json.dumps({"size": [80, 24], **msg}).encode())

    def recv(self):
        b1, b2 = self.f.read(2)
        n = b2 & 0x7F
        if n == 126:
            n = int.from_bytes(self.f.read(2), "big")
        elif n == 127:
            n = int.from_bytes(self.f.read(8), "big")
        return b1 & 0x0F, self.f.read(n)

    def read_until(self, needle: bytes, limit=5.0):
        out, end = b"", time.time() + limit
        while needle not in out and time.time() < end:
            op, data = self.recv()
            if op == term.OP_CLOSE:
                break
            if op == term.OP_BIN:
                out += data
        return out


@unittest.skipUnless(term.supported(), "pty needs Linux or macOS")
class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.port = free_port()
        self.shell = term.Terminal(self.root)
        self.httpd = serve(Hub("demo", [], None, {}), Path(tempfile.mkdtemp()), self.port, self.shell)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        for c in WsClient.open:
            c.f.close(), c.sock.close()
        WsClient.open.clear()
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_foreign_origin_and_host_are_refused(self):
        self.assertEqual(WsClient(self.port, origin="https://evil.example").status, 403)
        self.assertEqual(WsClient(self.port, origin="null").status, 403)
        self.assertEqual(WsClient(self.port, host="evil.example").status, 403)

    def test_wrong_token_is_closed(self):
        ws = WsClient(self.port)
        self.assertEqual(ws.status, 101)
        ws.hello(token="guess")
        op, data = ws.recv()
        self.assertEqual(op, term.OP_CLOSE)
        self.assertEqual(int.from_bytes(data[:2], "big"), 1008)

    def test_pairing_code_works_once_then_token_runs_a_real_shell(self):
        code = self.shell.pair_code
        ws = WsClient(self.port)
        ws.hello(pair=code)
        op, data = ws.recv()
        self.assertEqual(op, term.OP_TEXT)
        token = json.loads(data)["token"]
        self.assertEqual(token, self.shell.token)
        ws.send(term.OP_BIN, b"echo arcade-$((40+2))\n")
        self.assertIn(b"arcade-42", ws.read_until(b"arcade-42"))
        ws.send(term.OP_BIN, b"pwd\n")
        self.assertIn(str(self.root.resolve()).encode(), ws.read_until(str(self.root.resolve()).encode()))

        again = WsClient(self.port)  # a copied link is worthless after first use
        again.hello(pair=code)
        self.assertEqual(again.recv()[0], term.OP_CLOSE)

        ok = WsClient(self.port)
        ok.hello(token=token)
        ok.send(term.OP_BIN, b"echo second\n")
        self.assertIn(b"second", ok.read_until(b"second"))

    def test_shell_exit_is_reported(self):
        ws = WsClient(self.port)
        ws.hello(token=self.shell.token)
        ws.send(term.OP_BIN, b"exit 3\n")
        while True:
            op, data = ws.recv()
            if op == term.OP_TEXT:
                self.assertEqual(json.loads(data), {"exit": 3})
                break

    def test_off_by_default(self):
        port = free_port()
        httpd = serve(Hub("demo", [], None, {}), Path(tempfile.mkdtemp()), port)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            self.assertEqual(WsClient(port).status, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_vendor_files_only_from_allowlist(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/static/vendor/xterm.js", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn(b"Terminal", r.read())
        c.request("GET", "/static/vendor/README.md", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse(); r.read()
        self.assertEqual(r.status, 404)
        c.close()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp())
        cls.port = free_port()
        session = cls.home / "20260101-120000-demo.jsonl"
        cls.hub = Hub("demo", [{"path": "a.py", "size": 1}], session, {})
        cls.httpd = serve(cls.hub, cls.home, cls.port)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, host=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        return r.status, r.read(), r

    def test_page_and_state(self):
        status, body, r = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Workflow Arcade", body)
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.hub.publish({"type": "command", "cmd": "ls", "cwd": "."})
        status, body, _ = self.get("/api/state")
        state = json.loads(body)
        self.assertEqual(state["snapshot"]["project"], "demo")
        self.assertEqual(state["events"][-1]["cmd"], "ls")

    def test_rejects_foreign_host_and_writes(self):
        self.assertEqual(self.get("/", host="evil.example:80")[0], 403)
        self.assertEqual(self.get("/api/state", method="POST")[0], 405)

    def test_sessions_cannot_escape_folder(self):
        self.assertIsNone(read_session(self.home, "../../etc/passwd"))
        self.assertEqual(self.get("/api/session?id=..%2F..%2Fx.jsonl")[0], 404)
        status, body, _ = self.get("/api/session?id=20260101-120000-demo.jsonl")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["type"], "snapshot")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_session_file_is_private(self):
        mode = (self.home / "20260101-120000-demo.jsonl").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_event_stream(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/events?after=0", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        threading.Timer(0.2, lambda: self.hub.publish({"type": "commit", "sha": "abc1234", "message": "x"})).start()
        deadline = time.time() + 5
        buf = b""
        while b"abc1234" not in buf and time.time() < deadline:
            buf += r.fp.readline()
        self.assertIn(b"abc1234", buf)
        c.close()


class CliTest(unittest.TestCase):
    def test_refuses_home_folder(self):
        with self.assertRaises(SystemExit):
            check_folder(Path.home().resolve())

    def test_hooks_exist(self):
        for shell in ("bash", "zsh", "powershell"):
            self.assertIn("commands.log", HOOKS[shell])


if __name__ == "__main__":
    unittest.main()
