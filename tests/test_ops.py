import json
import random
import sqlite3
import unittest
from pathlib import Path

from lunete import graph, ops
from lunete.ids import Hlc, item_uuid5, uuid7

from .test_graph import create, new_store

GOLDEN = Path(__file__).parent / "golden"


def apply(store, kind, target, patch, channel="cli", **kw):
    return ops.apply(store, ops.make(store, kind, target, patch), channel, **kw)


class Envelope(unittest.TestCase):
    def setUp(self):
        self.s = new_store()

    def test_actor_comes_from_the_channel_never_the_client(self):
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task", "title": "x"})
        env["actor"] = {"kind": "human", "id": "someone-else"}
        r = ops.apply(self.s, env, "cli")
        self.assertEqual(r["reason"], "actor is set by the store, not the client")
        item = create(self.s)
        stored = json.loads(list(self.s.ops())[-1]["text"])
        self.assertEqual(stored["actor"], {"id": self.s.meta["owner"], "kind": "human"})
        self.assertTrue(item)

    def test_agent_writes_wait_for_b4(self):
        r = apply(self.s, "item.create", str(uuid7()), {"kind": "task"}, channel="agent")
        self.assertEqual(r["reason"], "agent writes wait for B4")

    def test_refusals_are_logged_without_the_patch(self):
        r = apply(self.s, "item.create", str(uuid7()), {"kind": "task", "title": "secret words", "class": "sealed"})
        self.assertEqual(r["reason"], "unknown key class")
        text = list(self.s.ops())[-1]["text"]
        self.assertNotIn("secret words", text)
        env = json.loads(text)
        self.assertEqual(env["kind"], "refusal")
        self.assertEqual(env["actor"], {"kind": "store"})
        self.assertEqual(env["patch"]["refused_actor"]["kind"], "human")

    def test_wrong_store_wrong_hash_preview_hash_and_ledger(self):
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        self.assertIn("don't match this store", ops.apply(self.s, dict(env, org=str(uuid7())), "cli")["reason"])
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        self.assertIn("payload_hash", ops.apply(self.s, dict(env, payload_hash="0" * 64), "cli")["reason"])
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        self.assertIn("B4", ops.apply(self.s, dict(env, preview_hash="0" * 64), "cli")["reason"])
        self.assertIn("ledger step", apply(self.s, "ledger.add", None, {"amount": -1})["reason"])
        self.assertIn("ledger.add", apply(self.s, "item.create", str(uuid7()), {"kind": "ledger"})["reason"])

    def test_clock_ahead_and_hlc_device_mismatch(self):
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        far = Hlc(env["device"], now_ms=lambda: 9_999_999_999_999).now()
        self.assertEqual(ops.apply(self.s, dict(env, hlc=far), "cli")["reason"], "clock ahead")
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        self.assertIn("device part", ops.apply(self.s, dict(env, device="other"), "cli")["reason"])

    def test_bools_are_not_numbers(self):
        env = ops.make(self.s, "item.create", str(uuid7()), {"kind": "task"})
        self.assertIn("whole number", ops.apply(self.s, dict(env, device_seq=True), "cli")["reason"])


class Duplicates(unittest.TestCase):
    def test_same_op_twice_gets_the_original_answer_and_writes_nothing(self):
        s = new_store()
        env = ops.make(s, "item.create", str(uuid7()), {"kind": "task", "title": "once"})
        first = ops.apply(s, env, "cli")
        again = ops.apply(s, json.dumps(env), "cli")
        self.assertEqual((again["seq"], again["duplicate"]), (first["seq"], True))
        self.assertEqual(s.head()[0], 1)

    def test_a_duplicate_refusal_gets_the_same_refusal(self):
        s = new_store()
        env = ops.make(s, "item.update", str(uuid7()), {"title": "x"})
        first = ops.apply(s, env, "cli")
        again = ops.apply(s, env, "cli")
        self.assertEqual((again["seq"], again["reason"]), (first["seq"], first["reason"]))
        self.assertEqual(s.head()[0], 1)

    def test_reused_op_id_is_not_logged(self):
        s = new_store()
        env = ops.make(s, "item.create", str(uuid7()), {"kind": "task", "title": "a"})
        ops.apply(s, env, "cli")
        other = dict(env, patch={"kind": "task", "title": "b"})
        other["payload_hash"] = graph.payload_hash("item.create", env["target"], other["patch"])
        self.assertEqual(ops.apply(s, other, "cli")["reason"], "op_id reused")
        self.assertEqual(s.head()[0], 1)
        self.assertEqual(s.get_meta("op_id_reused"), "1")


