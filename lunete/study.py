"""Study lines: what you're learning, how much time it got this week, and how the checks went.

Everything lives in the personal store as ordinary graph items, written through ops.apply:
- a study line is a `line` with fields {study: true, week_min, checks?: ["YYYY-MM-DD", ...]};
- a piece of a line (one part of a course or sprint) is a `task` on the line, fields {est_min: [low, high]},
  status `open` or `done`;
- an entry is an `event` on the line: title = the note, fields {study: true, day, minutes, score?, url?},
  plus a `relates` link to the piece it was for, if any.

No streaks and no points: a missed day costs nothing, and a met target says "done for this week".
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re

from . import ops
from .ids import uuid7

MAX_MINUTES = 24 * 60
MAX_WEEK_MIN = 7 * 24 * 60
WEEKS_SHOWN = 8
_DUR = re.compile(r"^\s*(?:(\d{1,3})\s*h)?\s*(?:(\d{1,4})\s*m(?:in)?)?\s*$", re.I)


class StudyError(ValueError):
    """A study change that can't be made. The message says why and what to do instead."""


def minutes(text: str | int) -> int:
    """'6h', '90m', '1h30m' or a plain number of minutes."""
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    t = str(text).strip()
    if t.isdigit():
        return int(t)
    m = _DUR.match(t)
    if not t or not m or not (m.group(1) or m.group(2)):
        raise StudyError(f"time {text!r}: write it like 6h, 90m or 1h30m")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def minutes_range(text: str) -> list[int]:
    """'3-5h' → [180, 300]; '4h' → [240, 240]."""
    t = str(text).strip()
    m = re.match(r"^(\d{1,3})\s*-\s*(\d{1,3})\s*h$", t, re.I)
    if m:
        lo, hi = int(m.group(1)) * 60, int(m.group(2)) * 60
    else:
        lo = hi = minutes(t)
    if lo > hi:
        raise StudyError(f"estimate {text!r}: the low end comes first, like 3-5h")
    return [lo, hi]


def piece_spec(text: str) -> tuple[str, list[int]]:
    """'S1 stats toolkit=3-5h' → ('S1 stats toolkit', [180, 300])."""
    name, sep, est = str(text).rpartition("=")
    if not sep or not name.strip():
        raise StudyError(f"piece {text!r}: write it as NAME=ESTIMATE, like \"S1 stats toolkit=3-5h\"")
    return name.strip(), minutes_range(est)


def _day(text) -> str:
    try:
        return dt.date.fromisoformat(str(text)).isoformat()
    except ValueError:
        raise StudyError(f"date {text!r}: use YYYY-MM-DD, like 2026-11-23") from None


def _write(store, kind, target, patch, channel):
    r = ops.apply(store, ops.make(store, kind, target, patch), channel)
    if not r["ok"]:
        raise StudyError(r["reason"])
    return r


def _check_week(week_min):
    if not isinstance(week_min, int) or isinstance(week_min, bool) or not 0 <= week_min <= MAX_WEEK_MIN:
        raise StudyError(f"weekly target {week_min!r}: between 0 and {MAX_WEEK_MIN // 60}h")


# ---------------------------------------------------------------- lines and pieces

def lines(store) -> list[dict]:
    return [i for i in store.items("line") if i["fields"].get("study") is True]


def find_line(store, name_or_id: str) -> dict:
    """A study line by id, or by name (case-insensitive)."""
    found = [li for li in lines(store) if li["id"] == name_or_id or li["title"].lower() == str(name_or_id).lower()]
    if not found:
        names = ", ".join(li["title"] for li in lines(store)) or "none yet"
        raise StudyError(f"no study line {name_or_id!r} (lines: {names}); add one with `lunete study add-line`")
    return found[0]


def plan_line(name: str, week_min: int, pieces=(), checks=()) -> dict:
    """What add_line would write, for --dry-run and for add_line itself."""
    name = str(name).strip()
    if not name or len(name) > 80:
        raise StudyError("a line's name is 1 to 80 characters")
    _check_week(week_min)
    fields = {"study": True, "week_min": week_min}
    if checks:
        fields["checks"] = sorted(_day(c) for c in checks)
    return {"name": name, "fields": fields,
            "pieces": [{"title": t, "est_min": est} for t, est in pieces]}


