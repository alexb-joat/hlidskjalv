# Changelog

## 0.5.0 (2026-11-23)

**Study: what you're learning, against a weekly target**
- A **Study** tab next to Learn. Log a session in a few seconds: the line, the time (`25m`, `1h30m`), the day, a note; a 0–10 score on lines with check dates, the piece it went to on lines with pieces, and an optional link. Enter saves.
- Each line shows this week against its target ("done for this week" once it's met), the last eight weeks, scores with the check dates marked, and each piece's hours against its estimate. Edit or delete a session (with Undo), change a target, download everything as CSV. No streaks and no points: a missed day costs nothing.
- From the command line: `lunete study add-line` (with `--dry-run`), `log`, `list` and `target`. See [Study](docs/guide.md#study) in the guide.
- Sessions stay on this computer, in `~/.lunete/graph/personal.db`, and are never synced.
- Every change, from the page or the command line, goes into a change log. `lunete graph check` replays it and checks it gives what the page shows; `lunete graph rebuild` rebuilds the view from the log.

**Hlidskjalv is now Lunete**
- The repository moved to [github.com/alexb-joat/lunete](https://github.com/alexb-joat/lunete); the old address redirects there, and the Help menu's links point at the new one.
- The program, the command and the Python package are called `lunete` (`python3 -m lunete watch <folder>`). The `hlidskjalv` command and `python3 -m hlidskjalv` still start Lunete for this release (the second with a one-line note), so login items installed by 0.4 keep running.
- Settings and history move once from `~/.hlidskjalv` to `~/.lunete`, and a link is left at the old path, so shell hooks you installed before keep recording. If no link can be made (Windows without symlink rights), nothing moves and the old folder is used.
- Environment variables are `LUNETE_HOME`, `LUNETE_PORT` and `LUNETE_TRIODITIS_LOG`; the `HLIDSKJALV_*` and `ARCADE_*` names still work for this release.
- Unchanged, so nothing has to be set up again: paired browsers, the sync copy (`hlidskjalv-sync.json`), and the login item Autostart installed (`hlidskjalv.service`, `io.github.hlidskjalv.watch`, the `Hlidskjalv` scheduled task).

**Learn is games now**
- Four games in place of the five text levels: Approve or deny (6 levels), Save point rescue (8), Treasure hunt (6) and Token budget (5). Each cleared level is worth 25 XP once; levels finished before still count. Four new badges.
- Hovering over or clicking Learn opens its sub-tabs (keyboard: ↓ on the Learn tab); each has its own address, such as `#learn/hunt`.

**Lighter**
- The README fits on one screen, and the documentation is three pages: [Guide](docs/guide.md), [Pairing and sync](docs/pairing-and-sync.md) and [Security](docs/security.md).
- A starting tour: a spotlight on each part of the page with a short note, six steps, once per browser, after the first-start setup. Esc or Skip ends it; Help starts it again.
- **Help** next to Settings: the tour, keyboard shortcuts, what each view does, the command decoder, the guide and where to report a problem.
- Settings has five sections instead of ten: Appearance, Watching, Pairing, Sync and This computer. Same controls, regrouped; the old section names still open the right one.

**Fixed**
- Two saves of the same file at the same moment (for example two password changes) could make one of them fail. Each save now has its own temporary file, and saves take turns.

## 0.4.0 (2026-10-30)

**Trioditis 0.4 on the page**
- Each AI step names the program that sent it, and a step no model answered says why: **over budget** (stop) or **rate-limited · retry in N s** (wait). No cost chip, "budget → local" chip or fallback count where they don't apply, and no model called "none" in the cost table.
- Details → **By tool**: calls and cloud spend today and this month per program.
- Long-context price tiers, priced the way Trioditis prices them.
- The ribbon shows the latest AI step even when it was logged before the page opened.

**Security**
- Sign-in with GitHub or Google calls https addresses only.
- A large synced gist is fetched only from `gist.githubusercontent.com`, never from whatever address a reply names.

**Engineering**
- Browser tests in CI: a live Hlidskjalv driven in Firefox and WebKit (`tests/e2e`), failing on any console error, CSP violation or request that leaves the dashboard's origin.
- ruff in CI (pyflakes, bugbear, import order, pyupgrade, bandit security rules). The linter and Playwright are hash-pinned development tools; Hlidskjalv itself still needs only the standard library.

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
