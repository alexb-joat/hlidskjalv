import contextlib
import datetime as dt
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lunete import __main__ as M
from lunete import graph, personal, study
from lunete.auth import Owner
from lunete.server import Hub, serve
from lunete.settings import VIEWS, Settings
from lunete.study import StudyError

from .test_graph import new_store
from .test_lunete import free_port

MON = dt.date(2026, 11, 23)  # a Monday


class Durations(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual([study.minutes(t) for t in ("6h", "90m", "1h30m", "45", "2h 5m", "10min")],
                         [360, 90, 90, 45, 125, 10])
        for bad in ("", "h", "six hours", "1.5h"):
            with self.assertRaises(StudyError):
                study.minutes(bad)

    def test_minutes_with_many_spaces_fails_fast(self):
        # CodeQL py/polynomial-redos: the old pattern backtracked over runs of spaces (4,000 took 0.1 s, growing
        # with the square); a long input is now refused before any pattern runs
        self.assertEqual(study.minutes(" 1 h 30 m "), 90)
        t0 = time.perf_counter()
        for bad in ("1h" + " " * 60000 + "x", "1" * 60000, " " * 60000 + "m"):
            with self.assertRaises(StudyError):
                study.minutes(bad)
        self.assertLess(time.perf_counter() - t0, 0.5)

    def test_ranges_and_pieces(self):
        self.assertEqual(study.minutes_range("3-5h"), [180, 300])
        self.assertEqual(study.minutes_range("4h"), [240, 240])
        self.assertEqual(study.piece_spec("S1 stats toolkit=3-5h"), ("S1 stats toolkit", [180, 300]))
        with self.assertRaises(StudyError):
            study.minutes_range("5-3h")
        with self.assertRaises(StudyError):
            study.piece_spec("no estimate")


class Lines(unittest.TestCase):
    def setUp(self):
        self.s = new_store()
        self.line = study.add_line(self.s, "Eval sprint", 180, [("S1 stats", [180, 300]), ("S2 judge", [480, 600])])

    def test_add_line_with_pieces_and_no_duplicate_names(self):
        summary = study.summary(self.s, MON)
        li = summary["lines"][0]
        self.assertEqual((li["name"], li["week_min"], [p["title"] for p in li["pieces"]]),
                         ("Eval sprint", 180, ["S1 stats", "S2 judge"]))
        with self.assertRaises(StudyError):
            study.add_line(self.s, "eval SPRINT", 60)

    def test_log_counts_toward_the_week_and_the_piece(self):
        piece = study.summary(self.s, MON)["lines"][0]["pieces"][0]["id"]
        study.log(self.s, "Eval sprint", "2026-11-23", "2h", note="bootstrap", piece=piece)
        study.log(self.s, "Eval sprint", "2026-11-25", "70m")
        study.log(self.s, "Eval sprint", "2026-11-16", "30m")  # last week
        li = study.summary(self.s, dt.date(2026, 11, 25))["lines"][0]
        self.assertEqual((li["this_week"], li["today"], li["done_for_week"]), (190, 70, True))
        self.assertEqual([w["minutes"] for w in li["weeks"]][-2:], [30, 190])
        self.assertEqual(len(li["weeks"]), study.WEEKS_SHOWN)
        self.assertEqual(li["pieces"][0]["minutes"], 120)

    def test_target_and_piece_done(self):
        study.set_target(self.s, "Eval sprint", 240)
        piece = study.summary(self.s, MON)["lines"][0]["pieces"][1]["id"]
        study.set_piece_done(self.s, piece, True)
        study.set_piece_done(self.s, piece, True)  # already done: nothing to write
        li = study.summary(self.s, MON)["lines"][0]
        self.assertEqual((li["week_min"], li["pieces"][1]["done"]), (240, True))
        with self.assertRaises(StudyError):
            study.set_target(self.s, "Eval sprint", 99999)


class Entries(unittest.TestCase):
    def setUp(self):
        self.s = new_store()
        study.add_line(self.s, "Serbian", 360, checks=["2026-12-18", "2027-01-29"])

    def test_scores_and_checks(self):
        study.log(self.s, "Serbian", "2026-11-23", "20m", score=4)
        study.log(self.s, "Serbian", "2026-11-24", "20m", score="6")
        li = study.summary(self.s, MON)["lines"][0]
        self.assertEqual(li["scores"], [{"day": "2026-11-23", "score": 4}, {"day": "2026-11-24", "score": 6}])
        self.assertEqual(li["checks"], ["2026-12-18", "2027-01-29"])

    def test_bad_input_is_named(self):
        for kw, word in (({"mins": "0m"}, "between 1"), ({"score": 11}, "0 to 10"), ({"day": "23.11.2026"}, "YYYY-MM-DD"),
                         ({"url": "javascript:alert(1)"}, "http"), ({"note": "x" * 201}, "200")):
            args = {"day": "2026-11-23", "mins": "10m", **kw}
            with self.assertRaises(StudyError) as cm:
                study.log(self.s, "Serbian", args.pop("day"), args.pop("mins"), **args)
            self.assertIn(word, str(cm.exception))

    def test_edit_delete_restore(self):
        e = study.log(self.s, "Serbian", "2026-11-23", "20m", note="podcast", score=5, url="https://example.org/a")
        study.edit(self.s, e, mins="35m", score=None, note="podcast, slow")
        got = study.entries(self.s)[0]
        self.assertEqual((got["minutes"], got["score"], got["note"], got["url"]),
                         (35, None, "podcast, slow", "https://example.org/a"))
        study.delete(self.s, e)
        self.assertEqual(study.entries(self.s), [])
        study.restore(self.s, e)
        self.assertEqual(len(study.entries(self.s)), 1)
        self.assertTrue(graph.check(self.s)["ok"])

    def test_csv_export_defuses_formulas(self):
        study.log(self.s, "Serbian", "2026-11-23", "20m", note="=HYPERLINK(\"x\")")
        text = study.csv_text(self.s)
        self.assertEqual(text.splitlines()[0], "day,line,minutes,score,piece,note,link")
        self.assertIn("'=HYPERLINK", text)


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                M.main(list(argv))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        self.home = tempfile.mkdtemp()
        patcher = mock.patch.dict(os.environ, {"LUNETE_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_server_json(self, port):
        Path(self.home, "server.json").write_text(json.dumps({"port": port, "folder": "/tmp/proj"}), encoding="utf-8")

    def test_pair_refuses_when_lunete_is_stopped(self):
        import socket
        with socket.socket() as sock:  # a port nothing listens on once closed
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.write_server_json(port)  # left behind by a server that has since stopped
        code, out, _ = self.run_cli("pair", "--open")
        self.assertIn(f"isn't running on port {port}", str(code))
        self.assertIn("python3 -m lunete watch /tmp/proj", str(code))
        self.assertNotIn("#pair=", out)
        self.assertFalse(Path(self.home, "pair").exists())  # no code issued for a dead link

    def test_pair_prints_the_link_when_lunete_answers(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Ok(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        self.write_server_json(port)
        code, out, _ = self.run_cli("pair")
        self.assertEqual(code, 0)
        self.assertIn(f"http://127.0.0.1:{port}/#pair=", out)

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.run_cli("study", "add-line", "Eval sprint", "--week", "3h", "--piece", "S1=3-5h", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("nothing written", out)
        self.assertFalse(personal.path(Path(self.home)).exists())

    def test_add_log_list_and_check(self):
        self.assertEqual(self.run_cli("study", "add-line", "Serbian", "--week", "6h")[0], 0)
        self.assertEqual(self.run_cli("study", "log", "serbian", "25m", "--score", "6")[0], 0)
        code, out, _ = self.run_cli("study", "list")
        self.assertIn("Serbian: 25 of 360 min this week", out)
        code, out, _ = self.run_cli("graph", "check")
        self.assertEqual(code, 0, out)
        self.assertIn("ok: the chain verifies", out)

    def test_errors_name_the_cause_and_the_fix(self):
        code, _, err = self.run_cli("graph", "check")
        self.assertEqual(code, 1)
        self.assertIn("no store", err)
        code, _, err = self.run_cli("study", "log", "Music", "10m")
        self.assertEqual(code, 2)
        self.assertIn("lunete study add-line", err)

    def test_rebuild_repairs_a_lagging_view(self):
        self.run_cli("study", "add-line", "Serbian", "--week", "6h")
        s = personal.open_store(Path(self.home))
        s.conn.execute("UPDATE items SET title = 'tampered'")
        s.close()
        code, _, err = self.run_cli("graph", "check")
        self.assertEqual(code, 1)
        self.assertIn("lunete graph rebuild", err)
        code, out, _ = self.run_cli("graph", "rebuild")
        self.assertEqual(code, 0, out)
        self.assertIn("Serbian", study.summary(personal.open_store(Path(self.home)))["lines"][0]["name"])

    def test_help_is_accurate(self):
        code, out, _ = self.run_cli("study", "add-line", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--dry-run", out)
        self.assertIn("example:", out)


class Api(unittest.TestCase):
    """The Study tab's routes: personal data, so the owner key is needed even to read."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.port = free_port()
        self.owner = Owner(self.home)
        self.httpd = serve(Hub("demo", [], None, {}), self.home, self.port, None, self.owner,
                           Settings(self.home / "settings.json"), study_home=self.home)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.key = self.owner.start_session()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, method, path, body=None, key=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}"}
        if data is not None:
            hdr |= {"Content-Type": "application/json", "Content-Length": str(len(data))}
        if key:
            hdr["X-Hlidskjalv-Key"] = key
        c.request(method, path, body=data, headers=hdr)
        r = c.getresponse()
        raw, ctype = r.read(), r.getheader("Content-Type", "")
        c.close()
        return r.status, (json.loads(raw or b"{}") if "json" in ctype else raw.decode())

    def act(self, **body):
        return self.call("POST", "/api/study", body, key=self.key)

    def test_unpaired_browsers_get_nothing(self):
        for method, path, body in (("GET", "/api/study", None), ("GET", "/api/study.csv", None),
                                   ("POST", "/api/study", {"action": "line", "name": "Serbian", "week": "6h"})):
            for key in (None, "not-a-session"):
                status, out = self.call(method, path, body, key=key)
                self.assertEqual(status, 401, (method, path, key))
                self.assertNotIn("lines", out)
        self.assertFalse(personal.path(self.home).exists())

    def test_every_action_round_trips(self):
        status, out = self.call("GET", "/api/study", key=self.key)
        self.assertEqual((status, out["lines"]), (200, []))
        status, out = self.act(action="line", name="Serbian", week="6h")
        self.assertEqual(status, 200, out)
        line = out["line"]
        status, out = self.act(action="log", line=line, minutes="25m", day=dt.date.today().isoformat(), note="podcast", score="6")
        self.assertEqual(status, 200, out)
        entry = out["entry"]
        self.assertEqual((out["summary"]["lines"][0]["today"], out["summary"]["entries"][0]["score"]), (25, 6))
        status, out = self.act(action="edit", entry=entry, minutes="40", score=None)
        self.assertEqual((status, out["summary"]["entries"][0]["minutes"], out["summary"]["entries"][0]["score"]), (200, 40, None))
        self.assertEqual(self.act(action="target", line=line, week="5h")[1]["summary"]["lines"][0]["week_min"], 300)
        self.assertEqual(self.act(action="delete", entry=entry)[1]["summary"]["entries"], [])
        self.assertEqual(len(self.act(action="restore", entry=entry)[1]["summary"]["entries"]), 1)
        status, text = self.call("GET", "/api/study.csv", key=self.key)
        self.assertEqual(status, 200)
        self.assertEqual(text.splitlines()[0], "day,line,minutes,score,piece,note,link")
        self.assertIn("Serbian,40", text)

    def test_bad_requests_are_named(self):
        self.act(action="line", name="Serbian", week="6h")
        for body, word in (({"action": "fly"}, "action must be"), ({"action": "log", "line": "Music", "minutes": "5m"}, "add-line"),
                           ({"action": "log", "line": "Serbian", "minutes": "0m"}, "between 1"),
                           ({"action": "line", "name": "serbian", "week": "1h"}, "exists")):
            status, out = self.act(**body)
            self.assertEqual(status, 400, body)
            self.assertIn(word, out["error"])
        status, page = self.call("GET", "/")
        self.assertIn('id="view-study"', page)
        self.assertIn("/static/study.js", page)


class SavedLayouts(unittest.TestCase):
    def test_a_layout_saved_before_study_gains_the_tab(self):
        path = Path(tempfile.mkdtemp()) / "settings.json"
        old = ["alerts", "live", "replay", "learn", "details", "terminal"]
        path.write_text(json.dumps({"version": 1, "layout": {"views": old, "default_view": "alerts"}}))
        views = Settings(path).data["layout"]["views"]
        self.assertEqual(sorted(views), sorted(VIEWS))
        self.assertEqual(views[:5], ["alerts", "live", "replay", "learn", "study"])  # after Learn; the owner's order kept
        self.assertEqual(Settings(path).data["layout"]["default_view"], "alerts")


if __name__ == "__main__":
    unittest.main()
