"""Find system programs without trusting PATH.

Hlidskjalv runs git, crontab, journalctl, systemctl and launchctl to do its
job. A malicious package that drops a fake `git` into ~/.local/bin (which
is usually on PATH) must not be able to run itself through the tool meant
to notice it, so programs are looked up in system directories only. The
shell for the Terminal tab is the exception: that is deliberately yours
($SHELL), checked to be an absolute, executable path in terminal.py.
"""

from __future__ import annotations

import os
import shutil

if os.name == "nt":
    _root = os.environ.get("SystemRoot") or r"C:\Windows"
    SYSTEM_PATH = os.pathsep.join([os.path.join(_root, "System32"), _root,
                                   os.path.join(_root, "System32", "WindowsPowerShell", "v1.0"),
                                   r"C:\Program Files\Git\cmd", r"C:\Program Files\PowerShell\7"])
else:
    SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"


def which(name: str) -> str | None:
    """Absolute path of a program from the system directories, or None."""
    exe = shutil.which(name, path=SYSTEM_PATH)
    return exe if exe and os.path.isabs(exe) else None
