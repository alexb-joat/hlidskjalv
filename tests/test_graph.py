import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lunete import graph, ops
from lunete.graph import GraphError, Malformed, Store, canon, strict_loads
from lunete.ids import is_uuid7, uuid7


def new_store(space="personal"):
    d = Path(tempfile.mkdtemp())
    return Store.create(d / "graph" / f"{space}.db", space)


def create(store, kind="task", title="t", channel="cli", **patch):
    item = str(uuid7())
    r = ops.apply(store, ops.make(store, "item.create", item, {"kind": kind, "title": title, **patch}), channel)
    assert r["ok"], r
    return item


class StrictParsing(unittest.TestCase):
    def refused(self, text, word):
        with self.assertRaises(Malformed) as cm:
            strict_loads(text)
        self.assertIn(word, str(cm.exception))

    def test_refuses_what_section_4_lists(self):
        self.refused('{"a":1,"a":2}', "duplicate key")
        self.refused('{"a":NaN}', "NaN")
        self.refused('{"a":Infinity}', "Infinity")
        self.refused('{"a":1.5}', "decimal string")
        self.refused('{"a":1e3}', "decimal string")
        self.refused('{"a":"\\ud800"}', "lone surrogate")
        self.refused(f'{{"a":{1 << 63}}}', "64 bits")
        self.refused("[" * 9 + "]" * 9, "levels deep")
        self.refused("{nope", "not JSON")

    def test_accepts_plain_values(self):
        self.assertEqual(strict_loads('{"a":[1,-2,"x",true,null]}'), {"a": [1, -2, "x", True, None]})
        self.assertEqual(strict_loads(f'{{"a":{(1 << 63) - 1}}}')["a"], (1 << 63) - 1)

    def test_canon_sorts_keys_keeps_unicode_and_strings_as_given(self):
        self.assertEqual(canon({"b": 1, "a": "č́"}), '{"a":"č́","b":1}')
        with self.assertRaises(Malformed):
            canon({"a": 0.5})


