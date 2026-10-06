# Workflow Arcade

[![ci](https://github.com/alexb-joat/workflow-arcade/actions/workflows/ci.yml/badge.svg)](https://github.com/alexb-joat/workflow-arcade/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![dependencies](https://img.shields.io/badge/dependencies-0-5fd6ae)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**See what you and your AI coding helper are doing, as a game.** Workflow Arcade watches a project folder and draws it as a map: folders are islands, files are tiles. Each edit, command, Git save point and AI step lights up the map, lands in a quest log and earns XP. Hover over anything (a file, a command, even one flag like `-rf`) for a plain-English explanation.

Made for people who are new to coding and work with an AI helper in VS Code.

| View | What it does |
|---|---|
| **World** | Live map of the project and a quest log of everything that happens, with "Careful" badges on risky commands |
| **Replay** | Every session is saved on your computer; scrub through it like a game replay |
| **Academy** | Five short levels (terminal, Python, Git, working with AI, Switchboard), a command decoder and badges |

It only **watches**: it never changes your files, never runs anything from your project, and never sends anything anywhere. No dependencies beyond Python itself.

## Start (2 minutes)

You need Python 3.10 or newer. Kubuntu has it already; on Windows install it from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH".

```bash
git clone https://github.com/alexb-joat/workflow-arcade.git
cd workflow-arcade
python3 -m workflow_arcade watch ~/projects/my-app      # Windows: py -3 -m workflow_arcade watch C:\Users\you\projects\my-app
```

Your browser opens at `http://127.0.0.1:8788`. Leave that terminal running while you work; press **Ctrl+C** to stop (the session is saved for Replay).

Point it at **one project folder**, not your whole home folder or drive (it refuses those).

## See your terminal commands (optional)

Add a small hook to your shell so commands typed in the project (by you *or* by your AI helper in VS Code's terminal) show up in the quest log:

Kubuntu (bash):

```bash
python3 -m workflow_arcade hook bash >> ~/.bashrc
```

macOS (zsh): `python3 -m workflow_arcade hook zsh >> ~/.zshrc`

Windows (PowerShell):

```powershell
if (!(Test-Path $PROFILE)) { New-Item -Force $PROFILE | Out-Null }
py -3 -m workflow_arcade hook powershell | Add-Content $PROFILE
```

Open a new terminal afterwards. Commands are written to `~/.arcade/commands.log` (readable only by you). **Start a command with a space** to keep it out. Secrets that slip into commands (`API_KEY=…`, tokens, passwords in URLs) are masked before they're shown or saved in sessions.

To remove the hook, delete the lines between `# --- Workflow Arcade` and `# --- end Workflow Arcade ---` in that file.

## See AI steps and cost (optional, with Switchboard)

If your AI helper talks to models through [Switchboard](https://github.com/alexb-joat/switchboard), the Arcade shows which route and model handled each step, whether it ran locally (free, private) or in the cloud, the tokens used and, if you add prices, the cost:

```bash
python3 -m workflow_arcade watch ~/projects/my-app --switchboard-log ~/switchboard/logs/decisions.jsonl
```

For costs, copy `prices.example.json` to `~/.arcade/prices.json` and fill in each cloud model's price per million tokens from the provider's pricing page.

### Connect VS Code to Switchboard (Cline)

[Cline](https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev) is a free, open-source AI coding helper for VS Code that asks before every edit and command.

1. Start Switchboard: `python -m switchboard serve` (in the Switchboard folder).
2. In VS Code: Extensions → search **Cline** → Install.
3. Open Cline's settings and choose:
   - **API Provider:** OpenAI Compatible
   - **Base URL:** `http://127.0.0.1:8787/v1`
   - **API Key:** your `SWITCHBOARD_API_KEY`, or any text if you didn't set one
   - **Model ID:** `auto`
4. Your Claude and OpenAI keys now live only in Switchboard's `.env`, not in VS Code. Switchboard sends easy steps to free local models and hard ones to the cloud.

Cline's requests carry a large instruction block, and small local models (3B–8B) often struggle to follow it for multi-step edits. Keep a strong cloud model first in Switchboard's `code` route, and let local models take the quick and writing routes.

## Privacy and security

- Listens on `127.0.0.1` only and rejects requests with any other `Host` header, so other devices and websites can't reach it.
- Read-only: no endpoint changes anything; it never executes project files.
- File **contents** never leave your computer and are never shown; only names and line counts. Files that look secret (`.env`, `*.pem`, `id_rsa`, `*secret*`, `*credential*`…) aren't even read.
- Git is run with repository hooks and fsmonitor switched off, so a cloned repo can't make it run anything.
- Sessions live in `~/.arcade/sessions` (owner-only permissions); the last 50 are kept (`--keep` to change).
- The page loads fonts from Google Fonts; everything else is local. Offline it falls back to system fonts.

## Try it without installing

The page opens in a demo session when it isn't connected to a running Arcade, so you can explore World, Replay and Academy with example data.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

## License

MIT. Not affiliated with VS Code, Cline, Anthropic, OpenAI or Ollama.