class Items(unittest.TestCase):
    def setUp(self):
        self.s = new_store()

    def test_stale_status_is_turned_down_with_its_reason(self):
        t = create(self.s, status="open")
        self.assertTrue(apply(self.s, "item.status", t, {"from": "open", "to": "done"})["ok"])
        r = apply(self.s, "item.status", t, {"from": "open", "to": "doing"})
        self.assertIn("status is 'done', not 'open'", r["reason"])

    def test_an_edit_to_a_deleted_item_is_turned_down(self):
        t = create(self.s)
        self.assertTrue(apply(self.s, "item.delete", t, {})["ok"])
        self.assertEqual(apply(self.s, "item.update", t, {"title": "x"})["reason"], "deleted")
        self.assertEqual(apply(self.s, "item.delete", t, {})["reason"], "already deleted")
        self.assertTrue(apply(self.s, "item.restore", t, {})["ok"])
        self.assertTrue(apply(self.s, "item.update", t, {"title": "back"})["ok"])

    def test_fields_merge_shallow_and_null_removes(self):
        t = create(self.s, fields={"a": 1, "b": {"x": 1}})
        apply(self.s, "item.update", t, {"fields": {"a": None, "b": {"y": 2}, "c": "3"}})
        self.assertEqual(self.s.items()[0]["fields"], {"b": {"y": 2}, "c": "3"})
        prov = {r["field"] for r in self.s.conn.execute("SELECT field FROM field_prov WHERE item = ?", (t,))}
        self.assertTrue({"fields.a", "fields.b", "fields.c", "title", "status"} <= prov)

    def test_typed_ids_are_uuid7_and_connector_ids_are_derived(self):
        self.assertIn("UUIDv7", apply(self.s, "item.create", "not-an-id", {"kind": "task"})["reason"])
        good = str(item_uuid5(self.s.org, "git", "abc123"))
        patch = {"kind": "commit", "source": "git", "external_id": "abc123"}
        self.assertIn("derived id", apply(self.s, "item.create", str(uuid7()), patch, channel="connector:git")["reason"])
        self.assertIn("own name", apply(self.s, "item.create", good, dict(patch, source="x"),
                                        channel="connector:git")["reason"])
        self.assertTrue(apply(self.s, "item.create", good, patch, channel="connector:git")["ok"])
        self.assertIn("already exists", apply(self.s, "item.create", good, patch, channel="connector:git")["reason"])

    def test_person_account_and_agent_rules(self):
        self.assertIn("payload", apply(self.s, "item.create", str(uuid7()), {"kind": "person", "title": "Ana"})["reason"])
        self.assertIn("currency", apply(self.s, "item.create", str(uuid7()), {"kind": "account"})["reason"])
        acct = create(self.s, kind="account", title="card", fields={"currency": "EUR"})
        self.assertIn("never changes", apply(self.s, "item.update", acct, {"fields": {"currency": "USD"}})["reason"])
        self.assertIn("sponsor", apply(self.s, "item.create", str(uuid7()), {"kind": "agent"})["reason"])
        p = create(self.s, kind="person", title="")
        a = create(self.s, kind="agent", title="helper", sponsor=p)
        links = self.s.links("sponsored_by")
        self.assertEqual((links[0]["from_id"], links[0]["to_id"]), (a, p))


