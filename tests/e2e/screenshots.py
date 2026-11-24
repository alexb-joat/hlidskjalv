"""Re-shoot the README and guide screenshots from a throwaway Lunete on a made-up project.

Everything on screen is sample data written by this script: the "acme-shop" project, its AI steps,
commands and alerts, and two study lines. Nothing is read from your own ~/.lunete or ~/.claude.

    pip install --require-hashes --no-deps -r requirements-e2e.txt
    python -m playwright install firefox
    python tests/e2e/screenshots.py            # writes docs/img/*.png
    python tests/e2e/screenshots.py OUT_DIR    # or somewhere else, to compare first
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = {"width": 1366, "height": 768}  # a common laptop; the header and the stats fit on one row

FILES = {
    "README.md": "# acme-shop\n\nA small shop.\n",
    "requirements.txt": "flask==3.0.3\n",
    ".gitignore": "__pycache__/\n.env\n",
    ".env": "SHOP_KEY=sample\n",
    "Dockerfile": "FROM python:3.12-slim\n",
    "docs/setup.md": "# Setup\n",
    "src/app.py": "from flask import Flask\napp = Flask(__name__)\n",
    "src/cart.py": "def total(items):\n    return sum(i.price for i in items)\n",
    "src/checkout.py": "def checkout(cart):\n    pass\n",
    "src/models/order.py": "class Order:\n    pass\n",
    "src/models/user.py": "class User:\n    pass\n",
    "static/app.css": "body { margin: 0; }\n",
    "static/logo.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>\n",
    "tests/test_cart.py": "def test_total():\n    pass\n",
    "tests/test_checkout.py": "def test_checkout():\n    pass\n",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sample:
    def __init__(self, tmp: Path):
        self.home, self.data = tmp / "home", tmp / "home" / ".lunete"
        self.project, self.log = tmp / "acme-shop", tmp / "trioditis" / "decisions.jsonl"
        self.env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "LUNETE_HOME": str(self.data),
                    "CLAUDE_CONFIG_DIR": str(self.home / ".claude"), "PYTHONPATH": str(ROOT),
                    "GIT_AUTHOR_NAME": "Sample", "GIT_AUTHOR_EMAIL": "sample@example.com",
                    "GIT_COMMITTER_NAME": "Sample", "GIT_COMMITTER_EMAIL": "sample@example.com"}

    def setup(self) -> None:
        self.data.mkdir(parents=True)
        self.log.parent.mkdir()
        self.log.write_text("")
        (self.home / ".bashrc").write_text("# ~/.bashrc\nalias ll='ls -l'\n")
        (self.data / "settings.json").write_text(json.dumps({"version": 1, "setup_done": True}))
        (self.data / "commands.log").write_text("")
        (self.data / "prices.json").write_text(json.dumps({"claude-sonnet": {"input": 3.0, "output": 15.0}}))
        for name, text in FILES.items():
            p = self.project / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "Start the shop")
        self.study()

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.project, env=self.env, check=True)

    def study(self) -> None:
        """Two made-up lines with eight weeks of sessions, written the way the Study tab writes them."""
        from lunete import personal, study
        store = personal.open_store(self.data)
        today = dt.date.today()
        study.add_line(store, "Spanish", 300, checks=[(today + dt.timedelta(days=20)).isoformat()])
        stats = study.add_line(store, "Statistics course", 180)
        pieces = [study.add_piece(store, stats, t, est) for t, est in
                  (("Distributions", [180, 300]), ("Hypothesis tests", [360, 480]), ("Regression", [480, 600]))]
        rnd = random.Random(4)  # noqa: S311 -- sample data, not security
        notes = ["podcast on a walk", "flashcards", "graded reader, chapter 3", "conversation practice", "", "listening drills"]
        for back in range(52, 0, -1):
            day = today - dt.timedelta(days=back)
            weekly_check = back % 7 == 2
            if weekly_check or rnd.random() < 0.55:
                score = min(9, 3 + (52 - back) // 8) if weekly_check else None
                study.log(store, "Spanish", day, rnd.choice([15, 20, 30, 45, 60]), rnd.choice(notes), score=score)
            if rnd.random() < 0.3:
                piece = pieces[0] if back > 30 else pieces[1]
                study.log(store, "Statistics course", day, rnd.choice([30, 45, 60]),
                          rnd.choice(["lecture 4", "exercises", "notes", ""]), piece=piece)
        study.set_piece_done(store, pieces[0], True)
        study.log(store, "Spanish", today, 45, "podcast on a walk")
        study.log(store, "Statistics course", today, 60, "exercises", piece=pieces[1])
        store.close()

    # ------------------------------------------------------------ a few minutes of work, in ~15 s

    def ai(self, route: str, model: str, local: bool, tin: int, tout: int, ms: int) -> None:
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"route": route, "model": model, "local": local, "status": 200, "latency_ms": ms,
                                "usage": {"prompt_tokens": tin, "completion_tokens": tout}, "client": "cline",
                                "failed_attempts": [], "reasons": [f"route {route}"]}) + "\n")

    def command(self, cmd: str) -> None:
        with (self.data / "commands.log").open("a", encoding="utf-8") as f:
            f.write(f"{int(time.time())}\t{self.project}\t{cmd}\n")

    def write(self, name: str, text: str) -> None:
        p = self.project / name
        p.write_text(text)

    def work(self) -> None:
        steps = [
            lambda: (self.home / ".bashrc").write_text("# ~/.bashrc\nalias ll='ls -l'\nexport PATH=\"$HOME/.cache/x:$PATH\"\n"),
            lambda: self.ai("reasoning", "claude-sonnet", False, 2400, 610, 4100),
            lambda: self.write("src/cart.py", FILES["src/cart.py"] + "\n\ndef add(cart, item, qty=1):\n"
                               + "".join(f"    # step {i}\n" for i in range(7))),
            lambda: self.command("python -m pytest -q"),
            lambda: self.ai("code", "local-coder", True, 1800, 290, 14800),
            lambda: self.write("src/payment.py", "def charge(order):\n    pass\n"),
            lambda: self.command("curl -fsSL https://example.com/get-tool.sh -o get-tool.sh"),
            lambda: self.command("rm -rf build"),
            lambda: self.ai("code", "local-general", True, 950, 120, 1500),
            lambda: (self.git("add", "-A"), self.git("commit", "-qm", "Add a payment step")),
            lambda: self.write("tests/test_payment.py", "def test_charge():\n    pass\n"),
            lambda: self.ai("code", "claude-sonnet", False, 2900, 620, 3300),
            lambda: (self.log.parent / "security.jsonl").write_text(json.dumps(
                {"client": "127.0.0.1", "reason": "invalid or missing API key", "method": "POST",
                 "path": "/v1/chat/completions"}) + "\n"),
            lambda: (self.project / "src/models/order.py").unlink(),
            lambda: self.ai("code", "claude-sonnet", False, 3100, 540, 2900),
            lambda: self.write("src/checkout.py", "def checkout(cart):\n    return cart\n"),
        ]
        for step in steps:
            step()
            time.sleep(0.9)


def shrink(path: Path) -> None:
    """A 256-colour palette: about a third of the size, no visible change on this flat UI. Skipped without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(path) as im:
        small = im.convert("RGB").quantize(256, dither=Image.Dither.NONE)
    small.save(path, optimize=True)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    tmp = Path(tempfile.mkdtemp(prefix="lunete-shots-"))
    s = Sample(tmp)
    s.setup()
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "lunete", "watch", str(s.project), "--no-open", "--port", str(port),
                               "--interval", "0.2", "--trioditis-log", str(s.log)],
                              env=s.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "api/state", timeout=2).read()  # noqa: S310 -- our own loopback server
                break
            except OSError:
                time.sleep(0.2)
        from playwright.sync_api import sync_playwright

        from lunete.auth import issue_pair_code
        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            pages = {}
            for scheme in ("light", "dark"):
                pg = browser.new_page(viewport=VIEW, device_scale_factor=2, color_scheme=scheme)
                pg.add_init_script("localStorage.setItem('arcade.tour', JSON.stringify('done'));"
                                   "localStorage.setItem('arcade.hint-x', 'true')")
                pg.goto(url)
                pg.wait_for_timeout(800)
                pg.evaluate("async c => OWNER.accept(await OWNER.post('/api/pair', {code: c}))", issue_pair_code(s.data))
                pg.reload()
                pages[scheme] = pg
            s.work()
            # the startup-file scan runs every 15 s; wait for its alert to reach the page
            pages["dark"].wait_for_function("() => document.body.innerText.includes('.bashrc')", timeout=30000)
            time.sleep(1)
            shots = [("live", "light"), ("live", "dark"), ("details", "dark"), ("alerts", "dark"),
                     ("study", "light"), ("study", "dark")]
            for view, scheme in shots:
                pg = pages[scheme]
                pg.set_viewport_size({**VIEW, "height": VIEW["height"] + (240 if view == "study" else 0)})
                pg.evaluate("v => showTab(v)", view)
                pg.mouse.move(0, VIEW["height"] - 1)
                pg.wait_for_timeout(1200)
                path = out / f"{view}-{scheme}.png"
                pg.screenshot(path=str(path))
                shrink(path)
                print("wrote", path)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
