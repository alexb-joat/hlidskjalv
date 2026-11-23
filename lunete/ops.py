"""Applying a change: judge it, then append it to the log and update the view, or log a refusal.

`apply(store, envelope, channel)` is the one way to write a graph store (architecture section 4).
- The channel says who sent it: "cli" and "page" are the human who owns this install,
  "connector:<name>" is that connector, "agent" is refused ("agent writes wait for B4").
  `actor` comes from the channel, never from the client.
- A change the rules turn down is never applied; a `refusal` op is appended instead, holding the
  refused op_id, kind, target, actor and reason (never the patch).
- The same op_id with the same client fields gets the original answer and writes nothing; the same
  op_id with different fields gets "op_id reused" and isn't logged.
- `make(store, kind, target, patch)` builds a complete envelope for the local channels.

Ledger entries (`ledger.add`, `ledger.reverse`) are refused in this version; they come with the
ledger step, under a new rules branch.
"""
from __future__ import annotations

import os
import re

from . import graph
from .graph import CLIENT_FIELDS, STORE_FIELDS, Malformed, canon, client_hash, payload_hash, sha256_hex
from .ids import is_item_id, is_uuid7, item_uuid5, parse_stamp, uuid7

ITEM_KINDS = ("line", "task", "event", "person", "agent", "run", "commit", "doc", "ledger", "account", "budget", "fund")
CURRENCIES = {"EUR": 2, "USD": 2, "RSD": 2}
MAX_TITLE = 200
MAX_STATUS = 40
MAX_REASON = 1000
MAX_PAYLOAD = 16 * 1024
DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FIELD_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

PATCH_KEYS = {
    "item.create": {"kind", "title", "status", "fields", "source", "external_id", "payload_ref", "sponsor"},
    "item.update": {"fields", "title"},
    "item.status": {"from", "to"},
    "item.delete": set(),
    "item.restore": set(),
    "link.add": {"to_org"},
    "link.remove": set(),
    "agent.sponsor": {"from", "to"},
    "payload.set": {"payload_ref"},
    "payload.erase": {"payload_ref"},
    "ledger.add": None,  # refused before its keys are looked at
    "ledger.reverse": None,
}

# relation: (allowed from kinds, allowed to kinds or "same"); None = any
LINK_RULES = {
    "on_line": ({"task", "event", "run", "commit", "doc", "agent"}, {"line"}),
    "part_of": ({"task", "line"}, "same"),
    "depends_on": ({"task"}, {"task", "event"}),
    "assigned_to": ({"line", "task"}, {"person", "agent"}),
    "produced_by": ({"commit", "doc", "run"}, {"person", "agent", "run"}),
    "spawned_by": ({"run"}, {"run", "agent"}),
    "funds": ({"fund"}, {"line", "budget"}),
    "relates": (None, None),
}
LINK_REFUSED = {
    "booked_to": "booked_to links are set only by ledger.add",
    "sponsored_by": "sponsored_by mirrors an agent's sponsor; change it with agent.sponsor",
    "holder_of": "holder_of is reserved for sealed items",
}
CYCLE_RELS = ("part_of", "depends_on")


