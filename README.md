<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/banner-dark.svg">
  <img alt="Lunete: see what you and your AI helper are doing" src="docs/img/banner-light.svg" width="720">
</picture>

**A live, plain-English map of your project and everything your AI coding helper does in it.**

[![ci](https://github.com/alexb-joat/lunete/actions/workflows/ci.yml/badge.svg)](https://github.com/alexb-joat/lunete/actions/workflows/ci.yml)
[![codeql](https://github.com/alexb-joat/lunete/actions/workflows/codeql.yml/badge.svg)](https://github.com/alexb-joat/lunete/actions/workflows/codeql.yml)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![dependencies](https://img.shields.io/badge/dependencies-0-5fd6ae)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

<sub><i>Lunete: in Chrétien de Troyes' <i>Yvain</i>, the damsel who gets the hero out of a trap and back to where he belongs. Called Hlidskjalv until 0.4.</i></sub>

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/live-dark.png">
  <img alt="The Live view: a map of the project's folders and files, stats for edits, commands, AI steps and spend, and an activity log with AI steps, edits and alerts." src="docs/img/live-light.png" width="880">
</picture>

## What you get

| View | |
|---|---|
| **Live** | Your project as a map that lights up as files change, and a log of every edit, command, save point and AI step |
| **Replay** | Every session is saved on your computer; scrub through it step by step |
| **Learn** | Four short games (approve or deny, save point rescue, treasure hunt, token budget), a command decoder and badges |
| **Study** | Log the time you give what you're learning and see this week against your target, with scores and course pieces; it stays on your computer |
| **Details** | Click anything for the whole story: why a model was picked, tokens, cost, what each part of a command does |
| **Alerts** | Things you didn't do yourself: risky commands an AI ran, startup files that changed, refused requests, spending caps |

Made for people who are new to coding and work with an AI helper in VS Code. Lunete only **watches**: it never changes your files. A real terminal inside the page is there if you want it (`--terminal`).

## Start in 2 minutes

You need Python 3.10 or newer, and nothing else.

```bash
git clone https://github.com/alexb-joat/lunete.git && cd lunete
python3 -m lunete watch ~/projects/my-app
```

On Windows: `py -3 -m lunete watch C:\Users\you\projects\my-app`. Your browser opens at `http://127.0.0.1:8788`, and a short tour shows you around; leave the terminal running while you work. **Ctrl+C** stops it and saves the session for Replay. Point it at one project folder, not your whole home folder.

## Privacy

- **Local only:** it listens on `127.0.0.1` and makes no outbound connection unless you set one up.
- **File contents never leave:** only names and line counts; secret-looking files aren't read and secrets in commands are masked.
- **Read-only:** it never runs anything from your project; Git runs with hooks off.

## Known limits

- **One folder per run,** and up to 5,000 files in it (build and library folders are skipped first; past that the page says how many were left out).
- **AI steps come from two places only:** [Trioditis](https://github.com/alexb-joat/trioditis)'s log and Claude Code's own transcripts. A helper that talks to a model provider directly shows its edits and commands, but not its AI steps or cost.
- **Study stays on one computer:** sessions are never synced, so a second computer starts empty.
- **Not on PyPI yet:** install from a clone, as above.
- **Windows without symlink rights:** settings and history stay in `~/.hlidskjalv` instead of moving to `~/.lunete`. Everything still works.

## More

[Guide](docs/guide.md) (Study, AI steps and cost, alerts, terminal, customizing, start at login) · [Pairing and sync](docs/pairing-and-sync.md) · [Security](docs/security.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · Report a vulnerability via [SECURITY.md](SECURITY.md)

[GNU AGPL-3.0-or-later](LICENSE). Copyright (C) 2026 Alex Bubnov.

Lunete can sync over a network, so the AGPL's network clause applies: a modified version offered to others remotely must ship its modified source under the same licence. The earlier Hlidskjalv releases (v0.3.0, v0.4.0) remain under their original MIT terms.

**Name:** "Lunete" is the project name, not part of the code licence. A fork must use a different name and must not imply endorsement by or affiliation with this project.

Not affiliated with VS Code, Cline, Anthropic, OpenAI or Ollama.
