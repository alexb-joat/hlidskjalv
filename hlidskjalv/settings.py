"""Settings in ~/.hlidskjalv/settings.json: one schema, validated on every write.

The page reads them with GET /api/settings and changes them with POST
/api/settings (owner key required, see auth.py). A POST carries only the
fields that change; they are merged onto the current settings, checked
against SCHEMA, and written atomically. Unknown keys and bad values are
refused with a message saying which one and why, nothing is half-applied.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

from .auth import write_private

VERSION = 1
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
VIEWS = ["live", "replay", "learn", "details", "terminal", "alerts"]
SEVERITIES = ["info", "medium", "high", "critical"]

# Leaf specs: (kind, default, *args). Dicts nest.
SCHEMA: dict = {
    "setup_done": ("bool", False),
    "look": {
        "mode": ("enum", "system", ["system", "light", "dark"]),
        "palette": ("enum", "neutral", ["neutral", "graphite", "paper", "mint", "indigo", "rose", "amber"]),
        "accent": ("color", None),
        "surface": ("color", None),
        "text": ("color", None),
        "font": ("enum", "system", ["system", "clean", "humanist", "geometric", "rounded", "readable", "hyperlegible",
                                    "plex", "display", "serif", "book", "slab", "mono", "code"]),
        "font_size": ("int", 15, 12, 20),
        "brightness": ("int", 0, -40, 40),  # percent towards white (+) or black (-), page and panels
        "density": ("enum", "comfortable", ["compact", "comfortable", "spacious"]),
        "radius": ("int", 12, 0, 24),
        "animations": ("bool", True),
        "background": {
            "type": ("enum", "solid", ["solid", "linear", "radial", "particles"]),
            "colors": ("colors", [], 0, 3),
            "angle": ("int", 135, 0, 360),
            "particles": {
                "density": ("int", 60, 0, 200),
                "speed": ("float", 1.0, 0.0, 5.0),
                "size": ("float", 2.0, 0.5, 6.0),
                "color": ("color", None),
                "links": ("bool", True),
            },
        },
    },
    "layout": {
        "nav_position": ("enum", "top", ["top", "bottom", "left", "right"]),
        "nav_labels": ("bool", False),
        "views": ("views", list(VIEWS)),
        "hidden": ("viewset", []),
        "default_view": ("enum", "live", VIEWS),
    },
    "features": {
        "terminal": ("bool", False),
        "xp": ("bool", True),
    },
    "alerts": {
        "ai_commands": ("bool", True),
        "refused_access": ("bool", True),
        "persistence": ("bool", True),
        "system_log": ("bool", True),
        "cost": ("bool", True),
        "view": ("enum", "grouped", ["grouped", "timeline", "compact"]),
        "toast_min": ("enum", "high", SEVERITIES),
        "notify": ("bool", False),
        "sound": ("bool", False),
        "colors": {s: ("color", None) for s in SEVERITIES},
    },
    "sources": {
        "trioditis_log": ("path", None),
        "claude_code": ("bool", True),
    },
    "usage": {
        "fresh_threshold": ("int", 150_000, 10_000, 1_000_000),
        # When your Claude plan's weekly window resets, copied once from the official usage panel.
        # Other apps and machines share that window but leave no transcripts here, so it can't be derived.
        "week_anchor": ("datetime", None),
    },
}

# Changing these only takes effect after a restart (POST /api/restart).
RESTART_KEYS = {("features", "terminal"), ("sources", "trioditis_log"), ("sources", "claude_code")}


class SettingsError(ValueError):
    pass


def defaults(schema: dict = SCHEMA) -> dict:
    return {k: defaults(v) if isinstance(v, dict) else copy.deepcopy(v[1]) for k, v in schema.items()}


def _leaf(spec: tuple, value, where: str):
    kind = spec[0]
    if kind == "bool":
        if isinstance(value, bool):
            return value
    elif kind == "enum":
        if value in spec[2]:
            return value
        raise SettingsError(f"{where}: must be one of {', '.join(spec[2])}")
    elif kind == "int":
        if isinstance(value, int) and not isinstance(value, bool) and spec[2] <= value <= spec[3]:
            return value
        raise SettingsError(f"{where}: must be a whole number from {spec[2]} to {spec[3]}")
    elif kind == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and spec[2] <= value <= spec[3]:
            return float(value)
        raise SettingsError(f"{where}: must be a number from {spec[2]} to {spec[3]}")
    elif kind == "color":
        if value is None or (isinstance(value, str) and COLOR.match(value)):
            return value.lower() if value else None
        raise SettingsError(f"{where}: must be a colour like #1a2b3c, or null")
    elif kind == "colors":
        if isinstance(value, list) and spec[2] <= len(value) <= spec[3] and all(isinstance(c, str) and COLOR.match(c) for c in value):
            return [c.lower() for c in value]
        raise SettingsError(f"{where}: must be a list of up to {spec[3]} colours like #1a2b3c")
    elif kind == "views":
        if isinstance(value, list) and sorted(value) == sorted(VIEWS):
            return list(value)
        raise SettingsError(f"{where}: must list every view exactly once: {', '.join(VIEWS)}")
    elif kind == "viewset":
        if isinstance(value, list) and len(set(value)) == len(value) and all(v in VIEWS for v in value) and len(value) < len(VIEWS):
            return list(value)
        raise SettingsError(f"{where}: must be a list of views to hide, leaving at least one")
    elif kind == "datetime":
        if value is None:
            return None
        if isinstance(value, str) and len(value) <= 40:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return value
            except ValueError:
                pass
        raise SettingsError(f"{where}: must be a date and time like 2026-10-16T09:00, or null")
    elif kind == "path":
        if value is None or (isinstance(value, str) and 0 < len(value) <= 1000 and "\0" not in value):
            return value
        raise SettingsError(f"{where}: must be a file path, or null")
    raise SettingsError(f"{where}: wrong type")


def merge(current: dict, change: dict, schema: dict = SCHEMA, where: str = "") -> dict:
    """current + change, validated. Raises SettingsError on the first problem."""
    if not isinstance(change, dict):
        raise SettingsError(f"{where or 'settings'}: must be an object")
    out = copy.deepcopy(current)
    for k, v in change.items():
        path = f"{where}.{k}" if where else k
        if k not in schema:
            raise SettingsError(f"{path}: unknown setting")
        spec = schema[k]
        out[k] = merge(out.get(k, defaults(spec)), v, spec, path) if isinstance(spec, dict) else _leaf(spec, v, path)
    return out


def _restart_needed(before: dict, after: dict) -> bool:
    return any(before[a][b] != after[a][b] for a, b in RESTART_KEYS)


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        """Saved settings over defaults. Anything invalid in the file falls back to its default."""
        base = defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return base
        if not isinstance(raw, dict):
            return base
        raw.pop("version", None)
        src = raw.get("sources")
        if isinstance(src, dict) and "switchboard_log" in src and "trioditis_log" not in src:
            src["trioditis_log"] = src.pop("switchboard_log")  # renamed in 0.3

        def keep(cur, change, schema):
            for k, v in change.items():
                if k not in schema:
                    continue
                if isinstance(schema[k], dict):
                    if isinstance(v, dict):
                        keep(cur[k], v, schema[k])
                    continue
                try:
                    cur[k] = _leaf(schema[k], v, k)
                except SettingsError:
                    pass
        keep(base, raw, SCHEMA)
        return base

    def update(self, change: dict) -> tuple[dict, bool]:
        """Apply and save a change. Returns (settings, whether a restart is needed)."""
        after = merge(self.data, change)
        restart = _restart_needed(self.data, after)
        write_private(self.path, json.dumps({"version": VERSION, **after}, indent=2))
        self.data = after
        return after, restart

    def reset(self) -> dict:
        self.data = defaults()
        write_private(self.path, json.dumps({"version": VERSION, **self.data}, indent=2))
        return self.data