class Links(unittest.TestCase):
    def setUp(self):
        self.s = new_store()

    def test_endpoint_rules(self):
        line, task, ev = create(self.s, kind="line"), create(self.s), create(self.s, kind="event")
        self.assertTrue(apply(self.s, "link.add", {"from": ev, "rel": "on_line", "to": line}, {})["ok"])
        self.assertIn("can't end at", apply(self.s, "link.add", {"from": ev, "rel": "on_line", "to": task}, {})["reason"])
        self.assertIn("same kind", apply(self.s, "link.add", {"from": task, "rel": "part_of", "to": line}, {})["reason"])
        self.assertIn("itself", apply(self.s, "link.add", {"from": task, "rel": "relates", "to": task}, {})["reason"])
        self.assertIn("already exists", apply(self.s, "link.add", {"from": ev, "rel": "on_line", "to": line}, {})["reason"])
        for rel in ("booked_to", "sponsored_by", "holder_of"):
            self.assertFalse(apply(self.s, "link.add", {"from": task, "rel": rel, "to": line}, {})["ok"])

    def test_a_loop_is_refused_one_relation_at_a_time(self):
        a, b, c = (create(self.s, title=n) for n in "abc")
        apply(self.s, "link.add", {"from": a, "rel": "depends_on", "to": b}, {})
        apply(self.s, "link.add", {"from": b, "rel": "depends_on", "to": c}, {})
        self.assertEqual(apply(self.s, "link.add", {"from": c, "rel": "depends_on", "to": a}, {})["reason"],
                         "would make a loop through a")
        self.assertTrue(apply(self.s, "link.add", {"from": c, "rel": "part_of", "to": a}, {})["ok"])

    def test_remove_tombstones_and_connectors(self):
        a, b = create(self.s), create(self.s)
        t = {"from": a, "rel": "relates", "to": b}
        self.assertEqual(apply(self.s, "link.remove", t, {})["reason"], "no such link")
        apply(self.s, "link.add", t, {})
        self.assertIn("only links it added", apply(self.s, "link.remove", t, {}, channel="connector:git")["reason"])
        apply(self.s, "item.delete", b, {})
        self.assertTrue(apply(self.s, "link.remove", t, {})["ok"])  # works when an end is deleted
        apply(self.s, "item.restore", b, {})
        self.assertIn("a person removed", apply(self.s, "link.add", t, {}, channel="connector:git")["reason"])
        self.assertTrue(apply(self.s, "link.add", t, {})["ok"])

    def test_cross_store_links_only_from_personal(self):
        task = create(self.s)
        other = str(uuid7())
        self.assertTrue(apply(self.s, "link.add", {"from": task, "rel": "relates", "to": str(uuid7())},
                              {"to_org": other})["ok"])
        org = new_store("org")
        t2 = create(org)
        self.assertIn("org item may not", apply(org, "link.add", {"from": t2, "rel": "relates", "to": str(uuid7())},
                                                {"to_org": other})["reason"])


