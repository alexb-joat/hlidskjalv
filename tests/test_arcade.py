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
from workflow_arcade.server import Hub, read_session, serve
from workflow_arcade.sources import CommandWatcher, FileWatcher, SwitchboardWatcher


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
