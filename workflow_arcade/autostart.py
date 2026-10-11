"""Start the Arcade at login, and show exactly what starts and how.

`python -m workflow_arcade autostart enable <folder> | disable | status`.
Only this command line installs or removes anything: the web page shows the
policy (status) but never edits your login items.

- Linux: a systemd *user* unit (no root), hardened like Switchboard's.
- macOS: a launchd agent in ~/Library/LaunchAgents.
- Windows: a hidden Scheduled Task for the current user (no admin rights).

The terminal's on/off comes from Settings, not from this file. Under the
Linux unit, NoNewPrivileges means `sudo` doesn't work in the in-page terminal.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

UNIT = "workflow-arcade.service"
LABEL = "io.github.workflow-arcade.watch"
TASK = "Workflow Arcade"
PKG_PARENT = str(Path(__file__).resolve().parent.parent)  # works without `pip install`

# Switchboard's own installers (in its repo, deploy/*) use these names.
SWITCHBOARD = {"linux": "switchboard.service", "darwin": "io.github.switchboard.router",
               "windows": "Switchboard LLM router"}

HARDENING = {
    "UMask=0077": "files it creates are readable only by you",
    "NoNewPrivileges=true": "it and its children can never gain privileges (no sudo, no setuid)",
    "LockPersonality=true": "can't switch kernel execution domains",
    "RestrictRealtime=true": "can't take realtime CPU priority",
    "SystemCallArchitectures=native": "only native system calls",
    "MemoryMax=2G": "memory cap, shared with terminal shells it starts",
    "Restart=on-failure": "comes back after a crash, not after you stop it",
}


def system() -> str:
    s = platform.system().lower()
    return "windows" if s.startswith("win") else s


def interpreter() -> str:
    """The base Python, not a virtualenv's: the Arcade needs only the standard library,
    and a venv that gets deleted or rebuilt must not break the login item."""
    base = getattr(sys, "_base_executable", None) if sys.prefix != sys.base_prefix else None
    exe = base if base and os.path.exists(base) else sys.executable
    stable = Path(exe).with_name("python3")  # survives 3.13 -> 3.14 upgrades (POSIX; harmless elsewhere)
    if stable.exists() and os.path.realpath(stable) == os.path.realpath(exe):
        return str(stable)
    return exe


def command(folder: Path, port: int) -> list[str]:
    exe = interpreter()
    if system() == "windows":
        w = Path(exe).with_name("pythonw.exe")  # no console window
        exe = str(w) if w.exists() else exe
    return [exe, "-m", "workflow_arcade", "watch", str(folder), "--no-open", "--port", str(port)]


def _home_env() -> dict:
    return {"ARCADE_HOME": os.environ["ARCADE_HOME"]} if os.environ.get("ARCADE_HOME") else {}


def render_unit(folder: Path, port: int) -> str:
    env = {"PYTHONPATH": PKG_PARENT, "PYTHONUNBUFFERED": "1", **_home_env()}  # unbuffered: messages reach the journal
    lines = ["[Unit]", "Description=Workflow Arcade: live view of your work in " + str(folder),
             "After=default.target", "", "[Service]",
             f"WorkingDirectory={folder}",
             *(f'Environment="{k}={v}"' for k, v in env.items()),
             "ExecStart=" + " ".join(shlex.quote(a) for a in command(folder, port)),
             "RestartSec=5", *HARDENING, "", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def render_plist(folder: Path, port: int, log: Path) -> str:
    env = {"PYTHONPATH": PKG_PARENT, **_home_env()}
    args = "".join(f"<string>{escape(a)}</string>" for a in command(folder, port))
    envs = "".join(f"<key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>WorkingDirectory</key><string>{escape(str(folder))}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict>{envs}</dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>Umask</key><integer>63</integer>
  <key>StandardOutPath</key><string>{escape(str(log))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict>
</plist>
"""


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def render_task_script(folder: Path, port: int) -> str:
    cmd = command(folder, port)
    args = subprocess.list2cmdline(cmd[1:])
    return (f"$env:PYTHONPATH = {_ps_quote(PKG_PARENT)}; $a = New-ScheduledTaskAction -Execute {_ps_quote(cmd[0])} -Argument {_ps_quote(args)} "
            f"-WorkingDirectory {_ps_quote(str(folder))}; "
            "$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; "
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero); "
            "$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited; "
            f"Register-ScheduledTask -TaskName {_ps_quote(TASK)} -Action $a -Trigger $t -Settings $s "
            "-Principal $p -Force | Out-Null; "
            f"Start-ScheduledTask -TaskName {_ps_quote(TASK)}")


def unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _run(*args: str, timeout: float = 15) -> tuple[int, str]:
    exe = shutil.which(args[0])
    if not exe:
        return 127, f"{args[0]} not found"
    try:
        r = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def enable(folder: Path, port: int, home: Path) -> str:
    sysname = system()
    if sysname == "linux":
        p = unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_unit(folder, port), encoding="utf-8")
        _run("systemctl", "--user", "daemon-reload")
        code, out = _run("systemctl", "--user", "enable", "--now", UNIT)
        return out if code else f"on: starts at login. Unit: {p}\nlogs: journalctl --user -u {UNIT} -f"
    if sysname == "darwin":
        p, log = plist_path(), home / "arcade.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            p.write_text(render_plist(folder, port, log), encoding="utf-8")
        finally:
            os.umask(old)
        domain = f"gui/{os.getuid()}"
        _run("launchctl", "bootout", f"{domain}/{LABEL}")
        code, out = _run("launchctl", "bootstrap", domain, str(p))
        return out if code else f"on: starts at login. Agent: {p}\nlogs: {log}"
    if sysname == "windows":
        code, out = _run("powershell", "-NoProfile", "-NonInteractive", "-Command", render_task_script(folder, port))
        return out if code else f"on: starts at logon (Scheduled Task '{TASK}')"
    return f"autostart isn't supported on {sysname}"


def disable() -> str:
    sysname = system()
    if sysname == "linux":
        _run("systemctl", "--user", "disable", "--now", UNIT)
        unit_path().unlink(missing_ok=True)
        _run("systemctl", "--user", "daemon-reload")
    elif sysname == "darwin":
        _run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}")
        plist_path().unlink(missing_ok=True)
    elif sysname == "windows":
        _run("powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK)} -Confirm:$false -ErrorAction SilentlyContinue")
    return "off: nothing starts at login any more"


# ---------------------------------------------------------------- status (read-only)

def _systemd_status(unit: str, file: Path | None, logs: int) -> dict:
    out = {"kind": "systemd user unit", "name": unit, "file": str(file) if file else None,
           "installed": bool(file and file.exists())}
    if file and file.exists():
        text = file.read_text(encoding="utf-8", errors="replace")
        out["exec"] = next((ln[10:] for ln in text.splitlines() if ln.startswith("ExecStart=")), None)
        out["hardening"] = [{"rule": ln, "means": HARDENING.get(ln, "")} for ln in text.splitlines()
                            if ln.split("=")[0] in {h.split("=")[0] for h in HARDENING}
                            or ln.split("=")[0] in ("PrivateTmp", "ProtectSystem", "ProtectHome")]
    code, txt = _run("systemctl", "--user", "show", unit, "-p",
                     "UnitFileState,ActiveState,SubState,MainPID,ActiveEnterTimestamp,FragmentPath")
    if code == 0:
        props = dict(ln.split("=", 1) for ln in txt.splitlines() if "=" in ln)
        out.update(enabled=props.get("UnitFileState") == "enabled", active=props.get("ActiveState") == "active",
                   state=f"{props.get('ActiveState', '?')} ({props.get('SubState', '?')})",
                   pid=int(props.get("MainPID") or 0) or None, since=props.get("ActiveEnterTimestamp") or None)
        if not out["installed"] and props.get("FragmentPath"):
            fp = Path(props["FragmentPath"])
            if fp.exists():
                return _systemd_status(unit, fp, logs)
    code, txt = _run("journalctl", "--user", "-u", unit, "-n", str(logs), "-o", "short-iso", "--no-pager")
    out["logs"] = txt.splitlines()[-logs:] if code == 0 else []
    out["stop"] = f"systemctl --user stop {unit}"
    return out