def add_line(store, name: str, week_min: int, pieces=(), checks=(), channel="cli") -> str:
    plan = plan_line(name, week_min, pieces, checks)
    if any(li["title"].lower() == plan["name"].lower() for li in lines(store)):
        raise StudyError(f"a study line called {plan['name']!r} exists; change its target with `lunete study target`")
    line = str(uuid7())
    _write(store, "item.create", line, {"kind": "line", "title": plan["name"], "status": "open",
                                        "fields": plan["fields"]}, channel)
    for p in plan["pieces"]:
        add_piece(store, line, p["title"], p["est_min"], channel)
    return line


def add_piece(store, line: str, title: str, est_min: list[int], channel="page") -> str:
    title = str(title).strip()
    if not title or len(title) > 120:
        raise StudyError("a piece's name is 1 to 120 characters")
    piece = str(uuid7())
    _write(store, "item.create", piece, {"kind": "task", "title": title, "status": "open",
                                         "fields": {"est_min": list(est_min)}}, channel)
    try:
        _write(store, "link.add", {"from": piece, "rel": "on_line", "to": line}, {}, channel)
    except StudyError:
        _write(store, "item.delete", piece, {}, channel)
        raise
    return piece


def set_target(store, line: str, week_min: int, channel="page") -> None:
    _check_week(week_min)
    _write(store, "item.update", find_line(store, line)["id"], {"fields": {"week_min": week_min}}, channel)


def set_piece_done(store, piece: str, done: bool, channel="page") -> None:
    row = store.item(piece)
    if row is None or row["kind"] != "task":
        raise StudyError(f"no piece {piece}")
    want = "done" if done else "open"
    if row["status"] != want:
        _write(store, "item.status", piece, {"from": row["status"], "to": want}, channel)


# ---------------------------------------------------------------- entries

def _entry_fields(day, mins, score=None, url=None) -> dict:
    mins = minutes(mins)
    if not 1 <= mins <= MAX_MINUTES:
        raise StudyError(f"minutes {mins}: between 1 and {MAX_MINUTES}")
    fields = {"study": True, "day": _day(day), "minutes": mins}
    if score not in (None, ""):
        if isinstance(score, bool) or not str(score).isdigit() or not 0 <= int(score) <= 10:
            raise StudyError(f"score {score!r}: a whole number from 0 to 10")
        fields["score"] = int(score)
    if url not in (None, ""):
        url = str(url).strip()
        if not re.match(r"^https?://\S{1,500}$", url):
            raise StudyError("link: a web address starting with http:// or https://")
        fields["url"] = url
    return fields


def _note(note) -> str:
    note = (note or "").strip()
    if len(note) > 200:
        raise StudyError("a note is at most 200 characters")
    return note


def _pieces_of(store, line_id: str) -> set[str]:
    return {lk["from_id"] for lk in store.links("on_line") if lk["to_id"] == line_id
            and (store.item(lk["from_id"]) or {"kind": ""})["kind"] == "task"}


def log(store, line: str, day, mins, note="", score=None, url=None, piece=None, channel="page") -> str:
    """Log one session on a line. Returns the entry's id."""
    line_id = find_line(store, line)["id"]
    fields = _entry_fields(day, mins, score, url)
    if piece and piece not in _pieces_of(store, line_id):
        raise StudyError("that piece belongs to another line")
    entry = str(uuid7())
    _write(store, "item.create", entry, {"kind": "event", "title": _note(note), "fields": fields}, channel)
    try:
        _write(store, "link.add", {"from": entry, "rel": "on_line", "to": line_id}, {}, channel)
        if piece:
            _write(store, "link.add", {"from": entry, "rel": "relates", "to": piece}, {}, channel)
    except StudyError:
        _write(store, "item.delete", entry, {}, channel)
        raise
    return entry


def _entry(store, entry: str):
    row = store.item(entry)
    if row is None or row["kind"] != "event":
        raise StudyError(f"no entry {entry}")
    return row