class Platforms(unittest.TestCase):
    def refused(self, argv, os_="linux"):
        return ops.check_platforms([{"os": os_, "argv": argv}])

    def test_refused_programs_and_flags(self):
        for argv in (["bash"], ["/bin/sh", "x"], ["C:\\Windows\\cmd.exe", "/c", "dir"], ["sudo", "code"],
                     ["npx", "x"], ["npm", "exec", "x"], ["deno", "run"], ["awk", "{print}"],
                     ["python3", "-c", "print(1)"], ["python3.12", "-Ic", "x"], ["node", "--eval=1"],
                     ["perl", "-E", "x"], ["git", "-c", "core.sshCommand=x", "fetch"], ["ssh", "-oProxyCommand=x"],
                     ["find", ".", "-exec", "rm", "{}", ";"], ["gnome-terminal", "--", "vim"], ["konsole", "-e", "x"]):
            self.assertIsNotNone(self.refused(argv), argv)
        self.assertIsNotNone(self.refused(["tool.bat"], "windows"))
        self.assertIsNotNone(self.refused(["python", "tool.py", "-c", "3"]))  # turned down on purpose

    def test_plain_programs_pass(self):
        for argv in (["code", "/home/me/project"], ["python3", "-m", "http.server"], ["git", "-C", "repo", "status"],
                     ["xdg-open", "https://example.org"], ["npm", "test"], ["tool.bat"]):
            self.assertIsNone(self.refused(argv), argv)

    def test_shape(self):
        for bad in ([], [{"os": "linux"}], [{"os": "bsd", "argv": ["x"]}], [{"os": "linux", "argv": []}],
                    [{"os": "linux", "argv": ["a\nb"]}], [{"os": "linux", "argv": [""]}]):
            self.assertIsNotNone(ops.check_platforms(bad), bad)

    def test_checked_on_every_write(self):
        s = new_store()
        t = create(s)
        r = apply(s, "item.update", t, {"fields": {"platforms": [{"os": "linux", "argv": ["sh", "-c", "x"]}]}})
        self.assertIn("runs a line of code", r["reason"])


class Replay(unittest.TestCase):
    def test_replay_equals_the_live_view_on_500_seeded_random_changes(self):
        rng = random.Random(20261123)  # noqa: S311 (seeded test data, not secrets)
        s = new_store()
        ids: list[str] = []
        kinds = ["line", "task", "event", "doc"]
        rels = ["on_line", "part_of", "depends_on", "relates"]
        accepted = refused = 0
        for _ in range(500):
            roll = rng.random()
            pick = (lambda: rng.choice(ids)) if ids else (lambda: str(uuid7()))
            if roll < 0.25 or not ids:
                item = str(uuid7())
                r = apply(s, "item.create", item, {"kind": rng.choice(kinds), "title": f"n{rng.randint(0, 99)}",
                                                   "status": rng.choice(["open", "done"]),
                                                   "fields": {"n": rng.randint(-5, 5)}})
                if r["ok"]:
                    ids.append(item)
            elif roll < 0.45:
                r = apply(s, "item.update", pick(), {"fields": {rng.choice("abc"): rng.choice([None, 1, "x"])},
                                                     "title": f"u{rng.randint(0, 9)}"})
            elif roll < 0.6:
                r = apply(s, "item.status", pick(), {"from": rng.choice(["open", "done"]),
                                                     "to": rng.choice(["open", "done", "doing"])})
            elif roll < 0.7:
                r = apply(s, rng.choice(["item.delete", "item.restore"]), pick(), {})
            elif roll < 0.9:
                r = apply(s, "link.add", {"from": pick(), "rel": rng.choice(rels), "to": pick()}, {})
            else:
                r = apply(s, "link.remove", {"from": pick(), "rel": rng.choice(rels), "to": pick()}, {})
            accepted += r["ok"]
            refused += not r["ok"]
        self.assertGreater(accepted, 150)
        self.assertGreater(refused, 50)
        report = graph.check(s)
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["counts"]["changes"], 500)

    def test_golden_log_replays_to_the_fixed_view(self):
        texts = (GOLDEN / "graph-v1.jsonl").read_text(encoding="utf-8").splitlines()
        want = json.loads((GOLDEN / "graph-v1-view.json").read_text(encoding="utf-8"))
        prev = graph.ZERO_HASH
        for line in texts:  # the chain still verifies over the stored text
            rec = json.loads(line)
            self.assertEqual(graph.chain_hash(prev, rec["text"]), rec["hash"])
            prev = rec["hash"]
        mem = sqlite3.connect(":memory:", isolation_level=None)
        mem.row_factory = sqlite3.Row
        graph.view_schema(mem)
        graph.replay((json.loads(line)["text"] for line in texts), mem)
        self.assertEqual({t: graph._rows(mem, t) for t in graph.VIEW_TABLES}, want)


if __name__ == "__main__":
    unittest.main()
