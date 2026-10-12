"""python -m hlidskjalv [watch|hook|sessions]"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .alerts import Acks, Alerter, PersistenceWatcher, TrioditisSecurityWatcher, SystemLogWatcher
from .cost_alerts import CostWatcher
from .server import NICE_HOST, AlertsContext, Hub, Progress, UsageContext, list_sessions, load_history, serve
from .usage import ClaudeUsage
from . import terminal as term
from .auth import Owner, issue_pair_code, rotate_key, write_private
from .settings import Settings
from .sources import ClaudeCodeWatcher, CommandWatcher, FileWatcher, GitWatcher, TrioditisWatcher, load_prices

HOOKS = {
    "bash": r'''
# --- Hlidskjalv: record terminal commands (local file, owner-only) ---
# Commands that start with a space are not recorded (Ubuntu's default HISTCONTROL).
__arcade_last=$(HISTTIMEFORMAT= builtin history 1 | awk '{print $1}')
__arcade_log() {
  local h n c f="${HLIDSKJALV_HOME:-${ARCADE_HOME:-$HOME/.hlidskjalv}}/commands.log"
  h=$(HISTTIMEFORMAT= builtin history 1) || return
  read -r n c <<<"$h"
  { [ -z "$c" ] || [ "$n" = "$__arcade_last" ]; } && return
  __arcade_last=$n
  [ -f "$f" ] || ( umask 077; mkdir -p "${f%/*}"; : >> "$f" )
  printf '%s\t%s\t%s\n' "$(date +%s)" "$PWD" "$c" >> "$f"
}
case ";$PROMPT_COMMAND;" in *";__arcade_log;"*) ;; *) PROMPT_COMMAND="__arcade_log${PROMPT_COMMAND:+;$PROMPT_COMMAND}";; esac
# --- end Hlidskjalv ---
''',
    "zsh": r'''
# --- Hlidskjalv: record terminal commands (local file, owner-only) ---
setopt HIST_IGNORE_SPACE   # commands starting with a space are not recorded
__arcade_last=$(fc -l -1 2>/dev/null | awk '{print $1}')
__arcade_log() {
  local h n c f="${HLIDSKJALV_HOME:-${ARCADE_HOME:-$HOME/.hlidskjalv}}/commands.log"
  h=$(fc -l -1 2>/dev/null) || return
  read -r n c <<<"$h"
  [[ -z "$c" || "$n" == "$__arcade_last" ]] && return
  __arcade_last=$n
  [[ -f "$f" ]] || ( umask 077; mkdir -p "${f:h}"; : >> "$f" )
  printf '%s\t%s\t%s\n' "$(date +%s)" "$PWD" "$c" >> "$f"
}
autoload -Uz add-zsh-hook && add-zsh-hook precmd __arcade_log
# --- end Hlidskjalv ---
''',
    "powershell": r'''
# --- Hlidskjalv: record terminal commands (local file in your profile folder) ---
# Commands that start with a space are not recorded.
$global:__HlidskjalvLastId = -1
$global:__HlidskjalvOriginalPrompt = $function:prompt
function global:prompt {
  $h = Get-History -Count 1
  if ($h -and $h.Id -ne $global:__HlidskjalvLastId -and -not $h.CommandLine.StartsWith(' ')) {
    $global:__HlidskjalvLastId = $h.Id
    $dir = if ($env:HLIDSKJALV_HOME) { $env:HLIDSKJALV_HOME } elseif ($env:ARCADE_HOME) { $env:ARCADE_HOME } else { Join-Path $HOME '.hlidskjalv' }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $ts = [DateTimeOffset]::Now.ToUnixTimeSeconds()
    $line = "$ts`t$($PWD.Path)`t$($h.CommandLine -replace "`r?`n", ' ')"
    Add-Content -Path (Join-Path $dir 'commands.log') -Value $line -Encoding utf8
  }
  & $global:__HlidskjalvOriginalPrompt
}
# --- end Hlidskjalv ---
''',
}


def data_home() -> Path:
    """$HLIDSKJALV_HOME (or the pre-rename $ARCADE_HOME), else ~/.hlidskjalv. An old ~/.arcade
    folder is moved there once, with a symlink left behind so installed shell hooks keep working."""
    env = os.environ.get("HLIDSKJALV_HOME") or os.environ.get("ARCADE_HOME")
    if env:
        home = Path(env)
    else:
        home, old = Path.home() / ".hlidskjalv", Path.home() / ".arcade"
        if not home.exists() and old.is_dir() and not old.is_symlink():
            try:
                os.rename(old, home)
                os.symlink(home, old, target_is_directory=True)
            except OSError:
                home = home if home.exists() else old
    home.mkdir(parents=True, exist_ok=True)
    (home / "sessions").mkdir(exist_ok=True)
    if os.name == "posix":
        os.chmod(home, 0o700)
        os.chmod(home / "sessions", 0o700)
    return home


def prune(sessions: Path, keep: int) -> None:
    for old in [s["id"] for s in list_sessions(sessions)][keep:]:
        try:
            (sessions / old).unlink()
        except OSError:
            pass


def check_folder(folder: Path) -> None:
    home = Path.home().resolve()
    if folder == home or folder == Path(folder.anchor) or not folder.is_dir():
        sys.exit(f"Pick a project folder (not your whole home folder or drive): {folder}")


def watch(args, argv: list[str]) -> None:
    root = Path(args.folder).expanduser().resolve()
    check_folder(root)
    home = data_home()
    sessions = home / "sessions"
    prune(sessions, max(args.keep - 1, 0))
    owner = Owner(home)
    settings = Settings(home / "settings.json")
    prefs = settings.data

    # Command-line flags win; otherwise Settings decide.
    sb_log = (args.trioditis_log or (os.environ.get("HLIDSKJALV_TRIODITIS_LOG") or os.environ.get("ARCADE_SWITCHBOARD_LOG"))
              or prefs["sources"]["trioditis_log"])
    sb_path = Path(sb_log).expanduser().resolve() if sb_log else None
    files = FileWatcher(root)
    git = GitWatcher(root)
    cmds = CommandWatcher(root, home / "commands.log")
    ai = TrioditisWatcher(sb_path, load_prices(home / "prices.json"))
    claude_code = ClaudeCodeWatcher(root) if prefs["sources"]["claude_code"] else None

    snapshot = files.snapshot()
    git.prime()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name)[:60] or "project"
    meta = {
        "git": git.available(), "branch": git.branch,
        "commands_hook": (home / "commands.log").exists(),
        "trioditis": bool(sb_path and sb_path.exists()),
        "claude_code": bool(claude_code and claude_code.available()),
        "terminal": bool((args.terminal or prefs["features"]["terminal"]) and term.supported()),
        "truncated": files.truncated, "version": __version__,
    }
    session_path = sessions / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.jsonl"
    # The live view continues from recent sessions, and lifetime XP survives pruning.
    hub = Hub(root.name, snapshot, session_path, meta, history=load_history(sessions), progress=Progress(home, sessions))

    # Alerts: things you didn't do yourself (see alerts.py). Each source can be switched off in Settings.
    alerter = Alerter(hub.publish, settings, home / "alerts.jsonl")
    persistence = PersistenceWatcher(root, alerter, expected=home / "expected.json")
    syslog = SystemLogWatcher(alerter)
    sb_security = TrioditisSecurityWatcher(sb_path.parent / "security.jsonl" if sb_path else None, alerter)
    cost = CostWatcher(home, alerter)
    alerts_ctx = AlertsContext(alerter, home / "alerts.jsonl", Acks(home / "alerts-ack.json"),
                               lambda: {"available": syslog.available, "why": syslog.why})

    def loop():
        while True:
            for source in (s for s in (files, git, cmds, ai, claude_code, persistence, syslog, sb_security, cost) if s):
                try:
                    for ev in source.poll():
                        hub.publish(ev)
                except Exception as e:  # one bad poll must not stop the game
                    print(f"warning: {type(source).__name__}: {e}", file=sys.stderr)
            time.sleep(args.interval)

    threading.Thread(target=loop, daemon=True).start()
    def restart():
        """Start over with the same command line (new settings apply); same PID under systemd."""
        again = [a for a in argv if a != "--no-open"] + ["--no-open"]
        os.execv(sys.executable, [sys.executable, "-m", "hlidskjalv", *again])

    shell = term.Terminal(root, owner, lambda sev, title, detail, fp: alerter.raise_(sev, "refused_access", title, detail, fingerprint=fp),
                          lambda sev, source, title, detail, fp, expected: alerter.raise_(sev, source, title, detail, fingerprint=fp, expected=expected),
                          lambda: bool(settings.data["features"]["terminal_root"])) if meta["terminal"] else None
    usage_ctx = UsageContext(home, ClaudeUsage(claude_code.dir) if claude_code else None)
    if usage_ctx.claude:
        usage_ctx.claude.plan = ClaudeUsage(claude_code.dir, all_projects=True)  # account-wide windows, cached separately
    httpd = serve(hub, sessions, args.port, shell, owner, settings, restart, alerts_ctx, usage_ctx)
    write_private(home / "server.json", json.dumps({
        "port": args.port, "pid": os.getpid(), "folder": str(root), "version": __version__,
        "started": time.time()}))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Hlidskjalv is watching {root}")
    print(f"  open {url}   (Ctrl+C to stop)")
    print(f"  same page, friendlier address in Chrome, Edge or Firefox: http://{NICE_HOST}:{args.port}/")
    if not args.no_open:
        url += f"#pair={owner.launch_code()}"  # one use: pairs this browser for settings and the terminal
    else:
        print("  to pair a browser (needed for settings and the terminal): your password in Settings > Pairing,"
              " or: python3 -m hlidskjalv pair")
    if shell:
        print("  terminal: ON (a real shell in this folder, in the Terminal tab)"
              + (" · root allowed" if prefs["features"]["terminal_root"] else " · no root") * (platform.system() == "Linux"))
    elif args.terminal or prefs["features"]["terminal"]:
        print("  note: the in-browser terminal needs Linux, macOS or Windows 10 (1809+); it stays off on this system")
    if files.truncated:
        print("  note: more than 5000 files; showing the first 5000")
    if not meta["commands_hook"]:
        print("  tip: terminal commands aren't recorded yet; see README > Record terminal commands")
    if sb_log and not meta["trioditis"]:
        print(f"  note: Trioditis log not found yet at {sb_path}; it appears after the first AI call")
    if claude_code and not meta["claude_code"]:
        print("  tip: no Claude Code session transcripts found for this folder yet; run `claude` here to see its AI steps too")
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSaved this session for Replay.")


def status_text(home: Path) -> str:
    """The usage panel, for the terminal: plan windows, spend, alerts, services."""
    from . import usage as u
    from .alerts import Acks, read_history
    from .autostart import status as auto_status
    from .cost_alerts import u_money
    from .sources import claude_code_project_dir
    lines = []
    cc = claude_code_project_dir(Path.cwd())
    w = u.ClaudeUsage(cc, all_projects=True).windows()
    prefs = Settings(home / "settings.json").data
    anchor = prefs.get("usage", {}).get("week_anchor")
    if anchor:
        w = u.ClaudeUsage(cc, all_projects=True).windows(week_anchor=u._epoch(anchor))
    lines.append("Claude plan (estimate from Claude Code transcripts on this computer)")
    for key, label in (("session", "Session (5h)"), ("week", "Weekly (7d)")):
        x = w.get(key) or {}
        if x.get("active"):
            left = x["resets_at"] - time.time()
            fam = ", ".join(f"{k} {v / 1e6:.1f}M" for k, v in (x.get("by_family") or {}).items())
            lines.append(f"  {label:<13} {x['turns']:>4} turns  {(x['in'] + x['out']) / 1e6:6.1f}M tokens  "
                         f"resets in {left / 3600:.1f} h   [{fam}]")
        else:
            lines.append(f"  {label:<13} no active window")
    b = u.budget(home)
    if b.get("available"):
        lines.append("Trioditis spend")
        lines.append(f"  today      {u_money(b['today']):>8} of {u_money(b['caps']['daily_usd'])}")
        lines.append(f"  this month {u_money(b['month']):>8} of {u_money(b['caps']['monthly_usd'])}")
        for name, p in sorted((b.get("providers") or {}).items()):
            caps = p.get("caps") or {}
            cap = f" of {u_money(caps['monthly_usd'])}" if "monthly_usd" in caps else ""
            lines.append(f"  {name:<10} {u_money(p['month']):>8}{cap} this month, {p['calls_month']} calls")
        for lim in b.get("limits") or []:
            if lim.get("remaining") is not None:
                lines.append(f"  {lim['provider']} {lim['name'].replace('_', ' ')}: {lim['remaining']:,} left"
                             + (f" of {lim['limit']:,}" if lim.get("limit") else ""))
    acct = u.accounts(home)
    for name, a in sorted((acct.get("providers") or {}).items()):
        if not a.get("error") and a.get("limit") is not None:
            lines.append(f"  {name}: {a.get('label', 'account')} {u_money(a['used'])} of {u_money(a['limit'])}")
    acks = set(Acks(home / "alerts-ack.json").ids)
    unread = [a for a in read_history(home / "alerts.jsonl") if not a.get("expected") and a.get("uid") not in acks]
    worst = max((a["severity"] for a in unread), default=None, key=lambda s: ["info", "medium", "high", "critical"].index(s))
    lines.append(f"Alerts: {len(unread)} unread" + (f", highest {worst}" if worst else ""))
    for a in unread[-3:]:
        lines.append(f"  [{a['severity']}] {a['title']}")
    st = auto_status(home, logs=0)
    for s in st.get("services", []):
        lines.append(f"{s['app']}: " + ("running" if s.get("active") else "on, not running" if s.get("enabled") else "not set to start at login"))
    return "\n".join(lines)


def password(args) -> None:
    """Set or remove the pairing password (asked for twice, never shown)."""
    import getpass
    owner = Owner(data_home())
    if args.clear:
        owner.set_password(None)
        print("Password removed. Browsers now pair with a one-time link only (python3 -m hlidskjalv pair).")
        return
    pw = getpass.getpass("New password (at least 8 characters): ")
    if pw != getpass.getpass("Again: "):
        sys.exit("They differ; nothing changed.")
    try:
        owner.set_password(pw)
    except ValueError as e:
        sys.exit(f"Nothing changed: {e}")
    print("Password set. In the browser: Settings > Pairing > enter it. Already paired browsers stay paired.")


def two_factor(args) -> None:
    """Authenticator-app codes for pairing: setup, status, or the emergency off switch."""
    owner = Owner(data_home())
    if args.action == "status":
        print("two-factor: " + ("ON (authenticator app)" if owner.totp_enabled() else "off"))
    elif args.action == "off":
        owner.totp_disable()
        print("Two-factor authentication is off. Pairing with the password alone works again.")
    else:
        import getpass
        import socket
        if owner.totp_enabled():
            sys.exit("Already set up. Run `python3 -m hlidskjalv 2fa off` first to start over.")
        d = owner.totp_begin(f"{getpass.getuser()}@{socket.gethostname()}")
        print("In your authenticator app, add an account by entering this key (or scan the QR code in Settings > Pairing):")
        print(f"  {d['secret']}\n  {d['uri']}")
        codes = owner.totp_confirm(input("Code the app shows now: ").strip())
        if codes is None:
            sys.exit("That code didn't match; nothing changed.")
        print("Two-factor authentication is on. Recovery codes (each works once; keep them somewhere safe):")
        print("  " + "  ".join(codes))


def pair(args) -> None:
    home = data_home()
    if args.rotate:
        rotate_key(home)
        print("New owner key: every browser is un-paired.")
    try:
        port = int(json.loads((home / "server.json").read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        port = int(os.environ.get("HLIDSKJALV_PORT") or os.environ.get("ARCADE_PORT") or "8788")
    url = f"http://127.0.0.1:{port}/#pair={issue_pair_code(home)}"
    print(f"Open this once, within 10 minutes, in the browser to pair:\n  {url}")
    if args.open:
        webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="hlidskjalv", description="A game-like live view of your coding workflow.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="watch a project folder and open Hlidskjalv")
    w.add_argument("folder", nargs="?", default=".")
    w.add_argument("--port", type=int, default=int(os.environ.get("HLIDSKJALV_PORT") or os.environ.get("ARCADE_PORT") or "8788"))
    w.add_argument("--trioditis-log", "--switchboard-log", dest="trioditis_log",  # old name: one more version
                   help="path to Trioditis' logs/decisions.jsonl")
    w.add_argument("--interval", type=float, default=1.0, help="seconds between checks")
    w.add_argument("--keep", type=int, default=50, help="how many past sessions to keep for Replay")
    w.add_argument("--no-open", action="store_true", help="don't open the browser")
    w.add_argument("--terminal", action="store_true",
                   help="also allow a real shell in the browser (Terminal tab); off by default")

    sub.add_parser("status", help="plan windows, spend against caps, unread alerts and services, in the terminal")

    pr = sub.add_parser("pair", help="print a one-time link that pairs a browser with the running Hlidskjalv")
    pr.add_argument("--open", action="store_true", help="also open it in the browser")
    pr.add_argument("--rotate", action="store_true", help="new owner key first: un-pairs every browser")

    pw = sub.add_parser("password", help="set the password that pairs a browser (Settings > Pairing)")
    pw.add_argument("--clear", action="store_true", help="remove the password; the one-time link still works")

    tf = sub.add_parser("2fa", help="authenticator-app codes for pairing: setup | status | off")
    tf.add_argument("action", choices=["setup", "status", "off"])

    a = sub.add_parser("autostart", help="start Hlidskjalv at login: enable <folder> | disable | status")
    a.add_argument("action", choices=["enable", "disable", "status"])
    a.add_argument("folder", nargs="?", help="project folder to watch (for enable)")
    a.add_argument("--port", type=int, default=int(os.environ.get("HLIDSKJALV_PORT") or os.environ.get("ARCADE_PORT") or "8788"))

    h = sub.add_parser("hook", help="print the shell snippet that records terminal commands")
    h.add_argument("shell", choices=sorted(HOOKS))

    sub.add_parser("sessions", help="list saved sessions")

    args = ap.parse_args(argv)
    if args.cmd == "watch":
        watch(args, list(argv if argv is not None else sys.argv[1:]))
    elif args.cmd == "pair":
        pair(args)
    elif args.cmd == "password":
        password(args)
    elif args.cmd == "2fa":
        two_factor(args)
    elif args.cmd == "status":
        print(status_text(data_home()))
    elif args.cmd == "autostart":
        from . import autostart
        home = data_home()
        if args.action == "enable":
            if not args.folder:
                sys.exit("Which folder? python3 -m hlidskjalv autostart enable ~/projects/my-app")
            folder = Path(args.folder).expanduser().resolve()
            check_folder(folder)
            print(autostart.enable(folder, args.port, home))
            print("Pair a browser: python3 -m hlidskjalv pair --open")
        elif args.action == "disable":
            print(autostart.disable())
        else:
            print(autostart.format_status(autostart.status(home)))
    elif args.cmd == "hook":
        print(HOOKS[args.shell].strip())
    elif args.cmd == "sessions":
        for s in list_sessions(data_home() / "sessions"):
            print(f"{s['id']}  {s['bytes']:>8} bytes")


if __name__ == "__main__":
    main()