def edit(store, entry: str, *, day=None, mins=None, note=None, score=..., url=..., channel="page") -> None:
    """Change an entry. score=None or url=None removes it; leaving them out keeps them."""
    row = _entry(store, entry)
    old = json.loads(row["fields"])
    fields = _entry_fields(day if day is not None else old["day"], mins if mins is not None else old["minutes"],
                           old.get("score") if score is ... else score, old.get("url") if url is ... else url)
    patch_fields = {k: v for k, v in fields.items() if old.get(k) != v}
    for k in ("score", "url"):
        if k in old and k not in fields:
            patch_fields[k] = None
    patch = {}
    if patch_fields:
        patch["fields"] = patch_fields
    if note is not None and _note(note) != row["title"]:
        patch["title"] = _note(note)
    if patch:
        _write(store, "item.update", entry, patch, channel)


def delete(store, entry: str, channel="page") -> None:
    _entry(store, entry)
    _write(store, "item.delete", entry, {}, channel)


def restore(store, entry: str, channel="page") -> None:
    _entry(store, entry)
    _write(store, "item.restore", entry, {}, channel)


# ---------------------------------------------------------------- reading

def entries(store) -> list[dict]:
    on_line = {lk["from_id"]: lk["to_id"] for lk in store.links("on_line")}
    piece_of = {lk["from_id"]: lk["to_id"] for lk in store.links("relates")}
    out = []
    for e in store.items("event"):
        f = e["fields"]
        if f.get("study") is not True or e["id"] not in on_line:
            continue
        out.append({"id": e["id"], "line": on_line[e["id"]], "day": f["day"], "minutes": f["minutes"],
                    "note": e["title"], "score": f.get("score"), "url": f.get("url"),
                    "piece": piece_of.get(e["id"]), "seq": e["created_seq"]})
    out.sort(key=lambda x: (x["day"], x["seq"]), reverse=True)
    return out


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def summary(store, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    monday = week_start(today)
    starts = [monday - dt.timedelta(weeks=n) for n in range(WEEKS_SHOWN - 1, -1, -1)]
    all_entries = entries(store)
    pieces_by_line: dict[str, list] = {}
    for lk in store.links("on_line"):
        item = store.item(lk["from_id"])
        if item is not None and item["kind"] == "task" and not item["deleted"]:
            pieces_by_line.setdefault(lk["to_id"], []).append(item)
    out_lines = []
    for li in lines(store):
        mine = [e for e in all_entries if e["line"] == li["id"]]
        weeks = []
        for s in starts:
            e_ = s + dt.timedelta(days=7)
            weeks.append({"start": s.isoformat(),
                          "minutes": sum(e["minutes"] for e in mine if s.isoformat() <= e["day"] < e_.isoformat())})
        pieces = []
        for p in sorted(pieces_by_line.get(li["id"], []), key=lambda r: r["created_seq"]):
            pieces.append({"id": p["id"], "title": p["title"], "est_min": json.loads(p["fields"]).get("est_min"),
                           "done": p["status"] == "done",
                           "minutes": sum(e["minutes"] for e in mine if e["piece"] == p["id"])})
        week_min = li["fields"].get("week_min", 0)
        this_week = weeks[-1]["minutes"]
        out_lines.append({
            "id": li["id"], "name": li["title"], "week_min": week_min, "checks": li["fields"].get("checks", []),
            "this_week": this_week, "today": sum(e["minutes"] for e in mine if e["day"] == today.isoformat()),
            "done_for_week": week_min > 0 and this_week >= week_min, "weeks": weeks,
            "scores": [{"day": e["day"], "score": e["score"]} for e in reversed(mine) if e["score"] is not None],
            "pieces": pieces, "total": sum(e["minutes"] for e in mine)})
    return {"today": today.isoformat(), "week_start": monday.isoformat(), "lines": out_lines,
            "entries": [{k: v for k, v in e.items() if k != "seq"} for e in all_entries[:200]],
            "entry_count": len(all_entries)}


def csv_text(store) -> str:
    names = {li["id"]: li["title"] for li in lines(store)}
    pieces = {i["id"]: i["title"] for i in store.items("task")}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["day", "line", "minutes", "score", "piece", "note", "link"])
    for e in sorted(entries(store), key=lambda x: (x["day"], x["seq"])):
        note = e["note"]
        if note[:1] in ("=", "+", "-", "@"):  # a spreadsheet would run it as a formula
            note = "'" + note
        w.writerow([e["day"], names.get(e["line"], ""), e["minutes"], "" if e["score"] is None else e["score"],
                    pieces.get(e["piece"], "") if e["piece"] else "", note, e["url"] or ""])
    return buf.getvalue()