def _launchd_status(label: str, file: Path, log: Path | None, logs: int) -> dict:
    out = {"kind": "launchd agent", "name": label, "file": str(file), "installed": file.exists()}
    code, txt = _run("launchctl", "print", f"gui/{os.getuid()}/{label}")
    out.update(enabled=file.exists(), active=code == 0 and "state = running" in txt)
    if log and log.exists():
        out["logs"] = log.read_text(encoding="utf-8", errors="replace").splitlines()[-logs:]
    out["stop"] = f"launchctl bootout gui/{os.getuid()}/{label}"
    return out


def _task_status(name: str) -> dict:
    code, txt = _run("powershell", "-NoProfile", "-NonInteractive", "-Command",
                     f"(Get-ScheduledTask -TaskName {_ps_quote(name)} -ErrorAction Stop).State")
    return {"kind": "Scheduled Task", "name": name, "installed": code == 0, "enabled": code == 0 and txt != "Disabled",
            "active": txt.strip() == "Running", "state": txt.strip() if code == 0 else "not installed",
            "stop": f"Stop-ScheduledTask -TaskName '{name}'"}


def status(home: Path, logs: int = 50) -> dict:
    """What starts at login, for both apps. Never changes anything."""
    sysname = system()
    if sysname == "linux":
        arcade = _systemd_status(UNIT, unit_path(), logs)
        sb = _systemd_status(SWITCHBOARD["linux"], unit_path().with_name(SWITCHBOARD["linux"]), logs)
    elif sysname == "darwin":
        arcade = _launchd_status(LABEL, plist_path(), home / "arcade.log", logs)
        sb = _launchd_status(SWITCHBOARD["darwin"], plist_path().with_name(SWITCHBOARD["darwin"] + ".plist"), None, logs)
    elif sysname == "windows":
        arcade, sb = _task_status(TASK), _task_status(SWITCHBOARD["windows"])
    else:
        return {"system": sysname, "services": []}
    arcade.update(app="Workflow Arcade", what="Watches one project folder and serves this page on 127.0.0.1",
                  enable="python3 -m workflow_arcade autostart enable <folder>",
                  remove="python3 -m workflow_arcade autostart disable")
    sb.update(app="Switchboard", what="Routes AI requests to local or cloud models on 127.0.0.1:8787",
              enable="./deploy/<your system>/install-service.sh  (in the Switchboard folder)",
              remove="./deploy/<your system>/install-service.sh --remove")
    return {"system": sysname, "services": [arcade, sb],
            "policy": ["Nothing starts at login unless you ran the enable command yourself.",
                       "Both listen on 127.0.0.1 only and run as you, never as administrator.",
                       "The in-page terminal stays off at login unless you switched it on in Settings.",
                       "This page can show the policy but can't change it: enabling and removing are command-line only."]}


def format_status(st: dict) -> str:
    out = []
    for s in st["services"]:
        out.append(f"{s['app']} ({s['kind']} {s['name']}): "
                   + ("ON" if s.get("enabled") else "off") + (", running" if s.get("active") else ", not running"))
        if s.get("exec"):
            out.append(f"  runs:  {s['exec']}")
        if s.get("since"):
            out.append(f"  since: {s['since']}")
        if s.get("file"):
            out.append(f"  file:  {s['file']}")
        out.append(f"  remove: {s['remove']}")
    out += ["", *("- " + p for p in st.get("policy", []))]
    return "\n".join(out)
