"""Risky shell commands, the server's copy of the page's RISKS table (static/app.js).

A test keeps the two identical: change one, change both. classify() runs on the raw command, so a
secret that redact() masks in the event still counts; the alert shows only the masked command.
"""

from __future__ import annotations

import re

# (pattern as written in app.js, ignore case, level, what it means)
RULES: list[tuple[str, bool, str, str]] = [
    (r"\brm\s+(-\w*r\w*f|-\w*f\w*r)\b|\brm\s+-r\b.*\s-f\b", True, "danger", "Deletes files and folders for good. There's no recycle bin."),
    (r"remove-item\b.*-recurse", True, "danger", "Deletes a folder and everything in it."),
    (r"\b(rd|rmdir)\s+\/s", True, "danger", "Deletes a folder and everything in it."),
    (r"\bsudo\b", False, "danger", "Runs as administrator: it can change anything on the computer. Only approve it if you know why it's needed."),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b|\|\s*iex\b", True, "danger",
     "Downloads a script and runs it straight away, unseen."),
    (r"\bgit\s+push\b.*(--force\b|\s-f\b)", False, "danger", "Overwrites history on GitHub. Other people's work can be lost."),
    (r"\bgit\s+reset\s+--hard\b", False, "danger", "Throws away every unsaved change."),
    (r"\bgit\s+clean\s+-\w*f", False, "danger", "Deletes files Git isn't tracking, which may include your work."),
    (r"\bchmod\s+(-R\s+)?(777|a\+w)\b", False, "danger", "Lets every user on the computer change these files."),
    (r"--privileged\b", False, "danger", "Gives a container full control of your computer."),
    (r"\b(cat|type|get-content|more|less|echo)\b.*\.env\b(?!\.example)", True, "caution",
     "Prints secret values on screen, where they can end up in logs or AI chats."),
    (r"\bgit\s+add\s+(\.|-A|--all)(\s|$)", False, "caution", "Adds everything. Check that .gitignore keeps .env and other secrets out."),
    (r"\b(pip3?|npm)\s+(install|i)\s+(?!-r\b)(?!-e\b)[a-z]", True, "caution",
     "Installs from the internet. Check the name is spelled exactly right: look-alike packages exist."),
    (r"\b(api[_-]?key|token|password|secret)\s*=", True, "caution",
     "Contains what looks like a secret. Keep keys in .env instead of typing them into commands."),
]
_COMPILED = [(re.compile(p, re.I if i else 0), level, text) for p, i, level, text in RULES]
SEVERITY = {"danger": "high", "caution": "medium"}  # as the page's RISK_SEV


def classify(cmd: str) -> list[tuple[str, str]]:
    """[(level, meaning)] for every rule the command matches, in table order."""
    return [(level, text) for rx, level, text in _COMPILED if rx.search(cmd)]


def severity(found: list[tuple[str, str]]) -> str | None:
    if not found:
        return None
    return SEVERITY["danger" if any(level == "danger" for level, _ in found) else "caution"]
