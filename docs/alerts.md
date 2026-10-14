# Alerts

Things you did not do yourself, most severe first.

[← Back to the README](../README.md)

---

The **Alerts** view (bell icon) collects things you didn't do yourself, most severe first, with an unread count on the icon:

| Source | What raises an alert |
|---|---|
| **Commands the AI ran** | A risky shell command an AI agent ran in this project on its own (from Claude Code's transcripts): `rm -rf`, `curl … \| sh`, `sudo`, `git push --force`, printing `.env`… The command shows up in the activity log either way, marked "run by AI". |
| **Security** | Changes to what can run as root or how a browser pairs: root allowed or forbidden in the terminal, two-factor switched on or off, a root-capable shell opened. Always on. |
| **Sync** | Settings that arrived from another computer, sync set up or switched off, a sync that stopped working. Always on. |
| **Refused access** | Requests Hlidskjalv turned away (another website, another host name, a wrong password or second-factor code, an ended session, a used pairing link) and requests Trioditis refused for a wrong or missing API key (from its `logs/security.jsonl`). |
| **Persistence changes** | Something set itself up to run later: login items (systemd user units, launchd agents, `~/.config/autostart`, the Windows Startup folder and Run keys), `crontab`, shell startup files (`.bashrc`, `.zshrc`…), SSH `authorized_keys` (fingerprints only, never keys), `~/.gitconfig` hooks and helpers, and this project's Git hooks. Only changes after Hlidskjalv starts count; its own login item is marked "expected". |
| **Spend and limits** | A spending cap (global or per provider) 75% used or reached, or a provider rate limit used up, from the same data Details shows. Once per period. |
| **sudo and logins** | `sudo` use and failures, SSH logins and new login sessions, from the system journal (Linux, for members of the `adm` or `systemd-journal` group). |

Alerts are kept in `~/.hlidskjalv/alerts.jsonl` (readable only by you), so they survive restarts. Mark them seen one by one or all at once. Pick the view (grouped by severity, the default; timeline; compact), filter and search; in Settings → Alerts switch sources on or off, choose from which severity a pop-up appears, and turn on browser notifications, a sound, or your own colour per severity. Secrets in commands and file changes are masked before they're shown or saved.
