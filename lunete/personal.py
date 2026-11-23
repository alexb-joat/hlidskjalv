"""The personal store: ~/.lunete/graph/personal.db (architecture section 7).

Personal lines (study, a morning run) live here, on this machine only. The sync code never imports
this module, and tests/test_personal.py fails if it ever does, so sync can't open personal.db by
accident. Code that needs the personal store imports it from here and nowhere else.
"""
from __future__ import annotations

from pathlib import Path

from .graph import Store

FILE = Path("graph") / "personal.db"


def path(home: Path) -> Path:
    return Path(home) / FILE


def open_store(home: Path) -> Store:
    """Open the personal store under the data home, making it on first use."""
    return Store.open_or_create(path(home), "personal")
