"""Settings, experience and learning progress on more than one computer, through a place you own.

No account server: the copy lives in a folder that Dropbox, Drive, Nextcloud or Syncthing already
carry, or in a private GitHub Gist. It is encrypted on this computer with a passphrase before it
leaves (ChaCha20-Poly1305, RFC 8439, key from scrypt), so the carrier only ever sees ciphertext.
What travels: settings (minus machine-specific ones), lifetime XP per computer, finished Learn
levels. What never travels: pairing, passwords, second-factor secrets, sessions, alerts, sessions'
activity, tokens. The sync configuration itself (where, the token, the derived key) stays in
~/.hlidskjalv/sync.json, readable only by you, and is never part of what is synced.

Merge rule: settings follow the newest copy (last writer wins, and an alert says so); XP adds up
across computers; finished levels are the union.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import os
import socket
import struct
import threading
import time
from pathlib import Path
from urllib import error, request

from .auth import write_private

# ---------------------------------------------------------------- ChaCha20-Poly1305 (RFC 8439)

_M32 = 0xFFFFFFFF


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & _M32


def _quarter(s: list[int], a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & _M32; s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _M32; s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & _M32; s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _M32; s[b] = _rotl(s[b] ^ s[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8L", key), counter & _M32, *struct.unpack("<3L", nonce)]
    w = list(state)
    for _ in range(10):
        _quarter(w, 0, 4, 8, 12); _quarter(w, 1, 5, 9, 13); _quarter(w, 2, 6, 10, 14); _quarter(w, 3, 7, 11, 15)
        _quarter(w, 0, 5, 10, 15); _quarter(w, 1, 6, 11, 12); _quarter(w, 2, 7, 8, 13); _quarter(w, 3, 4, 9, 14)
    return struct.pack("<16L", *((w[i] + state[i]) & _M32 for i in range(16)))


def chacha20_xor(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), 64):
        block = chacha20_block(key, counter + i // 64, nonce)
        chunk = data[i:i + 64]
        out[i:i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, block))
    return bytes(out)


def poly1305_mac(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        chunk = msg[i:i + 16]
        acc = ((acc + int.from_bytes(chunk + b"\x01", "little")) * r) % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(b: bytes) -> bytes:
    return b"\x00" * (-len(b) % 16)


def aead_encrypt(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
    """ciphertext || 16-byte tag"""
    otk = chacha20_block(key, 0, nonce)[:32]
    ct = chacha20_xor(key, 1, nonce, plaintext)
    mac_data = aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct))
    return ct + poly1305_mac(otk, mac_data)


def aead_decrypt(key: bytes, nonce: bytes, aad: bytes, data: bytes) -> bytes | None:
    """The plaintext, or None when the tag doesn't match (wrong key, or tampered)."""
    if len(data) < 16:
        return None
    ct, tag = data[:-16], data[-16:]
    otk = chacha20_block(key, 0, nonce)[:32]
    mac_data = aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct))
    import hmac
    if not hmac.compare_digest(poly1305_mac(otk, mac_data), tag):
        return None
    return chacha20_xor(key, 1, nonce, ct)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)  # like the password hash: ~16 MB, tens of ms


# ---------------------------------------------------------------- the blob

BLOB_NAME = "hlidskjalv-sync.json"
FORMAT = 1


