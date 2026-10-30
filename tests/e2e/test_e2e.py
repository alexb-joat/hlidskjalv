"""End-to-end: a real Hlidskjalv, a real browser (Firefox and WebKit), a throwaway project.

Off by default; needs Playwright and its browsers (requirements-e2e.txt):

    pip install --require-hashes --no-deps -r requirements-e2e.txt
    python -m playwright install firefox webkit
    HLIDSKJALV_E2E=1 python -m unittest discover -s tests/e2e -t . -v

Each test opens a fresh page and fails on any console error, CSP violation or request that
leaves the dashboard's own origin.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("HLIDSKJALV_E2E") == "1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(ENABLED, "set HLIDSKJALV_E2E=1 (and install requirements-e2e.txt) to run the browser tests")
class Dashboard:
    """Mixin: one Hlidskjalv and one browser per class; subclasses pick the engine."""
    engine = ""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.tmp = Path(tempfile.mkdtemp(prefix="hlidskjalv-e2e-"))
        cls.home, cls.project, cls.log = cls.tmp / "home", cls.tmp / "project", cls.tmp / "decisions.jsonl"
        data = cls.home / ".hlidskjalv"
        data.mkdir(parents=True)
        cls.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=cls.project, check=True)
        (cls.project / "README.md").write_text("hello\n")
        cls.log.write_text("")
        (data / "prices.json").write_text(json.dumps({"claude-haiku": {
            "input": 0.1, "output": 0.5, "long_context": {"threshold": 100000, "input": 0.5, "output": 2.5}}}))
        con = sqlite3.connect(data / "metrics.db")  # as Trioditis 0.4 writes it: with the calling tool
        con.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, ts REAL, route TEXT, model TEXT, local INTEGER, status INTEGER,"
                    " latency_ms INTEGER, tokens_in INTEGER, tokens_out INTEGER, cost REAL, fallbacks INTEGER, sensitive TEXT,"
                    " provider TEXT, unpriced INTEGER, session_fp TEXT, client TEXT)")
        con.execute("INSERT INTO calls (ts, route, model, local, status, cost, provider, client) "
                    "VALUES (?, 'quick', 'claude-haiku', 0, 200, 0.25, 'anthropic', 'batch-job')", (time.time(),))
        con.commit()
        con.close()
        cls.port = free_port()
        env = {**os.environ, "HOME": str(cls.home), "USERPROFILE": str(cls.home), "HLIDSKJALV_HOME": str(data),
               "PYTHONPATH": str(ROOT)}
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "hlidskjalv", "watch", str(cls.project), "--no-open", "--port", str(cls.port),
             "--interval", "0.2", "--trioditis-log", str(cls.log)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.url = f"http://127.0.0.1:{cls.port}/"
        deadline = time.time() + 20
        while True:
            try:
                urllib.request.urlopen(cls.url + "api/state", timeout=2).read()
                break
            except OSError:
                if time.time() > deadline or cls.server.poll() is not None:
                    cls.tearDownClass()
                    raise RuntimeError("Hlidskjalv did not start") from None
                time.sleep(0.2)
        cls.pw = sync_playwright().start()
        cls.browser = getattr(cls.pw, cls.engine).launch()

    @classmethod
    def tearDownClass(cls):
        for close in (lambda: cls.browser.close(), lambda: cls.pw.stop()):
            try:
                close()
            except Exception:  # noqa: S110 -- teardown: the browser may never have started
                pass
        if getattr(cls, "server", None):
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
            if cls.server.stderr:
                cls.server.stderr.close()

    # ------------------------------------------------------------- per test

    def setUp(self):
        self.errors, self.offsite = [], []
        self.page = self.browser.new_page(viewport={"width": 1360, "height": 900})
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(f"page error: {e}"))
        origin = self.url.rstrip("/")
        self.page.on("request", lambda r: r.url.startswith((origin, "data:", "blob:")) or self.offsite.append(r.url))
        self.page.goto(self.url)
        self.page.wait_for_function("() => /live map/i.test(document.body.innerText)", timeout=15000)
        self.page.evaluate("document.querySelectorAll('dialog[open]').forEach(d => d.close())")  # first-run setup

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [], "console errors or CSP violations")
        self.assertEqual(self.offsite, [], "requests that left the dashboard's own origin")

    def shows(self, text: str, timeout: float = 15000) -> None:
        self.page.wait_for_function("t => document.body.innerText.includes(t)", arg=text, timeout=timeout)

    def trioditis(self, **record) -> None:
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"route": "quick", "failed_attempts": [], "reasons": [], **record}) + "\n")

    # ------------------------------------------------------------- tests

    def test_boots_live_with_its_own_script_only(self):
        self.assertEqual(self.page.evaluate("document.querySelectorAll('script:not([src])').length"), 0)
        self.assertTrue(self.page.evaluate("/live/i.test(document.body.innerText)"))

    def test_a_new_file_appears_as_a_tile(self):
        name = f"e2e_{self.engine}.py"
        (self.project / name).write_text("print('hi')\n")
        self.shows(name[:8])

    def test_an_ai_step_shows_its_tool_and_long_context_price(self):
        self.trioditis(model="claude-haiku", local=False, status=200, client=f"tool-{self.engine}",
                       usage={"prompt_tokens": 150000, "completion_tokens": 1000})
        self.shows(f"tool-{self.engine}")
        self.shows("$0.08")  # 150,000 tokens is over the tier's threshold: $0.50/$2.50 for the whole call

    def test_a_rate_limit_says_when_to_retry(self):
        self.trioditis(route="explicit", model=None, status=429, error="rate_limited", client="e2e",
                       failed_attempts=[{"model": "claude-haiku", "status": 429, "retry_after": 7}])
        self.shows("rate-limited · retry in 7 s")

    def test_details_shows_spend_by_tool(self):
        self.page.evaluate("[...document.querySelectorAll('#nav button')].find(b => "
                           "/details/i.test(b.textContent + (b.title || '') + (b.getAttribute('aria-label') || '')))?.click()")
        self.page.wait_for_function("() => /by tool/i.test(document.body.innerText)", timeout=15000)
        self.shows("batch-job")


class FirefoxTest(Dashboard, unittest.TestCase):
    engine = "firefox"


class WebKitTest(Dashboard, unittest.TestCase):
    engine = "webkit"


if __name__ == "__main__":
    unittest.main()