class Refuse(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def actor_for(store, channel: str) -> dict:
    if channel in ("cli", "page"):
        return {"id": store.meta["owner"], "kind": "human"}
    if channel.startswith("connector:") and DEVICE.match(channel[10:]):
        return {"id": channel[10:], "kind": "connector"}
    if channel == "agent":
        return {"kind": "agent"}
    raise ValueError(f"unknown channel {channel!r}: use 'cli', 'page', 'connector:<name>' or 'agent'")


# ---------------------------------------------------------------- building envelopes (local channels)

def make(store, kind: str, target, patch: dict, reason: str | None = None) -> dict:
    """A complete envelope from this install's local device: op_id, clock, counter, base_seq and payload_hash."""
    with store.transaction():
        n = store.bump_meta("local_device_seq")
    device = store.meta["local_device"]
    clock = graph.Hlc(device)
    high = store.get_meta("hlc_high")
    if high:
        clock.seen(high, max_ahead_ms=None)
    env = {"v": 1, "op_id": str(uuid7()), "org": store.org, "space": store.space, "device": device,
           "device_seq": n, "hlc": clock.now(), "base_seq": store.head()[0], "kind": kind, "patch": patch,
           "payload_hash": payload_hash(kind, target, patch)}
    if target is not None:
        env["target"] = target
    if reason:
        env["reason"] = reason
    return env


# ---------------------------------------------------------------- apply

def apply(store, envelope, channel: str, *, payloads: dict | None = None) -> dict:
    """Judge and write one change. `envelope` is the JSON text or a dict; `payloads` maps a
    payload_ref to its content (it travels beside the envelope). Returns
    {"ok", "seq", "op_id", "reason"?, "duplicate"?}."""
    actor = actor_for(store, channel)
    raw = envelope if isinstance(envelope, (str, bytes)) else None
    try:
        env = graph.strict_loads(raw) if raw is not None else graph.strict_loads(canon(envelope))
        if not isinstance(env, dict):
            raise Malformed("an envelope is a JSON object")
        op_id = env.get("op_id")
        if not isinstance(op_id, str) or not is_uuid7(op_id):
            raise Malformed("op_id must be a UUIDv7")
    except Malformed as e:
        with store.transaction():
            chash = sha256_hex(raw if raw is not None else repr(envelope))
            seq = _refusal(store, None, None, None, actor, ("malformed: " + str(e))[:200], chash)
        return {"ok": False, "seq": seq, "op_id": None, "reason": "malformed: " + str(e)}

    chash = client_hash(env)
    with store.transaction() as conn:
        row = conn.execute("SELECT seq, kind, client_hash, text FROM ops WHERE op_id = ? OR refused_op_id = ?",
                           (op_id, op_id)).fetchone()
        if row is not None:
            if row["client_hash"] != chash:
                store.bump_meta("op_id_reused")
                return {"ok": False, "seq": None, "op_id": op_id, "reason": "op_id reused"}
            out = {"ok": row["kind"] != "refusal", "seq": row["seq"], "op_id": op_id, "duplicate": True}
            if row["kind"] == "refusal":
                out["reason"] = graph.strict_loads(row["text"])["patch"]["reason"]
            return out
        try:
            extra = _judge(store, conn, env, actor, payloads or {})
        except Refuse as r:
            seq = _refusal(store, op_id, env.get("kind"), env.get("target"), actor, r.reason, chash)
            return {"ok": False, "seq": seq, "op_id": op_id, "reason": r.reason}
        stored = dict(env, actor=actor, **extra.get("env", {}))
        seq, _ = store.append(stored, refused_op_id=None, chash=chash)
        stored = graph.strict_loads(conn.execute("SELECT text FROM ops WHERE seq = ?", (seq,)).fetchone()["text"])
        graph.effect(conn, stored)
        for ref, (salt, content) in extra.get("payloads", {}).items():
            conn.execute("INSERT INTO payloads(ref, salt, content, erased_seq) VALUES(?, ?, ?, NULL)", (ref, salt, content))
        erase = extra.get("erase")
        if erase:
            conn.execute("UPDATE payloads SET salt = NULL, content = NULL, erased_seq = ? WHERE ref = ?", (seq, erase))
        if env["device"] == store.meta["local_device"]:
            if env["device_seq"] > int(store.get_meta("local_device_seq", "0")):
                store.set_meta("local_device_seq", env["device_seq"])
    if extra.get("erase"):
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # so the erased pages don't stay in the WAL
    return {"ok": True, "seq": seq, "op_id": op_id}


def _refusal(store, refused_op_id, kind, target, actor, reason, chash) -> int:
    patch = {"refused_op_id": refused_op_id, "refused_actor": actor, "reason": reason[:200]}
    if isinstance(kind, str) and len(kind) <= 40:
        patch["kind"] = kind
    if isinstance(target, str) and len(target) <= 64:
        patch["target"] = target
    elif isinstance(target, dict) and all(isinstance(target.get(k), str) and len(target[k]) <= 64
                                          for k in ("from", "rel", "to")):
        patch["target"] = {k: target[k] for k in ("from", "rel", "to")}
    env ={"v": 1, "op_id": str(uuid7()), "org": store.org, "space": store.space, "device": store.device,
           "device_seq": store.bump_meta("store_device_seq"), "hlc": store.clock.now(), "base_seq": store.head()[0],
           "kind": "refusal", "patch": patch, "payload_hash": payload_hash("refusal", None, patch),
           "actor": {"kind": "store"}}
    seq, _ = store.append(env, refused_op_id=refused_op_id, chash=chash)
    return seq


# ---------------------------------------------------------------- judging

def _judge(store, conn, env, actor, payloads) -> dict:
    for k in env:
        if k in STORE_FIELDS:
            raise Refuse(f"{k} is set by the store, not the client")
        if k not in CLIENT_FIELDS:
            raise Refuse(f"unknown key {k}")
    if env.get("v") != 1 or isinstance(env.get("v"), bool):
        raise Refuse("envelope version must be 1")
    if env.get("org") != store.org or env.get("space") != store.space:
        raise Refuse("org and space don't match this store")
    device = env.get("device")
    if not isinstance(device, str) or not DEVICE.match(device):
        raise Refuse("device must be 1 to 64 letters, digits, - or _")
    _int(env, "device_seq", 1)
    base = _int(env, "base_seq", 0)
    if base > store.head()[0]:
        raise Refuse(f"base_seq {base} is ahead of this store (it ends at {store.head()[0]})")
    stamp = env.get("hlc")
    try:
        _, _, stamp_device = parse_stamp(stamp if isinstance(stamp, str) else "")
    except ValueError as e:
        raise Refuse(str(e)) from None
    if stamp_device != device:
        raise Refuse("the hlc's device part isn't the envelope's device")
    try:
        store.clock.seen(stamp)
    except ValueError as e:
        raise Refuse(str(e).split(":")[0]) from None  # "clock ahead"
    if "preview_hash" in env:
        raise Refuse("preview_hash waits for B4 (approvals)")
    if "reason" in env and (not isinstance(env["reason"], str) or len(env["reason"]) > MAX_REASON):
        raise Refuse(f"reason must be text of at most {MAX_REASON} characters")
    if actor["kind"] == "agent":
        raise Refuse("agent writes wait for B4")
    kind = env.get("kind")
    if kind == "refusal":
        raise Refuse("refusals are written by the store only")
    if kind not in PATCH_KEYS:
        raise Refuse(f"unknown op kind {kind!r}")
    if kind.startswith("ledger."):
        raise Refuse("ledger entries wait for the ledger step (architecture section 5)")
    patch = env.get("patch")
    if not isinstance(patch, dict):
        raise Refuse("patch must be an object")
    for k in patch:
        if k not in PATCH_KEYS[kind]:
            raise Refuse(f"unknown key {k}")
    target = env.get("target")
    if env.get("payload_hash") != payload_hash(kind, target, patch):
        raise Refuse("payload_hash doesn't match kind, target and patch")
    judge = _JUDGES[kind]
    return judge(store, conn, env, target, patch, actor, payloads) or {}


def _int(env, key, low):
    v = env.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < low:
        raise Refuse(f"{key} must be a whole number of at least {low}")
    return v


def _live_item(conn, item_id, what="item"):
    if not isinstance(item_id, str):
        raise Refuse(f"the {what} id must be text")
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise Refuse(f"no such {what} {item_id}")
    if row["deleted"]:
        raise Refuse("deleted")
    return row


def _check_fields(fields, *, creating: bool):
    if not isinstance(fields, dict):
        raise Refuse("fields must be an object")
    for k, v in fields.items():
        if not FIELD_KEY.match(k):
            raise Refuse(f"field name {k!r}: use 1 to 64 letters, digits, ., - or _")
        if v is None and creating:
            raise Refuse(f"field {k}: null removes a key, so it has no meaning on create")
        if k == "platforms" and v is not None:
            reason = check_platforms(v)
            if reason:
                raise Refuse(reason)


def _check_title(title):
    if not isinstance(title, str) or len(title) > MAX_TITLE:
        raise Refuse(f"title must be text of at most {MAX_TITLE} characters")
    if "\x00" in title:
        raise Refuse("title holds a NUL character")


def _store_payload(ref, payloads) -> tuple[dict, dict]:
    if not isinstance(ref, str) or not is_uuid7(ref):
        raise Refuse("payload_ref must be a UUIDv7")
    content = payloads.get(ref)
    if not isinstance(content, str):
        raise Refuse(f"payload {ref}: its content didn't arrive beside the envelope")
    if len(content.encode("utf-8")) > MAX_PAYLOAD:
        raise Refuse(f"payload {ref}: larger than {MAX_PAYLOAD // 1024} KB")
    salt = os.urandom(16)
    return {"payload_salted_hash": graph.salted_hash(salt, content)}, {ref: (salt, content)}


def _j_create(store, conn, env, target, patch, actor, payloads):
    item_kind = patch.get("kind")
    if item_kind not in ITEM_KINDS:
        raise Refuse(f"unknown item kind {item_kind!r}")
    if item_kind == "ledger":
        raise Refuse("ledger items are made by ledger.add")
    if not isinstance(target, str):
        raise Refuse("target must be the new item's id")
    if conn.execute("SELECT 1 FROM items WHERE id = ?", (target,)).fetchone():
        raise Refuse(f"item {target} already exists")
    source = patch.get("source", "typed")
    if actor["kind"] == "connector":
        if source != actor["id"]:
            raise Refuse(f"a connector's items carry its own name as source ({actor['id']})")
        ext = patch.get("external_id")
        if not isinstance(ext, str) or not 1 <= len(ext) <= 200:
            raise Refuse("a connector item needs external_id (1 to 200 characters)")
        if target != str(item_uuid5(store.org, source, ext)):
            raise Refuse("a connector item's id must be its derived id (architecture section 8)")
        if conn.execute("SELECT 1 FROM items WHERE source = ? AND external_id = ?", (source, ext)).fetchone():
            raise Refuse(f"{source} item {ext} already exists")
    else:
        if source != "typed" or "external_id" in patch:
            raise Refuse("items typed here have source 'typed' and no external_id")
        if not is_uuid7(target):
            raise Refuse("a typed item's id must be a UUIDv7")
    _check_title(patch.get("title", ""))
    status = patch.get("status", "")
    if not isinstance(status, str) or len(status) > MAX_STATUS:
        raise Refuse(f"status must be text of at most {MAX_STATUS} characters")
    fields = patch.get("fields", {})
    _check_fields(fields, creating=True)
    if item_kind == "person" and (patch.get("title", "") != "" or fields):
        raise Refuse("a person's name and details go in the payload, not the title or fields")
    if item_kind == "account" and fields.get("currency") not in CURRENCIES:
        raise Refuse(f"an account needs fields.currency, one of {', '.join(CURRENCIES)}")
    if item_kind == "agent":
        sponsor = patch.get("sponsor")
        row = _live_item(conn, sponsor, "sponsor")
        if row["kind"] != "person":
            raise Refuse("an agent's sponsor must be a person")
    elif "sponsor" in patch:
        raise Refuse("only agents have a sponsor")
    out: dict = {}
    if "payload_ref" in patch:
        env_extra, stored = _store_payload(patch["payload_ref"], payloads)
        out = {"env": env_extra, "payloads": stored}
    return out


def _j_update(store, conn, env, target, patch, actor, payloads):
    row = _live_item(conn, target)
    if row["kind"] == "ledger":
        raise Refuse("ledger entries can't be edited")
    if row["kind"] == "person":
        raise Refuse("a person's name and details change through payload.set")
    if not patch:
        raise Refuse("nothing to change")
    if "title" in patch:
        _check_title(patch["title"])
    if "fields" in patch:
        _check_fields(patch["fields"], creating=False)
        if row["kind"] == "account" and "currency" in patch["fields"]:
            raise Refuse("an account's currency never changes")


def _j_status(store, conn, env, target, patch, actor, payloads):
    row = _live_item(conn, target)
    if row["kind"] == "ledger":
        raise Refuse("ledger entries have no status")
    frm, to = patch.get("from"), patch.get("to")
    if not isinstance(frm, str) or not isinstance(to, str) or len(to) > MAX_STATUS:
        raise Refuse(f"from and to must be text (to: at most {MAX_STATUS} characters)")
    if row["status"] != frm:
        raise Refuse(f"status is {row['status']!r}, not {frm!r}: someone changed it first")


def _j_delete(store, conn, env, target, patch, actor, payloads):
    if not isinstance(target, str):
        raise Refuse("target must be an item id")
    row = conn.execute("SELECT kind, deleted FROM items WHERE id = ?", (target,)).fetchone()
    if row is None:
        raise Refuse(f"no such item {target}")
    if row["kind"] == "ledger":
        raise Refuse("ledger entries can't be edited")
    if env["kind"] == "item.delete" and row["deleted"]:
        raise Refuse("already deleted")
    if env["kind"] == "item.restore" and not row["deleted"]:
        raise Refuse("not deleted")


def _link_target(target):
    if not isinstance(target, dict) or set(target) != {"from", "rel", "to"} or \
            not all(isinstance(target[k], str) for k in target):
        raise Refuse("a link's target is {from, rel, to}")
    return target["from"], target["rel"], target["to"]


def _j_link_add(store, conn, env, target, patch, actor, payloads):
    frm, rel, to = _link_target(target)
    if rel in LINK_REFUSED:
        raise Refuse(LINK_REFUSED[rel])
    if rel not in LINK_RULES:
        raise Refuse(f"unknown relation {rel!r}")
    if frm == to:
        raise Refuse("an item can't link to itself")
    a = _live_item(conn, frm, "item")
    to_org = patch.get("to_org")
    if to_org is not None:
        if store.space != "personal":
            raise Refuse("an org item may not link to another store")
        if not isinstance(to_org, str) or not is_uuid7(to_org) or to_org == store.org:
            raise Refuse("to_org must be another org's id")
        if not is_item_id(to):
            raise Refuse("the linked org item's id isn't an item id")
        b_kind = None
    else:
        b_kind = _live_item(conn, to, "item")["kind"]
    allowed_from, allowed_to = LINK_RULES[rel]
    if allowed_from is not None and a["kind"] not in allowed_from:
        raise Refuse(f"{rel} can't start at a {a['kind']}")
    if b_kind is not None:
        if allowed_to == "same" and b_kind != a["kind"]:
            raise Refuse(f"{rel} joins items of the same kind ({a['kind']} → {b_kind})")
        if isinstance(allowed_to, set) and b_kind not in allowed_to:
            raise Refuse(f"{rel} can't end at a {b_kind}")
    row = conn.execute("SELECT live, removed_by_kind FROM links WHERE from_id = ? AND rel = ? AND to_id = ?",
                       (frm, rel, to)).fetchone()
    if row is not None and row["live"]:
        raise Refuse("that link already exists")
    if row is not None and actor["kind"] == "connector" and row["removed_by_kind"] == "human":
        raise Refuse("a person removed that link; a connector can't bring it back")
    if rel in CYCLE_RELS:
        seen, todo = set(), [to]
        while todo:
            node = todo.pop()
            if node == frm:
                title = conn.execute("SELECT title FROM items WHERE id = ?", (to,)).fetchone()
                raise Refuse(f"would make a loop through {(title['title'] if title and title['title'] else to)}")
            if node in seen:
                continue
            seen.add(node)
            todo.extend(r["to_id"] for r in conn.execute(
                "SELECT to_id FROM links WHERE from_id = ? AND rel = ? AND live = 1", (node, rel)))


def _j_link_remove(store, conn, env, target, patch, actor, payloads):
    frm, rel, to = _link_target(target)
    row = conn.execute("SELECT live, added_by_kind, added_by_id FROM links WHERE from_id = ? AND rel = ? AND to_id = ?",
                       (frm, rel, to)).fetchone()
    if row is None or not row["live"]:
        raise Refuse("no such link")
    if rel in LINK_REFUSED:
        raise Refuse(LINK_REFUSED[rel])
    if actor["kind"] == "connector" and (row["added_by_kind"] != "connector" or row["added_by_id"] != actor["id"]):
        raise Refuse("a connector may remove only links it added")


def _j_sponsor(store, conn, env, target, patch, actor, payloads):
    row = _live_item(conn, target, "agent")
    if row["kind"] != "agent":
        raise Refuse("agent.sponsor changes an agent's sponsor")
    if row["sponsor"] != patch.get("from"):
        raise Refuse("the sponsor changed first; reload and try again")
    new = _live_item(conn, patch.get("to"), "sponsor")
    if new["kind"] != "person":
        raise Refuse("an agent's sponsor must be a person")


def _j_payload_set(store, conn, env, target, patch, actor, payloads):
    _live_item(conn, target)
    ref = patch.get("payload_ref")
    if conn.execute("SELECT 1 FROM payloads WHERE ref = ?", (ref,)).fetchone():
        raise Refuse("payload_ref is already used; make a new one")
    env_extra, stored = _store_payload(ref, payloads)
    return {"env": env_extra, "payloads": stored}


def _j_payload_erase(store, conn, env, target, patch, actor, payloads):
    if not isinstance(target, str) or conn.execute("SELECT 1 FROM items WHERE id = ?", (target,)).fetchone() is None:
        raise Refuse(f"no such item {target}")
    ref = patch.get("payload_ref")
    row = conn.execute("SELECT erased_seq FROM payloads WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise Refuse("no such payload")
    if row["erased_seq"] is not None:
        raise Refuse("already erased")
    return {"erase": ref}


_JUDGES = {
    "item.create": _j_create, "item.update": _j_update, "item.status": _j_status,
    "item.delete": _j_delete, "item.restore": _j_delete, "link.add": _j_link_add, "link.remove": _j_link_remove,
    "agent.sponsor": _j_sponsor, "payload.set": _j_payload_set, "payload.erase": _j_payload_erase,
}


# ---------------------------------------------------------------- platforms (section 6)

_SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh", "busybox", "cmd", "powershell", "pwsh",
           "wscript", "cscript", "mshta"}
_WRAPPERS = {"env", "sudo", "doas", "su", "pkexec", "runas", "nohup", "setsid", "timeout", "nice", "xargs", "start"}
_RUNNERS = {"npx", "bunx", "uvx", "pipx", "deno"}
_ALWAYS = {"awk", "gawk"}
_FLAGS = {  # program: flags that put code in the argv
    "py": ("-c",), "node": ("-e", "-p", "--eval", "--print"), "perl": ("-e", "-E"), "ruby": ("-e",),
    "php": ("-r",), "lua": ("-e",), "osascript": ("-e",),
    "konsole": ("-e",), "xterm": ("-e",), "alacritty": ("-e",), "gnome-terminal": ("-e", "-x", "--"),
    "git": ("-c", "--config-env", "--exec-path"), "ssh": ("-o", "-F"),
    "find": ("-exec", "-execdir", "-ok", "-okdir"), "rsync": ("-e", "--rsh"),
}
_WIN_SCRIPTS = (".bat", ".cmd", ".ps1", ".vbs", ".js", ".wsf", ".hta")


def _program(argv0: str) -> str:
    name = re.split(r"[/\\]", argv0)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _flag_hit(args, flag) -> bool:
    for a in args:
        if flag == "--":
            if a == "--":
                return True
        elif flag.startswith("--"):
            if a == flag or a.startswith(flag + "="):
                return True
        elif len(flag) == 2:
            if a.startswith("-") and not a.startswith("--") and flag[1] in a[1:]:
                return True
        elif a == flag:  # single-dash words such as find's -exec
            return True
    return False


def check_platforms(value) -> str | None:
    """None when `fields.platforms` passes; otherwise the reason it is refused."""
    if not isinstance(value, list) or not value:
        return "platforms must be a list of {os, argv} entries"
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"os", "argv"}:
            return "each platform entry is {os, argv}"
        if entry["os"] not in ("linux", "macos", "windows"):
            return "os must be linux, macos or windows"
        argv = entry["argv"]
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
            return "argv must be a list of 1 to 64 strings"
        for a in argv:
            if not isinstance(a, str) or not a or any(c in a for c in "\x00\n\r"):
                return "argv strings must be non-empty, without NUL or line breaks"
        prog, args = _program(argv[0]), argv[1:]
        if prog in _SHELLS:
            return f"{prog} runs a line of code; name the program itself"
        if prog in _WRAPPERS:
            return f"{prog} would hide what really runs; name the program itself"
        if prog in _RUNNERS or (prog in ("npm", "pnpm") and args[:1] in (["exec"], ["dlx"])):
            return f"{prog} fetches and runs code that isn't on the machine yet"
        if prog in _ALWAYS:
            return f"{prog} takes its program in the argv"
        if re.fullmatch(r"python(3(\.\d+)?)?", prog) and _flag_hit(args, "-c"):
            return "python with -c carries code in the argv"
        for flag in _FLAGS.get(prog, ()):
            if _flag_hit(args, flag):
                return f"{prog} with {flag} runs a command from the argv"
        if entry["os"] == "windows" and prog.endswith(_WIN_SCRIPTS):
            return f"{prog} is a script file; Windows re-parses its arguments"
    return None
