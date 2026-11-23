"""The graph store: one SQLite file per store, holding the change log (`ops`) and the state view built from it.

docs/design/architecture.md is the contract; the section numbers below point into it.

- Strict parsing and canonical JSON (section 4): `strict_loads` refuses duplicate keys, NaN and
  Infinity, floats, lone surrogates, integers outside signed 64 bits, more than 8 levels of nesting
  and envelopes over 64 KB. `canon` is the one serialisation: keys sorted, no spaces, UTF-8.
- The log (section 7): `seq` has no gaps, each change is one `BEGIN IMMEDIATE` transaction, and the
  stored text is never re-serialised. `hash = sha256(prev_hash + "\\n" + text)`.
- The view (`items`, `links`, `field_prov`) is what `effect()` makes of accepted changes. It never
  reads the wall clock, so `replay()` rebuilds the same view. `effect()` keeps one branch per
  `rules_v`; a new rules version adds a branch and never edits an old one.
- Judging a change (is it allowed?) lives in ops.py. This module only stores and replays.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .ids import Hlc, parse_stamp, uuid7

SCHEMA_VERSION = 1
RULES_V = 1
MAX_ENVELOPE = 64 * 1024
MAX_DEPTH = 8
ZERO_HASH = "0" * 64
INT64_MIN, INT64_MAX = -(1 << 63), (1 << 63) - 1
SPACES = ("personal", "org")
CLIENT_FIELDS = ("v", "op_id", "org", "space", "device", "device_seq", "hlc", "base_seq", "kind", "target",
                 "patch", "reason", "payload_hash", "preview_hash")
STORE_FIELDS = ("actor", "payload_salted_hash", "seq", "epoch", "rules_v", "prev_hash", "hash", "auth_ref")
VIEW_TABLES = ("items", "links", "field_prov")


class GraphError(Exception):
    """A store can't be opened or written. The message names the cause, the place and the fix."""


class Malformed(ValueError):
    """Input that doesn't parse under the strict rules (section 4)."""


# ---------------------------------------------------------------- parsing and canonical JSON

