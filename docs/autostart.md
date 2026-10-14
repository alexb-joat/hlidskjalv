# Start at login

Optional. Nothing starts at login unless you run the command yourself.

[← Back to the README](../README.md)

---

```bash
python3 -m hlidskjalv autostart enable ~/projects/my-app   # watch this folder from now on
python3 -m hlidskjalv autostart status                     # what starts, how, since when
python3 -m hlidskjalv autostart disable                    # remove it again
python3 -m hlidskjalv password                             # then pair any browser by typing it (Settings > Pairing)
```

**Autostart policy.** What it does, exactly:

- Nothing starts at login unless you ran `autostart enable` yourself. Only that command line installs or removes the login item; the page can show it (Settings → Autostart: the command, its safety settings, since when it runs, its recent log) but never changes it.
- Linux: a systemd *user* unit, `~/.config/systemd/user/hlidskjalv.service`. No root. It runs as you with: files only you can read (`UMask=0077`), no privilege gain for it or anything it starts (`NoNewPrivileges`, so `sudo` doesn't work in the in-page terminal), a 2 GB memory cap, and a restart after crashes but not after you stop it. macOS: a launchd agent in `~/Library/LaunchAgents`. Windows: a hidden Scheduled Task for your user, no admin rights.
- It uses your system Python (Hlidskjalv needs nothing else), listens on `127.0.0.1` only, and starts with `--no-open`: no browser pops up at login.
- The in-page terminal stays off at login unless you switched it on in Settings.
- Logs: `journalctl --user -u hlidskjalv -f` (Linux), `~/.hlidskjalv/hlidskjalv.log` (macOS).
