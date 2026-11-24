"""Lunete: a local, game-like view of what you and your AI helper do in a project."""

__version__ = "0.5.0"

import os

#: Each env var's names, newest first: the 0.5 name, then the names it had before the renames.
ENV_NAMES = {
    "HOME": ("LUNETE_HOME", "HLIDSKJALV_HOME", "ARCADE_HOME"),
    "PORT": ("LUNETE_PORT", "HLIDSKJALV_PORT", "ARCADE_PORT"),
    "TRIODITIS_LOG": ("LUNETE_TRIODITIS_LOG", "HLIDSKJALV_TRIODITIS_LOG", "ARCADE_SWITCHBOARD_LOG"),
    "SSO_FAKE": ("LUNETE_SSO_FAKE", "HLIDSKJALV_SSO_FAKE"),
}


def env(what: str) -> str | None:
    """The first of a setting's env var names that is set and not empty (LUNETE_* first; the
    HLIDSKJALV_* and ARCADE_* names still work for one release)."""
    for name in ENV_NAMES[what]:
        value = os.environ.get(name)
        if value:
            return value
    return None
