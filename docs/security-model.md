# Security model

How Hlidskjalv is built to resist misuse, what it deliberately does not cover, and how to use it safely. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Threat model

Hlidskjalv runs on your computer as you, watches one project folder, and serves a web page on `127.0.0.1`. It reads
file names and line counts, your Git history, terminal commands you chose to record, Claude Code's and Trioditis's
logs (metadata only), and some system files to raise alerts. Optionally it opens a shell in the page. It has no
server of its own and no accounts. The design assumes:

- **Other websites can reach `127.0.0.1`.** Any page open in your browser can send requests to it. Defences: it
  binds `127.0.0.1` only; every request must carry a `Host` of this computer (`127.0.0.1`, `localhost` or
  `hlidskjalv.localhost` with the port), which stops DNS rebinding; changes need a paired browser's session key
  *and* an `Origin` of the page itself; the terminal's WebSocket checks the same. Refusals are logged as Alerts.
- **Injected script is the main browser risk**, since a paired browser keeps a session key in localStorage. The page's
  script is a served file and the CSP is `script-src 'self'` without `'unsafe-inline'`, so an injected inline
  script or handler does not run. Styles still allow inline for now (a known gap, tracked).
- **The terminal is a shell as you.** It is off unless started with `--terminal` or switched on in Settings, needs a
  paired browser, and on Linux starts with no-new-privileges so `sudo` cannot elevate unless you switch on
  "Root in the terminal" (which needs a password, is refused while passwordless sudo works, asks for a fresh
  two-factor code when one is set up, and raises an alert). Shells start through a small single-threaded stub, not
  `preexec_fn`. The flag travels on the command line, never the environment.
- **Pairing.** A browser pairs with a one-time link (needs your user account on this computer) or a password (salted
  hash, rate limited, optional authenticator-app code and recovery codes) or an optional GitHub/Google device-flow
  sign-in. The owner key and password are readable only by you (`0600`).
- **File contents never leave a module.** Only names and line counts become events. Files that look secret (`.env`,
  keys, `*secret*`, `*credential*`…) are not even read. Commands are masked for tokens, passwords and URLs with
  credentials before they are shown or saved. Git runs with hooks and fsmonitor switched off.
- **Outbound connections are opt-in** and listed in the README (Outbound connections). A default install makes none.
- **Dependencies.** The Python side uses the standard library only. The browser side vendors three files
  (xterm.js, its fit addon, qrcode-generator), copied unmodified from npm and checked against a committed checksum
  manifest by a unit test (`static/vendor/SHA256SUMS`).
- **Autostart.** Nothing starts at login unless you run `autostart enable` yourself; the Linux unit runs as you with
  `NoNewPrivileges`, `UMask=0077` and a memory cap.
- **Compromised CI.** Workflows are read-only, actions are pinned to commit SHAs, nothing persists credentials, and
  CodeQL and dependency review run on every pull request.

## Scope and limits

What Hlidskjalv does not try to do, so you can plan around it:

- **A hostile local user or program running as you.** It can read `~/.hlidskjalv` like you can. Hlidskjalv is not a
  sandbox and does not claim to be.
- **Inline styles** in the CSP (see above) and the page's `style=""` attributes.
- **Passkeys (WebAuthn).** Verifying them needs ES256 signature checks, which the standard library lacks. The choice
  is between vendoring a verifier (with a checksum, reviewed) and staying with the password, an authenticator-app code
  and provider sign-in. Not built: this is a recorded decision, not an oversight.
- **Multi-user machines.** One owner key per user account; no per-user isolation inside one running instance.
- **Detection is pattern-based.** The alert rules for risky commands, persistence changes and secret-looking names
  are a safety net and will miss things.

## Operating checklist

- Pair with the one-time link (`python3 -m hlidskjalv pair`) or set a password; add two-factor if others use this
  computer.
- Leave "Root in the terminal" off unless you need it.
- Keep `~/.hlidskjalv` private (the program sets `0700`); do not sync it with a service you do not trust, because
  it holds the owner key and session records.
- If a pairing link, owner key or recovery code was ever pasted anywhere, rotate it: `python3 -m hlidskjalv pair --rotate`.