def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise Malformed(f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_constant(name):
    raise Malformed(f"{name} is not allowed")


def _no_float(text):
    raise Malformed(f"fractional number {text}: send it as a decimal string (\"{text}\")")


def _int(text):
    n = int(text)
    if not INT64_MIN <= n <= INT64_MAX:
        raise Malformed(f"integer {text} is outside signed 64 bits")
    return n


def _walk(value, depth=1):
    """Refuse what json can't express safely: floats, lone surrogates, deep nesting."""
    if depth > MAX_DEPTH:
        raise Malformed(f"nested more than {MAX_DEPTH} levels deep")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise Malformed("object keys must be strings")
            _walk(k, depth)
            _walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _walk(v, depth + 1)
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise Malformed("a string holds a lone surrogate") from None
    elif isinstance(value, float):
        raise Malformed(f"fractional number {value!r}: send it as a decimal string")
    elif isinstance(value, int) and not isinstance(value, bool):
        if not INT64_MIN <= value <= INT64_MAX:
            raise Malformed(f"integer {value} is outside signed 64 bits")
    elif value is not None and not isinstance(value, bool):
        raise Malformed(f"{type(value).__name__} is not a JSON value")


def strict_loads(text: str | bytes):
    """Parse one JSON document under the strict rules. Raises Malformed with the reason."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise Malformed("not UTF-8") from None
    if len(text.encode("utf-8", "surrogatepass")) > MAX_ENVELOPE * 2:  # cheap guard before parsing
        raise Malformed(f"larger than {MAX_ENVELOPE // 1024} KB")
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_no_constant,
                           parse_float=_no_float, parse_int=_int)
    except Malformed:
        raise
    except (ValueError, RecursionError) as e:
        raise Malformed(f"not JSON: {e}") from None
    _walk(value)
    return value


def canon(value) -> str:
    """Canonical JSON: keys sorted, no spaces, UTF-8 without escapes, strings exactly as given."""
    _walk(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def payload_hash(kind, target, patch) -> str:
    body = {"kind": kind, "patch": patch}
    if target is not None:
        body["target"] = target
    return sha256_hex(canon(body))


def client_hash(env: dict) -> str:
    return sha256_hex(canon({k: env[k] for k in CLIENT_FIELDS if k in env}))


def chain_hash(prev_hash: str, text: str) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + b"\n" + text.encode("utf-8")).hexdigest()


def salted_hash(salt: bytes, content: str) -> str:
    return hashlib.sha256(salt + content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- schema

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ops(
  seq INTEGER PRIMARY KEY,
  op_id TEXT NOT NULL UNIQUE,
  refused_op_id TEXT,
  target TEXT,
  kind TEXT NOT NULL,
  client_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  hash TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ops_refused ON ops(refused_op_id) WHERE refused_op_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ops_target ON ops(target);
CREATE TABLE IF NOT EXISTS payloads(ref TEXT PRIMARY KEY, salt BLOB, content TEXT, erased_seq INTEGER);
"""

_VIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, fields TEXT NOT NULL,
  source TEXT NOT NULL, external_id TEXT, class TEXT NOT NULL, payload_ref TEXT, sponsor TEXT,
  deleted INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  created_seq INTEGER NOT NULL, updated_seq INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS items_ext ON items(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS items_kind ON items(kind);
CREATE TABLE IF NOT EXISTS links(
  from_id TEXT NOT NULL, rel TEXT NOT NULL, to_id TEXT NOT NULL, to_org TEXT, live INTEGER NOT NULL,
  added_seq INTEGER NOT NULL, added_by_kind TEXT NOT NULL, added_by_id TEXT,
  removed_seq INTEGER, removed_by_kind TEXT, removed_by_id TEXT,
  PRIMARY KEY(from_id, rel, to_id));
CREATE INDEX IF NOT EXISTS links_to ON links(to_id, rel);
CREATE TABLE IF NOT EXISTS field_prov(
  item TEXT NOT NULL, field TEXT NOT NULL, seq INTEGER NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT,
  PRIMARY KEY(item, field));
"""


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def view_schema(conn: sqlite3.Connection) -> None:
    """Make the view tables. One statement at a time: executescript would commit an open transaction."""
    for stmt in _VIEW_SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


# ---------------------------------------------------------------- the store

class Store:
    """One store file. Create it with `Store.create`, open it with `Store.open`.

    Writes go through `transaction()`; ops.apply is the only writer of the log."""

    def __init__(self, path: Path, conn: sqlite3.Connection):
        self.path = Path(path)
        self.conn = conn
        self.problem: str | None = None  # set when the store opened read-only
        self.meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        self.org = self.meta["org"]
        self.space = self.meta["space"]
        self.device = self.meta["store_device"]
        self.clock = Hlc(self.device)
        high = self.meta.get("hlc_high")
        if high:
            self.clock.seen(high, max_ahead_ms=None)
        seq, _ = self.head()
        applied = int(self.meta.get("applied_seq", "0"))
        if applied != seq:
            self.problem = (f"{self.path}: the view is at change {applied} but the log ends at {seq}, so it opened "
                            f"read-only. Run `lunete graph check` to see what differs.")

    @classmethod
    def create(cls, path, space: str) -> Store:
        """Make a new store file with its own random org id (architecture section 7)."""
        path = Path(path)
        if space not in SPACES:
            raise GraphError(f"space {space!r}: use 'personal' or 'org'")
        if path.exists():
            raise GraphError(f"{path} already exists; open it instead of creating it")
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        conn = _connect(path)
        if os.name == "posix":
            os.chmod(path, 0o600)
        conn.executescript(_SCHEMA)
        view_schema(conn)
        org = str(uuid7())
        meta = {
            "schema": str(SCHEMA_VERSION), "rules_v": str(RULES_V), "org": org, "space": space, "epoch": "1",
            "applied_seq": "0", "store_device": "store-" + uuid7().hex[-12:], "store_device_seq": "0",
            "owner": str(uuid7()), "local_device": "local-" + uuid7().hex[-12:], "local_device_seq": "0",
        }
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", meta.items())
        conn.execute("COMMIT")
        return cls(path, conn)

    @classmethod
    def open(cls, path) -> Store:
        path = Path(path)
        if not path.exists():
            raise GraphError(f"{path} doesn't exist; it is made on first use, or with Store.create")
        conn = None
        try:
            conn = _connect(path)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        except sqlite3.DatabaseError as e:
            if conn is not None:
                conn.close()
            raise GraphError(f"{path} isn't a Lunete store ({e}); move it aside and start a new one") from None
        if row is None or row["value"] != str(SCHEMA_VERSION):
            conn.close()
            raise GraphError(f"{path} has schema {row['value'] if row else 'none'}, this Lunete reads "
                             f"{SCHEMA_VERSION}; update Lunete or rebuild the store by replay")
        return cls(path, conn)

    @classmethod
    def open_or_create(cls, path, space: str) -> Store:
        path = Path(path)
        return cls.open(path) if path.exists() else cls.create(path, space)

    def close(self) -> None:
        self.conn.close()

    # -- reading
    def head(self) -> tuple[int, str]:
        row = self.conn.execute("SELECT seq, hash FROM ops ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["hash"]) if row else (0, ZERO_HASH)

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def item(self, item_id: str):
        return self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def items(self, kind: str | None = None, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM items WHERE 1=1"
        args: list = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if not include_deleted:
            sql += " AND deleted = 0"
        return [_item_dict(r) for r in self.conn.execute(sql + " ORDER BY created_seq", args)]

    def links(self, rel: str | None = None, live_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM links WHERE 1=1"
        args: list = []
        if rel:
            sql += " AND rel = ?"
            args.append(rel)
        if live_only:
            sql += " AND live = 1"
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY added_seq", args)]

    # -- writing
    def transaction(self):
        if self.problem:
            raise GraphError(self.problem)
        return _Txn(self.conn)

    def set_meta(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (key, str(value)))
        self.meta[key] = str(value)

    def bump_meta(self, key: str) -> int:
        n = int(self.get_meta(key, "0")) + 1
        self.set_meta(key, n)
        return n

    def append(self, env: dict, *, refused_op_id: str | None, chash: str) -> tuple[int, str]:
        """Number, chain and store one change (inside a transaction). Returns (seq, hash)."""
        seq, prev = self.head()
        seq += 1
        env = dict(env, seq=seq, prev_hash=prev, epoch=1, rules_v=RULES_V, auth_ref="local")
        env.pop("hash", None)
        text = canon(env)
        if len(text.encode("utf-8")) > MAX_ENVELOPE:
            raise Malformed(f"larger than {MAX_ENVELOPE // 1024} KB")
        h = chain_hash(prev, text)
        target = env.get("target")
        if isinstance(target, dict):
            target = target.get("from")
        self.conn.execute("INSERT INTO ops(seq, op_id, refused_op_id, target, kind, client_hash, text, hash) "
                          "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                          (seq, env["op_id"], refused_op_id, target, env["kind"], chash, text, h))
        self.set_meta("applied_seq", seq)
        stamp = env.get("hlc")
        if stamp and stamp > (self.get_meta("hlc_high") or ""):
            self.set_meta("hlc_high", stamp)
        return seq, h

    def ops(self):
        """Every change in seq order, as (seq, text, hash) rows."""
        return self.conn.execute("SELECT seq, op_id, refused_op_id, kind, client_hash, text, hash FROM ops ORDER BY seq")


class _Txn:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def _item_dict(row) -> dict:
    d = dict(row)
    d["fields"] = json.loads(d["fields"])
    d["deleted"] = bool(d["deleted"])
    return d


# ---------------------------------------------------------------- effects (the view), frozen per rules_v

def effect(conn: sqlite3.Connection, env: dict) -> None:
    """Apply an accepted change to the view tables. Never judges and never reads the wall clock."""
    branch = _EFFECTS.get(env.get("rules_v", RULES_V))
    if branch is None:
        raise GraphError(f"change {env.get('seq')} was written under rules {env.get('rules_v')}, which this Lunete "
                         f"doesn't know; update Lunete")
    branch(conn, env)


def _prov(conn, item, field, env):
    actor = env.get("actor") or {}
    conn.execute("INSERT INTO field_prov(item, field, seq, actor_kind, actor_id) VALUES(?, ?, ?, ?, ?) "
                 "ON CONFLICT(item, field) DO UPDATE SET seq = excluded.seq, actor_kind = excluded.actor_kind, "
                 "actor_id = excluded.actor_id", (item, field, env["seq"], actor.get("kind", ""), actor.get("id")))


def _touch(conn, item, env):
    conn.execute("UPDATE items SET updated_at = ?, updated_seq = ? WHERE id = ?", (env["hlc"], env["seq"], item))


def _effect_v1(conn, env):
    kind, target, patch, seq = env["kind"], env.get("target"), env.get("patch", {}), env["seq"]
    actor = env.get("actor") or {}
    if kind == "refusal":
        return
    if kind == "item.create":
        fields = patch.get("fields", {})
        conn.execute(
            "INSERT INTO items(id, kind, title, status, fields, source, external_id, class, payload_ref, sponsor, "
            "deleted, created_at, updated_at, deleted_at, created_seq, updated_seq) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, 0, ?, ?, NULL, ?, ?)",
            (target, patch["kind"], patch.get("title", ""), patch.get("status", ""), canon(fields),
             patch.get("source", "typed"), patch.get("external_id"), patch.get("payload_ref"), patch.get("sponsor"),
             env["hlc"], env["hlc"], seq, seq))
        for f in ("title", "status"):
            _prov(conn, target, f, env)
        if "payload_ref" in patch:
            _prov(conn, target, "payload_ref", env)
        for k in fields:
            _prov(conn, target, "fields." + k, env)
        if "sponsor" in patch:
            _link_up(conn, target, "sponsored_by", patch["sponsor"], None, env)
    elif kind == "item.update":
        row = conn.execute("SELECT fields FROM items WHERE id = ?", (target,)).fetchone()
        fields = json.loads(row["fields"])
        for k, v in patch.get("fields", {}).items():
            if v is None:
                fields.pop(k, None)
            else:
                fields[k] = v
            _prov(conn, target, "fields." + k, env)
        conn.execute("UPDATE items SET fields = ? WHERE id = ?", (canon(fields), target))
        if "title" in patch:
            conn.execute("UPDATE items SET title = ? WHERE id = ?", (patch["title"], target))
            _prov(conn, target, "title", env)
        _touch(conn, target, env)
    elif kind == "item.status":
        conn.execute("UPDATE items SET status = ? WHERE id = ?", (patch["to"], target))
        _prov(conn, target, "status", env)
        _touch(conn, target, env)
    elif kind == "item.delete":
        conn.execute("UPDATE items SET deleted = 1, deleted_at = ? WHERE id = ?", (env["hlc"], target))
        _touch(conn, target, env)
    elif kind == "item.restore":
        conn.execute("UPDATE items SET deleted = 0, deleted_at = NULL WHERE id = ?", (target,))
        _touch(conn, target, env)
    elif kind == "link.add":
        _link_up(conn, target["from"], target["rel"], target["to"], patch.get("to_org"), env)
    elif kind == "link.remove":
        conn.execute("UPDATE links SET live = 0, removed_seq = ?, removed_by_kind = ?, removed_by_id = ? "
                     "WHERE from_id = ? AND rel = ? AND to_id = ?",
                     (seq, actor.get("kind", ""), actor.get("id"), target["from"], target["rel"], target["to"]))
    elif kind == "agent.sponsor":
        conn.execute("DELETE FROM links WHERE from_id = ? AND rel = 'sponsored_by'", (target,))
        conn.execute("UPDATE items SET sponsor = ? WHERE id = ?", (patch["to"], target))
        _link_up(conn, target, "sponsored_by", patch["to"], None, env)
        _prov(conn, target, "sponsor", env)
        _touch(conn, target, env)
    elif kind == "payload.set":
        conn.execute("UPDATE items SET payload_ref = ? WHERE id = ?", (patch["payload_ref"], target))
        _prov(conn, target, "payload_ref", env)
        _touch(conn, target, env)
    elif kind == "payload.erase":
        pass  # the content lives outside the log; the view holds only the ref
    else:
        raise GraphError(f"change {seq}: kind {kind!r} has no effect under rules 1")


def _link_up(conn, frm, rel, to, to_org, env):
    actor = env.get("actor") or {}
    conn.execute(
        "INSERT INTO links(from_id, rel, to_id, to_org, live, added_seq, added_by_kind, added_by_id) "
        "VALUES(?, ?, ?, ?, 1, ?, ?, ?) ON CONFLICT(from_id, rel, to_id) DO UPDATE SET live = 1, "
        "to_org = excluded.to_org, added_seq = excluded.added_seq, added_by_kind = excluded.added_by_kind, "
        "added_by_id = excluded.added_by_id, removed_seq = NULL, removed_by_kind = NULL, removed_by_id = NULL",
        (frm, rel, to, to_org, env["seq"], actor.get("kind", ""), actor.get("id")))


_EFFECTS = {1: _effect_v1}


# ---------------------------------------------------------------- replay and check

def replay(texts, conn: sqlite3.Connection) -> int:
    """Apply stored change texts, in seq order, to empty view tables in `conn`. Returns how many applied."""
    n = 0
    for text in texts:
        env = json.loads(text)
        if env["kind"] == "refusal":
            continue
        effect(conn, env)
        n += 1
    return n


def rebuild(store: Store) -> int:
    """Drop the view tables and rebuild them from the log (the migration path, section 2 item 10).
    This is also the repair for a store that opened read-only because its view lagged the log."""
    store.problem = None
    with store.transaction() as conn:
        for t in VIEW_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        view_schema(conn)
        n = replay((r["text"] for r in store.ops()), conn)
        store.set_meta("applied_seq", store.head()[0])
    return n


def _rows(conn, table):
    keys = {"items": "id", "links": "from_id, rel, to_id", "field_prov": "item, field"}[table]
    return [canon(dict(r)) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {keys}")]  # noqa: S608 (names from VIEW_TABLES)


def check(store: Store) -> dict:
    """Read-only: verify the chain over the stored text, the payload hashes and the salted payload
    hashes, then replay in memory and compare with the view row by row. Returns counts and problems."""
    problems: list[str] = []
    counts = {"changes": 0, "accepted": 0, "refusals": 0, "items": 0, "links": 0,
              "payloads": 0, "erased": 0, "lost": 0, "rules_versions": []}
    prev, expect_seq = ZERO_HASH, 1
    texts = []
    salted: dict[str, str] = {}
    versions = set()
    for r in store.ops():
        counts["changes"] += 1
        text = r["text"]
        if r["seq"] != expect_seq:
            problems.append(f"change {r['seq']}: expected seq {expect_seq} (a gap in the log)")
        expect_seq = r["seq"] + 1
        if chain_hash(prev, text) != r["hash"]:
            problems.append(f"change {r['seq']}: its hash doesn't match the stored text and the previous hash")
        try:
            env = strict_loads(text)
        except Malformed as e:
            problems.append(f"change {r['seq']}: stored text doesn't parse ({e})")
            prev = r["hash"]
            continue
        if env.get("prev_hash") != prev:
            problems.append(f"change {r['seq']}: prev_hash inside the text isn't the previous change's hash")
        if env.get("payload_hash") != payload_hash(env.get("kind"), env.get("target"), env.get("patch")):
            problems.append(f"change {r['seq']}: payload_hash doesn't match its kind, target and patch")
        versions.add(env.get("rules_v"))
        if env.get("kind") == "refusal":
            counts["refusals"] += 1
        else:
            counts["accepted"] += 1
            texts.append(text)
            ref = (env.get("patch") or {}).get("payload_ref")
            if env.get("payload_salted_hash") and ref and env.get("kind") in ("item.create", "payload.set"):
                salted[ref] = env["payload_salted_hash"]
        prev = r["hash"]
    counts["rules_versions"] = sorted(v for v in versions if v is not None)
    applied = int(store.get_meta("applied_seq", "0"))
    if applied != counts["changes"]:
        problems.append(f"meta.applied_seq is {applied} but the log holds {counts['changes']} changes")
    for ref, want in salted.items():
        row = store.conn.execute("SELECT salt, content, erased_seq FROM payloads WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            counts["lost"] += 1
            problems.append(f"payload {ref}: missing, and no erasure is recorded (lost)")
        elif row["erased_seq"] is not None:
            counts["erased"] += 1
        else:
            counts["payloads"] += 1
            if salted_hash(row["salt"], row["content"]) != want:
                problems.append(f"payload {ref}: its content doesn't match the salted hash in the log")
    mem = sqlite3.connect(":memory:", isolation_level=None)
    mem.row_factory = sqlite3.Row
    view_schema(mem)
    try:
        replay(texts, mem)
    except (GraphError, sqlite3.DatabaseError, KeyError) as e:
        problems.append(f"replay failed: {e}")
    for table in VIEW_TABLES:
        want, have = _rows(mem, table), _rows(store.conn, table)
        if want != have:
            missing = len(set(want) - set(have))
            extra = len(set(have) - set(want))
            problems.append(f"view table {table}: {missing} row(s) the log makes are missing or differ, "
                            f"{extra} row(s) the log doesn't explain")
    mem.close()
    counts["items"] = store.conn.execute("SELECT count(*) FROM items").fetchone()[0]
    counts["links"] = store.conn.execute("SELECT count(*) FROM links WHERE live = 1").fetchone()[0]
    return {"counts": counts, "problems": problems, "ok": not problems}


def stamp_ms(stamp: str) -> int:
    return parse_stamp(stamp)[0]
