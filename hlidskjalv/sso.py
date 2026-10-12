"""Sign in with GitHub or Google as a way to pair a browser (OAuth 2.0 device flow, RFC 8628).

Not an account system: you link your GitHub or Google identity once from a paired browser, and
from then on any browser on this computer can pair by signing in there, with that provider's own
protections (its two-factor, its recovery) in front. The password and the one-time link stay.

What it needs: an OAuth application registered under your own name at the provider, whose public
client id goes in ~/.hlidskjalv/sso.json (Google also issues a "client secret" for installed apps,
which it treats as public; it is kept in the same owner-only file, never synced). What it costs:
each sign-in tells the provider that you use Hlidskjalv, and pairing this way needs the internet.

The flow: Hlidskjalv asks the provider for a short code, shows it with a link; you confirm it at
the provider; Hlidskjalv polls until the provider hands over a token, asks who you are, and
compares that with the linked identity. A match pairs the browser and counts as a second factor;
anything else is refused and raises an alert. Tokens are used once, for that question, and dropped.
"""

from __future__ import annotations

import json
import os
import threading
import time
from urllib import error, parse, request

from .auth import new_token, write_private

PROVIDERS = {
    "github": {"name": "GitHub", "device": "https://github.com/login/device/code", "token": "https://github.com/login/oauth/access_token",
               "identity": "https://api.github.com/user", "scope": "read:user", "secret": False,
               "parse": lambda d: {"id": str(d["id"]), "name": str(d["login"])}},
    "google": {"name": "Google", "device": "https://oauth2.googleapis.com/device/code", "token": "https://oauth2.googleapis.com/token",
               "identity": "https://www.googleapis.com/oauth2/v3/userinfo", "scope": "email", "secret": True,
               "parse": lambda d: {"id": str(d["sub"]), "name": str(d.get("email") or d["sub"])}},
}
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
FLOW_MAX_S = 900
POLL_MIN_S = 1.0
MAX_PENDING = 5


class SsoError(Exception):
    pass


def _url(provider: str, which: str) -> str:
    fake = os.environ.get("HLIDSKJALV_SSO_FAKE")  # tests only: a local stand-in for the provider
    return f"{fake}/{provider}/{which}" if fake else PROVIDERS[provider][which]


def _call(url: str, form: dict | None = None, token: str | None = None) -> dict:
    data = parse.urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json", "User-Agent": "Hlidskjalv"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with request.urlopen(request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET"), timeout=20) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))  # OAuth errors come as JSON with a 4xx status
        except ValueError:
            raise SsoError(f"the provider answered {e.code}") from e
    except (error.URLError, OSError, ValueError) as e:
        raise SsoError(f"couldn't reach the provider: {getattr(e, 'reason', e)}") from e