class StoreFiles(unittest.TestCase):
    def test_create_gives_a_random_org_id_never_local(self):
        a, b = new_store(), new_store()
        self.assertTrue(is_uuid7(a.org))
        self.assertNotEqual(a.org, b.org)
        self.assertEqual(a.space, "personal")
        self.assertEqual(a.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(a.conn.execute("PRAGMA secure_delete").fetchone()[0], 1)

    def test_create_refuses_an_existing_file_and_a_bad_space(self):
        s = new_store()
        with self.assertRaises(GraphError):
            Store.create(s.path, "personal")
        with self.assertRaises(GraphError):
            Store.create(s.path.parent / "x.db", "team")

    def test_open_read_only_when_the_view_lags_the_log(self):
        s = new_store()
        create(s)
        s.conn.execute("UPDATE meta SET value = '0' WHERE key = 'applied_seq'")
        s.close()
        again = Store.open(s.path)
        self.assertIn("read-only", again.problem)
        self.assertIn("lunete graph check", again.problem)
        with self.assertRaises(GraphError):
            again.transaction()

    def test_open_names_a_missing_file_and_a_foreign_file(self):
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(GraphError):
            Store.open(d / "none.db")
        (d / "junk.db").write_bytes(b"not sqlite at all, not even close" * 10)
        with self.assertRaises(GraphError):
            Store.open(d / "junk.db")


class ChainAndCheck(unittest.TestCase):
    def test_hash_chain_over_stored_text(self):
        s = new_store()
        create(s)
        create(s)
        rows = list(s.ops())
        self.assertEqual(rows[0]["hash"], graph.chain_hash(graph.ZERO_HASH, rows[0]["text"]))
        self.assertEqual(rows[1]["hash"], graph.chain_hash(rows[0]["hash"], rows[1]["text"]))
        self.assertTrue(graph.check(s)["ok"])

    def test_check_finds_tampered_text(self):
        s = new_store()
        item = create(s, title="before")
        s.conn.execute("UPDATE ops SET text = replace(text, 'before', 'after!') WHERE seq = 1")
        report = graph.check(s)
        self.assertFalse(report["ok"])
        self.assertTrue(any("hash doesn't match" in p for p in report["problems"]), report)
        self.assertTrue(item)

    def test_check_finds_a_view_the_log_doesnt_explain(self):
        s = new_store()
        item = create(s, title="real")
        s.conn.execute("UPDATE items SET title = 'edited behind the log' WHERE id = ?", (item,))
        report = graph.check(s)
        self.assertTrue(any("view table items" in p for p in report["problems"]), report)

    def test_rebuild_from_the_log_gives_the_same_view(self):
        s = new_store()
        line = create(s, kind="line", title="Serbian")
        ev = create(s, kind="event", title="audio", fields={"minutes": 20})
        ops.apply(s, ops.make(s, "link.add", {"from": ev, "rel": "on_line", "to": line}, {}), "cli")
        before = {t: graph._rows(s.conn, t) for t in graph.VIEW_TABLES}
        self.assertEqual(graph.rebuild(s), 3)
        self.assertEqual({t: graph._rows(s.conn, t) for t in graph.VIEW_TABLES}, before)


class Payloads(unittest.TestCase):
    def test_content_sits_beside_the_log_salted(self):
        s = new_store()
        person, ref = str(uuid7()), str(uuid7())
        r = ops.apply(s, ops.make(s, "item.create", person, {"kind": "person", "title": "", "payload_ref": ref}),
                      "cli", payloads={ref: "Ana Example"})
        self.assertTrue(r["ok"], r)
        for row in s.ops():
            self.assertNotIn("Ana Example", row["text"])
        env = json.loads(next(iter(s.ops()))["text"])
        salt = s.conn.execute("SELECT salt FROM payloads WHERE ref = ?", (ref,)).fetchone()[0]
        self.assertEqual(env["payload_salted_hash"], graph.salted_hash(salt, "Ana Example"))
        self.assertEqual(graph.check(s)["counts"]["payloads"], 1)

    def test_erase_keeps_the_chain_and_check_tells_erased_from_lost(self):
        s = new_store()
        a, ra = str(uuid7()), str(uuid7())
        b, rb = str(uuid7()), str(uuid7())
        ops.apply(s, ops.make(s, "item.create", a, {"kind": "person", "payload_ref": ra}), "cli", payloads={ra: "A"})
        ops.apply(s, ops.make(s, "item.create", b, {"kind": "person", "payload_ref": rb}), "cli", payloads={rb: "B"})
        self.assertTrue(ops.apply(s, ops.make(s, "payload.erase", a, {"payload_ref": ra}), "cli")["ok"])
        row = s.conn.execute("SELECT salt, content, erased_seq FROM payloads WHERE ref = ?", (ra,)).fetchone()
        self.assertEqual((row[0], row[1]), (None, None))
        s.conn.execute("DELETE FROM payloads WHERE ref = ?", (rb,))
        report = graph.check(s)
        self.assertEqual((report["counts"]["erased"], report["counts"]["lost"]), (1, 1))
        self.assertFalse(any("hash doesn't match" in p for p in report["problems"]))


class Writers(unittest.TestCase):
    def test_seq_has_no_gaps_across_two_connections(self):
        s = new_store()
        other = Store.open(s.path)
        for i in range(10):
            create(s if i % 2 else other)
        seqs = [r["seq"] for r in s.ops()]
        self.assertEqual(seqs, list(range(1, 11)))
        self.assertTrue(graph.check(s)["ok"])

    def test_connection_is_in_autocommit_mode(self):
        s = new_store()
        self.assertIsNone(s.conn.isolation_level)
        self.assertIsInstance(s.conn, sqlite3.Connection)


if __name__ == "__main__":
    unittest.main()
