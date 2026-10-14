# Changelog

## 0.3.0 (2026-10-14)

First public release. Earlier versions were private and named Workflow Arcade.

**Security**
- The page's script lives in its own file and runs under `script-src 'self'`; an injected inline script is blocked.
- No outbound request on a default start. Web fonts load only after you pick one in Settings.
- Terminal shells start through a single-threaded exec stub, not `preexec_fn` in a threaded server.
- Pairing with a password, sessions that end after idle time, optional two-factor authentication, optional sign-in with GitHub or Google (device flow).
- Root in the terminal only as an explicit opt-in.
- Hardened program lookups, request timeouts and query parsing; what CodeQL flagged is fixed or dismissed with a written reason.
- Checksums for the vendored libraries, checked by a test.

**Dashboard**
- Live map, Replay, Learn, Details, Terminal and Alerts views; Claude Code steps alongside your files, commands and Git history.
- Alerts for things you didn't do yourself (AI commands, refused access, persistence, system log entries, cost).
- Provider spend, balances, rate limits, spending caps from Trioditis and a Claude plan estimate.
- Encrypted sync of settings, XP and Learn progress between computers, with no server of its own.
- Customization: palettes, colour wheel, brightness, fourteen fonts, layout; first-start setup.
- Autostart at login on Linux, macOS and Windows; an experimental Windows terminal (ConPTY).

**Reliability**
- Event ring buffer with monotonic ids, a gap flag and a boot stamp; the page reloads its state when it falls behind and waits for a restarting server.
- One poll thread per source, with health on `/api/state`; stalled sources show on the page.
- File watcher with bounded memory, exact-or-net diffs, a stable cap and `skip_dirs`.
- Activity and XP kept across restarts.

**Other**
- Renamed from Workflow Arcade.
- CI on Linux, macOS and Windows (two Windows generations), Python 3.10 and 3.13; CodeQL and dependency review. Intermittent tests fixed at their cause.
- Short README with screenshots; reference moved to `docs/`.

## Before 0.3.0 (private)

- 0.1 (2026-10-06): game-like live map of your coding workflow.