class Sso:
    def __init__(self, home, alerter=None):
        self.file = home / "sso.json"
        self.alerter = alerter
        self.lock = threading.Lock()
        self.flows: dict[str, dict] = {}
        try:
            self.cfg = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cfg = {}

    # ------------------------------------------------------------ configuration
    def _save(self) -> None:
        write_private(self.file, json.dumps(self.cfg))

    def _alert(self, severity, source, title, detail="", fp=None, expected=True):
        if self.alerter:
            self.alerter.raise_(severity, source, title, detail, fingerprint=fp, expected=expected)

    def configured(self, provider: str) -> bool:
        return bool(self.cfg.get(provider, {}).get("client_id"))

    def linked(self, provider: str) -> dict | None:
        c = self.cfg.get(provider, {})
        return {"id": c["id"], "name": c["name"]} if c.get("id") else None

    def available(self) -> dict:
        """What an unpaired page may know: which providers can pair a browser right now."""
        return {p: {"name": PROVIDERS[p]["name"], "available": self.configured(p) and self.linked(p) is not None} for p in PROVIDERS}

    def status(self) -> dict:
        return {p: {"name": PROVIDERS[p]["name"], "configured": self.configured(p), "needs_secret": PROVIDERS[p]["secret"],
                    "linked": (self.linked(p) or {}).get("name")} for p in PROVIDERS}

    def configure(self, provider: str, client_id: str, client_secret: str | None = None) -> None:
        if provider not in PROVIDERS:
            raise SsoError("unknown provider")
        client_id = (client_id or "").strip()
        if not client_id or len(client_id) > 200:
            raise SsoError("a client id is needed")
        if PROVIDERS[provider]["secret"] and not (client_secret or "").strip():
            raise SsoError(f"{PROVIDERS[provider]['name']} also issues a client secret for installed apps; paste it too")
        with self.lock:
            c = self.cfg.setdefault(provider, {})
            c["client_id"] = client_id
            if client_secret:
                c["client_secret"] = client_secret.strip()
            self._save()

    def unlink(self, provider: str) -> None:
        with self.lock:
            c = self.cfg.get(provider, {})
            was = c.pop("name", None); c.pop("id", None)
            self._save()
        if was:
            self._alert("medium", "security", f"{PROVIDERS[provider]['name']} account unlinked", f"'{was}' can no longer pair a browser by signing in.", f"sso-unlink-{provider}")

    # ------------------------------------------------------------ the device flow
    def start(self, provider: str, purpose: str) -> dict:
        if provider not in PROVIDERS or purpose not in ("link", "pair"):
            raise SsoError("unknown provider or purpose")
        if not self.configured(provider):
            raise SsoError(f"{PROVIDERS[provider]['name']} sign-in isn't set up here (Settings > Pairing)")
        if purpose == "pair" and not self.linked(provider):
            raise SsoError(f"no {PROVIDERS[provider]['name']} account is linked yet")
        with self.lock:
            now = time.time()
            self.flows = {k: f for k, f in self.flows.items() if now < f["expires_at"] + 60}
            if sum(1 for f in self.flows.values() if f["status"] == "pending") >= MAX_PENDING:
                raise SsoError("too many sign-ins in progress; wait a minute")
        d = _call(_url(provider, "device"), {"client_id": self.cfg[provider]["client_id"], "scope": PROVIDERS[provider]["scope"]})
        if "device_code" not in d:
            raise SsoError(d.get("error_description") or d.get("error") or "the provider refused to start a sign-in")
        flow = {"id": new_token(), "provider": provider, "purpose": purpose, "device_code": d["device_code"],
                "user_code": str(d.get("user_code", "")), "uri": str(d.get("verification_uri") or d.get("verification_url") or ""),
                "expires_at": time.time() + min(int(d.get("expires_in", 600)), FLOW_MAX_S),
                "interval": max(POLL_MIN_S, float(d.get("interval", 5))), "status": "pending", "identity": None, "why": None, "consumed": False}
        with self.lock:
            self.flows[flow["id"]] = flow
        threading.Thread(target=self._poll, args=(flow,), daemon=True, name="hlidskjalv-sso").start()
        return {"id": flow["id"], "user_code": flow["user_code"], "uri": flow["uri"], "expires_in": int(flow["expires_at"] - time.time()),
                "provider": PROVIDERS[provider]["name"]}

    def _poll(self, flow: dict) -> None:
        provider, cfg = flow["provider"], self.cfg[flow["provider"]]
        form = {"client_id": cfg["client_id"], "device_code": flow["device_code"], "grant_type": GRANT}
        if PROVIDERS[provider]["secret"]:
            form["client_secret"] = cfg.get("client_secret", "")
        while flow["status"] == "pending":
            time.sleep(flow["interval"])
            if time.time() > flow["expires_at"]:
                flow["status"], flow["why"] = "expired", "the code expired; start again"
                return
            try:
                d = _call(_url(provider, "token"), form)
            except SsoError as e:
                flow["status"], flow["why"] = "error", str(e)
                return
            if d.get("access_token"):
                try:
                    who = PROVIDERS[provider]["parse"](_call(_url(provider, "identity"), token=d["access_token"]))
                except (SsoError, KeyError, TypeError, ValueError) as e:
                    flow["status"], flow["why"] = "error", f"the provider didn't say who signed in ({e})"
                    return
                self._finish(flow, who)
                return
            err = d.get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                flow["interval"] += 5
                continue
            flow["status"] = "denied" if err == "access_denied" else "expired" if err == "expired_token" else "error"
            flow["why"] = {"denied": "you declined at the provider", "expired": "the code expired; start again"}.get(flow["status"]) \
                or d.get("error_description") or err or "the provider refused"
            return

    def _finish(self, flow: dict, who: dict) -> None:
        name, provider = PROVIDERS[flow["provider"]]["name"], flow["provider"]
        if flow["purpose"] == "link":
            with self.lock:
                self.cfg.setdefault(provider, {}).update({"id": who["id"], "name": who["name"]})
                self._save()
            flow["status"], flow["identity"] = "ok", who["name"]
            self._alert("medium", "security", f"{name} account linked: {who['name']}",
                        "Any browser on this computer can now pair by signing in there. Unlink it in Settings > Pairing.", f"sso-link-{provider}")
            return
        allowed = self.linked(provider)
        if allowed and allowed["id"] == who["id"]:
            flow["status"], flow["identity"] = "ok", who["name"]
        else:
            flow["status"], flow["why"] = "refused", f"that {name} account isn't the linked one"
            self._alert("medium", "refused_access", f"A sign-in with {name} as '{who['name']}' was refused",
                        "Only the linked account may pair a browser this way.", f"sso-refused-{provider}", expected=False)

    def poll(self, flow_id) -> dict | None:
        flow = self.flows.get(flow_id) if isinstance(flow_id, str) else None
        if not flow:
            return None
        return {"status": flow["status"], "why": flow["why"], "identity": flow["identity"], "purpose": flow["purpose"],
                "provider": PROVIDERS[flow["provider"]]["name"]}

    def consume(self, flow_id) -> dict | None:
        """A finished pairing flow, once: the caller issues the session."""
        with self.lock:
            flow = self.flows.get(flow_id) if isinstance(flow_id, str) else None
            if not flow or flow["status"] != "ok" or flow["purpose"] != "pair" or flow["consumed"]:
                return None
            flow["consumed"] = True
            return {"identity": flow["identity"], "provider": flow["provider"]}