def seal(key: bytes, salt: bytes, device: str, payload: dict) -> str:
    nonce = os.urandom(12)
    aad = {"v": FORMAT, "device": device[:60], "updated": round(time.time(), 3)}
    aad_bytes = json.dumps(aad, sort_keys=True, separators=(",", ":")).encode()
    ct = aead_encrypt(key, nonce, aad_bytes, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    return json.dumps({"v": FORMAT, "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                       "aad": aad, "ct": base64.b64encode(ct).decode()}, indent=1)


def peek(text: str) -> dict | None:
    """The blob's unencrypted header (format, salt, device, updated), or None if it isn't one."""
    try:
        d = json.loads(text)
        if d.get("v") != FORMAT or not isinstance(d.get("aad"), dict):
            return None
        return {"salt": base64.b64decode(d["salt"]), "device": str(d["aad"].get("device", "?")),
                "updated": float(d["aad"].get("updated", 0))}
    except (ValueError, TypeError, KeyError):
        return None


def open_blob(key: bytes, text: str) -> dict | None:
    try:
        d = json.loads(text)
        aad_bytes = json.dumps(d["aad"], sort_keys=True, separators=(",", ":")).encode()
        pt = aead_decrypt(key, base64.b64decode(d["nonce"]), aad_bytes, base64.b64decode(d["ct"]))
        return json.loads(pt) if pt is not None else None
    except (ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------- what travels, and how it merges

MACHINE_ONLY = (("sources", "trioditis_log"), ("features", "terminal_root"), ("setup_done",))  # never synced
PULL_EVERY_S = 300
PUSH_AFTER_S = 10  # quiet time after a local change before it is pushed
TICK_S = 5


class SyncError(Exception):
    pass


def _strip(settings: dict) -> dict:
    out = json.loads(json.dumps(settings))
    out.pop("version", None)
    for path in MACHINE_ONLY:
        d = out
        for k in path[:-1]:
            d = d.get(k, {}) if isinstance(d, dict) else {}
        if isinstance(d, dict):
            d.pop(path[-1], None)
    return out


def _merge_settings(local: dict, remote: dict) -> dict:
    """The remote copy wins (it is newer), except for the machine-only values, which stay local."""
    out = json.loads(json.dumps(remote))
    for path in MACHINE_ONLY:
        src, dst = local, out
        for k in path[:-1]:
            src = src.get(k, {}) if isinstance(src, dict) else {}
            dst = dst.setdefault(k, {}) if isinstance(dst, dict) else {}
        if isinstance(src, dict) and path[-1] in src and isinstance(dst, dict):
            dst[path[-1]] = src[path[-1]]
    out.pop("version", None)
    return out


# ---------------------------------------------------------------- carriers

class FolderBackend:
    kind = "folder"

    def __init__(self, path: str):
        self.dir = Path(path).expanduser()
        self.file = self.dir / BLOB_NAME

    def where(self) -> str:
        return str(self.file)

    def read(self) -> str | None:
        try:
            return self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise SyncError(f"can't read {self.file}: {e.strerror or e}") from e

    def write(self, text: str) -> None:
        try:
            if not self.dir.is_dir():
                raise SyncError(f"{self.dir} is not a folder")
            tmp = self.file.with_name(self.file.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError as e:
            raise SyncError(f"can't write {self.file}: {e.strerror or e}") from e


class GistBackend:
    kind = "gist"
    API = "https://api.github.com"

    def __init__(self, token: str, gist_id: str | None = None):
        # The id becomes part of an API path carrying your token: only what a gist id looks like is accepted.
        if gist_id is not None and not re.fullmatch(r"[0-9a-fA-F]{8,64}", gist_id):
            raise SyncError("that doesn't look like a gist id (letters a-f and digits, as in the end of the gist's address)")
        self.token, self.gist_id = token, gist_id

    def where(self) -> str:
        return f"private GitHub Gist {self.gist_id}" if self.gist_id else "a new private GitHub Gist"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(self.API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "User-Agent": "Hlidskjalv", "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except error.HTTPError as e:
            why = {401: "GitHub refused the token", 403: "GitHub refused (rate limit or missing gist scope)",
                   404: "that gist doesn't exist, or the token can't see it"}.get(e.code, f"GitHub answered {e.code}")
            raise SyncError(why) from e
        except (error.URLError, OSError, ValueError) as e:
            raise SyncError(f"couldn't reach GitHub: {getattr(e, 'reason', e)}") from e

    def read(self) -> str | None:
        if not self.gist_id:
            return None
        g = self._call("GET", f"/gists/{self.gist_id}")
        f = (g.get("files") or {}).get(BLOB_NAME)
        if not f:
            return None
        if f.get("truncated") and f.get("raw_url"):
            try:
                with request.urlopen(request.Request(f["raw_url"], headers={"User-Agent": "Hlidskjalv"}), timeout=20) as r:
                    return r.read().decode("utf-8")
            except (error.URLError, OSError) as e:
                raise SyncError(f"couldn't fetch the gist content: {e}") from e
        return f.get("content")

    def write(self, text: str) -> None:
        files = {BLOB_NAME: {"content": text}}
        if self.gist_id:
            self._call("PATCH", f"/gists/{self.gist_id}", {"files": files})
        else:
            g = self._call("POST", "/gists", {"description": "Hlidskjalv sync (encrypted)", "public": False, "files": files})
            self.gist_id = g.get("id")
            if not self.gist_id:
                raise SyncError("GitHub created no gist")


# ---------------------------------------------------------------- the syncer

class Syncer:
    """Holds the configuration (sync.json), pushes and pulls, merges. `progress` is the lifetime-XP
    ledger (xp, events, since, academy, remote_xp, save()); `settings` the Settings store."""

    def __init__(self, home: Path, settings, progress, alerter=None, device: str | None = None):
        self.home, self.settings, self.progress, self.alerter = home, settings, progress, alerter
        self.file = home / "sync.json"
        self.lock = threading.RLock()
        self.cfg: dict = {}
        self.backend = None
        self._pushed_fp = None
        self._changed_at = 0.0
        try:
            self.cfg = json.loads(self.file.read_text(encoding="utf-8"))
            self.backend = self._backend(self.cfg)
        except (OSError, ValueError, KeyError, SyncError):
            self.cfg, self.backend = {}, None
        self.default_device = device or socket.gethostname().split(".")[0][:60] or "this computer"

    # ----- configuration
    @staticmethod
    def _backend(cfg: dict):
        if cfg.get("kind") == "folder":
            return FolderBackend(cfg["path"])
        if cfg.get("kind") == "gist":
            return GistBackend(cfg["token"], cfg.get("gist_id"))
        raise SyncError("no sync configured")

    @property
    def configured(self) -> bool:
        return self.backend is not None

    def _save(self) -> None:
        write_private(self.file, json.dumps(self.cfg))

    def _alert(self, severity: str, title: str, detail: str = "", fp: str | None = None, expected: bool = True, data=None) -> None:
        if self.alerter:
            self.alerter.raise_(severity, "sync", title, detail, fingerprint=fp, expected=expected, data=data)

    def status(self) -> dict:
        c = self.cfg
        return {"configured": self.configured, "kind": c.get("kind"), "where": self.backend.where() if self.backend else None,
                "device": c.get("device"), "default_device": self.default_device, "last_push": c.get("last_push"),
                "last_pull": c.get("last_pull"), "last_error": c.get("last_error"),
                "devices": {k: {"xp": v.get("xp", 0), "updated": v.get("updated")} for k, v in (c.get("devices") or {}).items()},
                "remote_xp": self.progress.remote_xp}

    def setup(self, kind: str, passphrase: str, device: str | None = None, path: str | None = None,
              token: str | None = None, gist_id: str | None = None) -> dict:
        """Connect: join an existing copy (its passphrase must match) or start one."""
        if not isinstance(passphrase, str) or len(passphrase) < 8:
            raise SyncError("the passphrase needs at least 8 characters")
        device = (device or self.default_device).strip()[:60] or self.default_device
        if kind == "folder":
            if not path:
                raise SyncError("which folder?")
            backend = FolderBackend(path)
            if not backend.dir.is_dir():
                raise SyncError(f"{backend.dir} is not a folder")
        elif kind == "gist":
            if not token:
                raise SyncError("a GitHub token with the gist scope is needed")
            backend = GistBackend(token.strip(), (gist_id or "").strip() or None)
        else:
            raise SyncError("kind must be folder or gist")
        with self.lock:
            existing = backend.read()
            header = peek(existing) if existing else None
            if existing and header is None:
                raise SyncError(f"{backend.where()} holds something that isn't a Hlidskjalv sync copy")
            salt = header["salt"] if header else os.urandom(16)
            key = derive_key(passphrase, salt)
            if header and open_blob(key, existing) is None:
                raise SyncError("wrong passphrase for the existing copy")
            self.cfg = {"kind": kind, "path": path if kind == "folder" else None, "gist_id": backend.gist_id if kind == "gist" else None,
                        "token": backend.token if kind == "gist" else None, "device": device,
                        "salt": base64.b64encode(salt).decode(), "key": base64.b64encode(key).decode(),
                        "seen_updated": 0, "devices": {}, "last_push": None, "last_pull": None, "last_error": None}
            self.backend = backend
            if existing:
                self._apply(open_blob(key, existing), header)
            self.push()
            self.cfg["gist_id"] = backend.gist_id if kind == "gist" else None
            self._save()
        self._alert("info", f"Sync set up: {backend.where()}", f"This computer is called '{device}'. Settings, XP and Learn progress travel encrypted; pairing and secrets never do.", "sync-setup")
        return self.status()

    def disconnect(self) -> None:
        with self.lock:
            self.cfg, self.backend = {}, None
            try:
                self.file.unlink()
            except OSError:
                pass
            self.progress.remote_xp = 0
            self.progress.save()
        self._alert("info", "Sync switched off", "The copy stays where it is; nothing is deleted.", "sync-off")

    # ----- payloads
    def _payload(self) -> dict:
        now = time.time()
        devices = {k: v for k, v in (self.cfg.get("devices") or {}).items() if k != self.cfg.get("device")}
        devices[self.cfg["device"]] = {"xp": self.progress.xp, "events": self.progress.events, "since": self.progress.since, "updated": now}
        return {"settings": _strip(self.settings.data), "devices": devices, "academy": dict(self.progress.academy)}

    def fingerprint(self) -> str:
        p = {"settings": _strip(self.settings.data), "xp": self.progress.xp, "events": self.progress.events, "academy": self.progress.academy}
        return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()

    def _apply(self, payload: dict, header: dict) -> None:
        """Merge a copy from elsewhere: XP adds up, Learn levels unite, settings follow the newest writer."""
        if not isinstance(payload, dict):
            raise SyncError("the copy is damaged")
        mine = self.cfg["device"]
        others = {k: v for k, v in (payload.get("devices") or {}).items() if k != mine and isinstance(v, dict)}
        self.cfg["devices"] = others
        self.progress.remote_xp = sum(int(v.get("xp", 0)) for v in others.values())
        for lesson, done in (payload.get("academy") or {}).items():
            if done:
                self.progress.academy[str(lesson)] = True
        self.progress.save()
        if header["device"] != mine and isinstance(payload.get("settings"), dict):
            try:
                _, restart = self.settings.update(_merge_settings(self.settings.data, payload["settings"]))
                self._alert("info", f"Settings arrived from '{header['device']}'",
                            "They apply now" + (", some after a restart" if restart else "") + ". XP from all your computers is added up.",
                            "sync-pull", data={"reload": True})
            except Exception as e:  # a copy from a newer version may hold settings this one doesn't know
                self._alert("medium", "Settings from another computer couldn't be applied", str(e)[:300], "sync-pull-bad", expected=False)
        self.cfg["seen_updated"] = header["updated"]
        self.cfg["last_pull"] = time.time()

    # ----- push / pull
    def pull(self, force: bool = False) -> bool:
        with self.lock:
            if not self.backend:
                return False
            text = self.backend.read()
            header = peek(text) if text else None
            if header is None:
                self.cfg["last_pull"] = time.time(); self._save()
                return False
            if not force and header["updated"] <= float(self.cfg.get("seen_updated") or 0):
                self.cfg["last_pull"] = time.time(); self._save()
                return False
            payload = open_blob(base64.b64decode(self.cfg["key"]), text)
            if payload is None:
                raise SyncError("the copy can't be decrypted with this passphrase any more")
            self._apply(payload, header)
            self._save()
            return True

    def push(self) -> None:
        with self.lock:
            if not self.backend:
                return
            text = seal(base64.b64decode(self.cfg["key"]), base64.b64decode(self.cfg["salt"]), self.cfg["device"], self._payload())
            self.backend.write(text)
            self.cfg["seen_updated"] = peek(text)["updated"]
            self.cfg["last_push"] = time.time()
            self.cfg["last_error"] = None
            self._pushed_fp = self.fingerprint()
            self._save()

    def sync_now(self) -> dict:
        self.pull()
        self.push()
        return self.status()

    def tick(self) -> None:
        """Called every few seconds by the worker: pull on schedule, push after local changes settle."""
        if not self.configured:
            return
        try:
            now = time.time()
            fp = self.fingerprint()
            if fp != self._pushed_fp and not self._changed_at:
                self._changed_at = now
            if now - float(self.cfg.get("last_pull") or 0) >= PULL_EVERY_S:
                self.pull()
            if self._changed_at and now - self._changed_at >= PUSH_AFTER_S:
                self._changed_at = 0.0
                if self.fingerprint() != self._pushed_fp:
                    self.push()
            if self.cfg.get("last_error"):
                self.cfg["last_error"] = None; self._save()
        except SyncError as e:
            if self.cfg.get("last_error") != str(e):
                self.cfg["last_error"] = str(e); self._save()
                self._alert("medium", "Sync isn't working", str(e), "sync-error", expected=False)
        except Exception as e:  # never let the worker die
            self.cfg["last_error"] = f"{type(e).__name__}: {e}"


def start_worker(syncer: Syncer) -> threading.Thread:
    def loop():
        while True:
            syncer.tick()
            time.sleep(TICK_S)
    t = threading.Thread(target=loop, daemon=True, name="hlidskjalv-sync")
    t.start()
    return t
