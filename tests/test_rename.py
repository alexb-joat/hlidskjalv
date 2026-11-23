"""The 0.5 rename (Hlidskjalv -> Lunete): env var order, the one-time state-folder move, the alias.

Every test runs in a temp HOME; the real ~/.hlidskjalv is never touched."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lunete import ENV_NAMES, env
from lunete import __main__ as M

ROOT = Path(__file__).resolve().parent.parent
ALL_NAMES = [n for names in ENV_NAMES.values() for n in names]


class EnvTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {n: "" for n in ALL_NAMES})
        p.start()
        self.addCleanup(p.stop)

    def test_newest_name_wins(self):
        os.environ.update(LUNETE_PORT="1", HLIDSKJALV_PORT="2", ARCADE_PORT="3")
        self.assertEqual(env("PORT"), "1")

    def test_old_names_still_work_in_order(self):
        os.environ.update(HLIDSKJALV_HOME="/h", ARCADE_HOME="/a")
        self.assertEqual(env("HOME"), "/h")
        os.environ["HLIDSKJALV_HOME"] = ""
        self.assertEqual(env("HOME"), "/a")

    def test_unset_and_empty_are_none(self):
        self.assertIsNone(env("TRIODITIS_LOG"))

    def test_trioditis_log_keeps_its_oldest_name(self):
        os.environ["ARCADE_SWITCHBOARD_LOG"] = "/old.jsonl"
        self.assertEqual(env("TRIODITIS_LOG"), "/old.jsonl")


class DataHomeTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        for p in (mock.patch.dict(os.environ, {n: "" for n in ALL_NAMES}), mock.patch.object(Path, "home", return_value=self.home)):
            p.start()
            self.addCleanup(p.stop)

    def old(self, name, content="kept"):
        d = self.home / name
        d.mkdir()
        (d / "settings.json").write_text(content, encoding="utf-8")
        return d

    def test_fresh_install_uses_dot_lunete(self):
        self.assertEqual(M.data_home(), self.home / ".lunete")
        self.assertTrue((self.home / ".lunete" / "sessions").is_dir())

    def test_env_folder_is_used_as_given(self):
        given = self.home / "elsewhere"
        os.environ["LUNETE_HOME"] = str(given)
        self.assertEqual(M.data_home(), given)
        self.assertFalse((self.home / ".lunete").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_hlidskjalv_folder_moves_once_and_leaves_a_link(self):
        self.old(".hlidskjalv")
        home = M.data_home()
        self.assertEqual(home, self.home / ".lunete")
        self.assertTrue((self.home / ".hlidskjalv").is_symlink())
        self.assertEqual((self.home / ".hlidskjalv" / "settings.json").read_text(encoding="utf-8"), "kept")
        self.assertEqual(M.data_home(), home)  # second start: nothing moves again

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_arcade_link_to_hlidskjalv_still_resolves(self):
        """The layout a 0.3/0.4 user has: ~/.arcade -> ~/.hlidskjalv (a real folder)."""
        self.old(".hlidskjalv")
        os.symlink(self.home / ".hlidskjalv", self.home / ".arcade", target_is_directory=True)
        M.data_home()
        self.assertEqual((self.home / ".arcade" / "settings.json").read_text(encoding="utf-8"), "kept")

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_arcade_folder_alone_moves_too(self):
        self.old(".arcade")
        self.assertEqual(M.data_home(), self.home / ".lunete")
        self.assertTrue((self.home / ".arcade").is_symlink())

    def test_no_link_means_no_move(self):
        self.old(".hlidskjalv")
        with mock.patch.object(M.os, "symlink", side_effect=OSError("no symlink rights")):
            home = M.data_home()
        self.assertEqual(home, self.home / ".hlidskjalv")
        self.assertFalse((self.home / ".hlidskjalv").is_symlink())
        self.assertFalse((self.home / ".lunete").exists())

    def test_existing_lunete_wins_and_old_stays(self):
        self.old(".hlidskjalv", "old")
        self.old(".lunete", "new")
        self.assertEqual(M.data_home(), self.home / ".lunete")
        self.assertFalse((self.home / ".hlidskjalv").is_symlink())


class NameTest(unittest.TestCase):
    def test_both_commands_start_the_same_program(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        scripts = dict(re.findall(r'^(\w+) = "([\w.:]+)"', text.split("[project.scripts]")[1].split("[")[0], re.M))
        self.assertEqual(scripts, {"lunete": "lunete.__main__:main", "hlidskjalv": "lunete.__main__:main"})

    def test_shell_hooks_read_the_new_name_first(self):
        for shell in ("bash", "zsh"):
            self.assertIn("${LUNETE_HOME:-${HLIDSKJALV_HOME:-${ARCADE_HOME:-$HOME/.lunete}}}", M.HOOKS[shell])
        self.assertIn("$env:LUNETE_HOME", M.HOOKS["powershell"])

    def test_kept_wire_ids(self):
        """Renaming these would unpair browsers, break sync between machines or orphan installed units."""
        from lunete import autostart, sync
        self.assertEqual(sync.BLOB_NAME, "hlidskjalv-sync.json")
        self.assertEqual((autostart.UNIT, autostart.LABEL, autostart.TASK), ("hlidskjalv.service", "io.github.hlidskjalv.watch", "Hlidskjalv"))
        self.assertIn('"X-Hlidskjalv-Key"', (ROOT / "lunete" / "static" / "app.js").read_text(encoding="utf-8"))


    def test_old_local_address_still_allowed(self):
        from lunete import server
        self.assertEqual(server.NICE_HOST, "lunete.localhost")
        policy = server.csp(8788)
        self.assertIn("ws://lunete.localhost:8788", policy)
        self.assertIn("ws://hlidskjalv.localhost:8788", policy)


if __name__ == "__main__":
    unittest.main()


class OldModuleName(unittest.TestCase):
    """0.4 login items and services run `python -m hlidskjalv`; they must keep starting on 0.5."""

    def test_python_m_hlidskjalv_runs_lunete(self):
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        out = subprocess.run([sys.executable, "-m", "hlidskjalv", "--help"], cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("lunete", out.stdout.lower())
        self.assertIn("python -m lunete", out.stderr)
