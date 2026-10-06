"""Mask secrets in text before it is shown on screen or written to a session file."""

from __future__ import annotations

import re

MASK = "•••"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # NAME=value where the name looks secret (API_KEY=..., GITHUB_TOKEN=..., DB_PASSWORD=...)
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIALS?)[A-Z0-9_]*)"
                r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"), rf"\1\2{MASK}"),
    # --password value / --token=value / --api-key value
    (re.compile(r"(?i)(--?(?:password|passwd|token|api[-_]?key|secret)(?:=|\s+))(\"[^\"]*\"|'[^']*'|\S+)"),
     rf"\1{MASK}"),
    # Authorization headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {MASK}"),
    # Credentials inside URLs: https://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), rf"\1{MASK}@"),
    # Well-known key shapes
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), MASK),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), MASK),
    # Long random-looking strings (hex or base64-ish, 32+ chars)
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), MASK),
]


def redact(text: str, limit: int = 500) -> str:
    """Return text with likely secrets masked, trimmed to `limit` characters."""
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
