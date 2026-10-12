# Hliðskjálf

*Hlidskjalv*: Odin's high seat, from which he looks out over every realm and sees all that happens. Here it looks out over one project: every file, command, save point and AI step, and anything you didn't do yourself.


[![ci](https://github.com/alexb-joat/hlidskjalv/actions/workflows/ci.yml/badge.svg)](https://github.com/alexb-joat/hlidskjalv/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![dependencies](https://img.shields.io/badge/dependencies-0-5fd6ae)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**See what you and your AI coding helper are doing.** Hlidskjalv watches a project folder and draws it as a live map: folders are groups, files are tiles. Each edit, command, Git save point and AI step lights up the map, lands in the activity log and (optionally) earns XP. Hover over anything (a file, a command, even one flag like `-rf`) for a plain-English explanation.

Made for people who are new to coding and work with an AI helper in VS Code.

| View | What it does |
|---|---|
| **Live** | Live map of the project and an activity log of everything that happens, with "Careful" badges on risky commands |
| **Replay** | Every session is saved on your computer; scrub through it step by step |
| **Learn** | Five short levels (terminal, Python, Git, working with AI, Trioditis), a command decoder and badges |
| **Details** | Click anything (a log entry, a file tile, a folder, the model ribbon) for every detail: why a model was picked, tokens, cost, what each piece of a command does, a file's history. Plus where your tokens and money go, and how to spend less |
| **Terminal** | Optional: a real shell in the project folder, inside the page (off unless you start with `--terminal`) |

It only **watches**: it never changes your files, never runs anything from your project, and never sends anything anywhere. The one exception is the optional terminal, which you switch on yourself. No dependencies beyond Python itself.

## Start (2 minutes)

You need Python 3.10 or newer. Kubuntu has it already; on Windows install it from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH".

```bash
git clone https://github.com/alexb-joat/hlidskjalv.git
cd hlidskjalv
python3 -m hlidskjalv watch ~/projects/my-app      # Windows: py -3 -m hlidskjalv watch C:\Users\you\projects\my-app
```

Your browser opens at `http://127.0.0.1:8788`. Leave that terminal running while you work; press **Ctrl+C** to stop (the session is saved for Replay).

Point it at **one project folder**, not your whole home folder or drive (it refuses those).

## See your terminal commands (optional)

Add a small hook to your shell so commands typed in the project (by you *or* by your AI helper in VS Code's terminal) show up in the activity log:

Kubuntu (bash):

```bash
python3 -m hlidskjalv hook bash >> ~/.bashrc
```

macOS (zsh): `python3 -m hlidskjalv hook zsh >> ~/.zshrc`

Windows (PowerShell):

```powershell
if (!(Test-Path $PROFILE)) { New-Item -Force $PROFILE | Out-Null }
py -3 -m hlidskjalv hook powershell | Add-Content $PROFILE
```

Open a new terminal afterwards. Commands are written to `~/.hlidskjalv/commands.log` (readable only by you). **Start a command with a space** to keep it out. Secrets that slip into commands (`API_KEY=…`, tokens, passwords in URLs) are masked before they're shown or saved in sessions.

To remove the hook, delete the lines between `# --- Hlidskjalv` and `# --- end Hlidskjalv ---` in that file.

## See AI steps and cost (optional, with Trioditis)

If your AI helper talks to models through [Trioditis](https://github.com/alexb-joat/trioditis), Hlidskjalv shows which route and model handled each step, whether it ran locally (free, private) or in the cloud, the tokens used and, if you add prices, the cost:

```bash
python3 -m hlidskjalv watch ~/projects/my-app --trioditis-log ~/trioditis/logs/decisions.jsonl
```

For costs, copy `prices.example.json` to `~/.hlidskjalv/prices.json` and fill in each cloud model's price per million tokens from the provider's pricing page.

If you also use the [Claude Code](https://claude.com/claude-code) CLI in the same folder, its AI steps show up too — read-only, from its own session transcripts (`~/.claude/projects/...`), not from Trioditis. Model and token counts only, never prompt or response text. Since Claude Code runs on your subscription rather than per-token billing, those steps are labeled "subscription" instead of a dollar cost.

The **Details** tab explains each AI step in full: Trioditis's reasons for the route and model, models it skipped (busy, down, or over your spending cap), tokens in, cached and out, and cost. Its cost-efficiency panel totals spend per model and points at the cheapest next step, for example a route that keeps sending routine prompts to a paid model.

Details also shows, read-only:

- **Spending caps:** today's and this month's cloud spend against Trioditis's caps (from `~/.hlidskjalv/metrics.db` and `budget.json`), and cloud calls that had no price so the caps couldn't count them.
- **Claude Code usage:** per session in this project: turns, tokens in, how much of that was re-read earlier context, tokens out, the largest context of any one turn, and how many commands the AI ran. Sessions past a threshold you choose are flagged "long": starting fresh for a new task is the biggest saving on a plan.
- **Providers:** per cloud provider, spend against its own cap, the rate limits it last reported (requests and tokens left, when they refill) and, where Trioditis has an account adapter (OpenRouter; Anthropic and OpenAI organizations with an admin key), your balance or this month's bill as the provider sees it.
- **Claude plan:** an estimate of your Claude subscription use in the same windows as the official meter (a 5-hour window that starts with your first message, and 7 days), split by model family. It's counted from Claude Code's transcripts on this computer, across all projects. It is not the official percentage: Anthropic doesn't publish how the caps are counted, and use in the claude.ai apps or on other machines shares the limits but leaves no transcripts here. You can enter the weekly reset time from the official panel once (Settings → Features) to anchor the week.
- **Local models for this computer:** Trioditis's hardware report (`~/.hlidskjalv/hardware.json`): how much memory a model may use, which installed models fit, and per route a suggestion with its reasons and the `config.yaml` change.

### Connect VS Code to Trioditis (Cline)

[Cline](https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev) is a free, open-source AI coding helper for VS Code that asks before every edit and command.

1. Start Trioditis: `python -m trioditis serve` (in the Trioditis folder).
2. In VS Code: Extensions → search **Cline** → Install.
3. Open Cline's settings and choose:
   - **API Provider:** OpenAI Compatible
   - **Base URL:** `http://127.0.0.1:8787/v1`
   - **API Key:** your `TRIODITIS_API_KEY`, or any text if you didn't set one
   - **Model ID:** `auto`
4. Your Claude and OpenAI keys now live only in Trioditis's `.env`, not in VS Code. Trioditis sends easy steps to free local models and hard ones to the cloud.

Cline's requests carry a large instruction block, and small local models (3B–8B) often struggle to follow it for multi-step edits. Keep a strong cloud model first in Trioditis's `code` route, and let local models take the quick and writing routes.

## Make it yours

The first time you open it, a short setup asks how it should look; skip it and everything stays at sensible defaults. Change anything later in **Settings** (the gear icon):

- **Look:** light, dark or follow the system; seven palettes, or your own accent, surface and text colours (with a readability check); font, text size, density, corner rounding, animations.
- **Background:** plain, gradient, glow, or slow particles (they pause when the tab is hidden and respect your system's reduce-motion setting).
- **Layout:** the icon navigation at the top, bottom, left or right, with or without names; which views to show, in what order, and which one opens first.
- **Features and sources:** the in-page terminal, levels and XP, Trioditis's log, Claude Code.
- **Your settings:** export, import, reset, or run the setup again.

Settings are saved in `~/.hlidskjalv/settings.json` and checked on every change. Changing them needs a paired browser (see below).

## Start at login (optional)

```bash
python3 -m hlidskjalv autostart enable ~/projects/my-app   # watch this folder from now on
python3 -m hlidskjalv autostart status                     # what starts, how, since when
python3 -m hlidskjalv autostart disable                    # remove it again
python3 -m hlidskjalv pair --open                          # pair your browser once
```

**Autostart policy.** What it does, exactly:

- Nothing starts at login unless you ran `autostart enable` yourself. Only that command line installs or removes the login item; the page can show it (Settings → Autostart: the command, its safety settings, since when it runs, its recent log) but never changes it.
- Linux: a systemd *user* unit, `~/.config/systemd/user/hlidskjalv.service`. No root. It runs as you with: files only you can read (`UMask=0077`), no privilege gain for it or anything it starts (`NoNewPrivileges`, so `sudo` doesn't work in the in-page terminal), a 2 GB memory cap, and a restart after crashes but not after you stop it. macOS: a launchd agent in `~/Library/LaunchAgents`. Windows: a hidden Scheduled Task for your user, no admin rights.
- It uses your system Python (Hlidskjalv needs nothing else), listens on `127.0.0.1` only, and starts with `--no-open`: no browser pops up at login.
- The in-page terminal stays off at login unless you switched it on in Settings.
- Logs: `journalctl --user -u hlidskjalv -f` (Linux), `~/.hlidskjalv/hlidskjalv.log` (macOS).

## Alerts

The **Alerts** view (bell icon) collects things you didn't do yourself, most severe first, with an unread count on the icon:

| Source | What raises an alert |
|---|---|
| **Commands the AI ran** | A risky shell command an AI agent ran in this project on its own (from Claude Code's transcripts): `rm -rf`, `curl … \| sh`, `sudo`, `git push --force`, printing `.env`… The command shows up in the activity log either way, marked "run by AI". |
| **Refused access** | Requests Hlidskjalv turned away (another website, another host name, a wrong owner key, a used pairing link) and requests Trioditis refused for a wrong or missing API key (from its `logs/security.jsonl`). |
| **Persistence changes** | Something set itself up to run later: login items (systemd user units, launchd agents, `~/.config/autostart`, the Windows Startup folder and Run keys), `crontab`, shell startup files (`.bashrc`, `.zshrc`…), SSH `authorized_keys` (fingerprints only, never keys), `~/.gitconfig` hooks and helpers, and this project's Git hooks. Only changes after Hlidskjalv starts count; its own login item is marked "expected". |
| **Spend and limits** | A spending cap (global or per provider) 75% used or reached, or a provider rate limit used up, from the same data Details shows. Once per period. |
| **sudo and logins** | `sudo` use and failures, SSH logins and new login sessions, from the system journal (Linux, for members of the `adm` or `systemd-journal` group). |

Alerts are kept in `~/.hlidskjalv/alerts.jsonl` (readable only by you), so they survive restarts. Mark them seen one by one or all at once. Pick the view (grouped by severity, the default; timeline; compact), filter and search; in Settings → Alerts switch sources on or off, choose from which severity a pop-up appears, and turn on browser notifications, a sound, or your own colour per severity. Secrets in commands and file changes are masked before they're shown or saved.

## The panel in the terminal

```bash
python3 -m hlidskjalv status
```

prints the Claude plan windows, Trioditis spend against its caps (and per provider), the last-reported rate limits, account balances, unread alerts and whether both services run: the same numbers as Details, without a browser. Run it from the project folder for that project's windows.

## Pairing a browser

Watching needs nothing. Anything that **changes** something (settings, the terminal) needs the owner key, which a browser gets once:

- The link Hlidskjalv opens at start pairs that browser automatically.
- For another browser, or a Hlidskjalv started with `--no-open` or at login: `python3 -m hlidskjalv pair` prints a one-time link (add `--open` to open it).

Pairing links work **once** and expire after 10 minutes, so a copied link is worthless. The key itself lives in `~/.hlidskjalv/owner.key` (readable only by you) and survives restarts. `python3 -m hlidskjalv pair --rotate` replaces it and un-pairs every browser at once.

Changes must also come from Hlidskjalv's own page (Origin check) and be JSON, so other websites can't send them. Settings are saved in `~/.hlidskjalv/settings.json`.

## Terminal in the page (optional)

```bash
python3 -m hlidskjalv watch ~/projects/my-app --terminal
```

(or switch it on in Settings). The **Terminal** tab then runs your normal shell in the project folder, as you. Commands typed there land in the activity log like any other (with the shell hook installed). A shell in a web page can do anything you can, so it's off by default and locked down when on:

- Only Hlidskjalv's own page can connect. Other websites are refused by an Origin check, and other users or programs on the computer by the owner key (see Pairing).
- At most four shells at a time; each one is closed when its tab disconnects.

The terminal display is [xterm.js](https://xtermjs.org/) (MIT), shipped unmodified in `hlidskjalv/static/vendor/` with its npm checksums. Linux and macOS use a pty. Windows 10 (1809) and later use ConPTY with PowerShell; that part is **experimental**: it's tested on Windows CI, not yet on a Windows desktop.

## Privacy and security

- Listens on `127.0.0.1` only and rejects requests with any other `Host` header, so other devices and websites can't reach it.
- Read-only: no endpoint changes anything and it never executes project files. The opt-in terminal above is the only exception.
- File **contents** never leave your computer and are never shown; only names and line counts. Files that look secret (`.env`, `*.pem`, `id_rsa`, `*secret*`, `*credential*`…) aren't even read.
- Git is run with repository hooks and fsmonitor switched off, so a cloned repo can't make it run anything.
- Sessions live in `~/.hlidskjalv/sessions` (owner-only permissions); the last 50 are kept (`--keep` to change).
- The page loads fonts from Google Fonts; everything else is local. Offline it falls back to system fonts.

## Try it without installing

The page opens in a demo session when it isn't connected to a running Hlidskjalv, so you can explore Live, Replay, Learn and Details with example data (and try the customization; in the demo it's kept in your browser).

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

## License

MIT. Not affiliated with VS Code, Cline, Anthropic, OpenAI or Ollama.
