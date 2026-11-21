# Lunete architecture (phase 1 core)

Status: draft, 21 Nov 2026, written before the code it describes.

Most of this was decided in the work-cockpit plan (19 Nov) and its independent review. Rules marked **proposed** are new in this doc, and the review and Alex may change them.

## 1. What Lunete is built to hold

Lunete is a work cockpit for one person first, then for small teams of 3 to 15 people. It holds:

- the work, as a graph of items and links;
- who did it, human or AI;
- the money around it, tracked and never moved.

The map on screen is drawn from that graph:
- a stop is an item with an `on_line` link;
- an interchange is an item on two or more lines.

Two streams are kept apart:

| Stream | Examples | Where it lives |
|---|---|---|
| Telemetry | file changes, commands, branch moves | stays local, in the code desk's session files (today's `sessions/*.jsonl`); never in the change log |
| Facts | a commit, an agent run, a task move, a ledger entry | changes in the log (this doc) |

Without that split, a 15-person org would log millions of changes.

## 2. Fixed before phase 1 (costly to change later)

1. Every change travels in one envelope, and ids are UUIDv7.
2. Two stores: `personal.db` and `org-<id>.db`.
3. Links are typed rows with endpoint rules.
4. Large content is held by reference (`payload_ref`, `payload_hash`).
5. `class` and `holder_of` are reserved for sealed items.
6. Money is whole minor units, corrected only by a reversing entry.
7. Platforms (how a stop is opened) are stored as argv lists.
8. A person's id is kept apart from a device's credentials.
9. Replay is the migration path: the state view can be rebuilt from the log.

## 3. Items and links

**Item kinds:** `line`, `task`, `event`, `person`, `agent`, `run`, `commit`, `doc`, `ledger`, `account`, `budget` and `fund`.

**Every item has these fields:**

| Field | Meaning |
|---|---|
| `id` | UUIDv7, global |
| `kind` | one of the kinds above |
| `title` | short text |
| `status` | free text per kind (e.g. `open`, `done`); changes only by compare-and-set |
| `fields` | a JSON object, plain fields |
| `source`, `external_id` | where it came from: `typed`, or a connector name plus its own id. (source, external_id) is unique when external_id is set |
| `class` | `normal`; `sealed` is reserved (refused in phase 1) |
| `payload_ref` | a pointer to large content kept outside the log, or null |
| `deleted` | soft delete; a deleted item can be restored |

**Link relations, with their endpoint rules (proposed):**

| Relation | From | To |
|---|---|---|
| `on_line` | task, event, run, commit, doc, agent | line |
| `part_of` | task / line | task / line (same kind) |
| `depends_on` | task | task, event |
| `assigned_to` | line, task | person, agent |
| `produced_by` | commit, doc, run | person, agent, run |
| `spawned_by` | run | run, agent |
| `spent_on` | ledger | line, task, run, budget |
| `funds` | fund | line, budget |
| `holder_of` | person | any sealed item (reserved, refused in phase 1) |
| `sponsored_by` | agent | person (**proposed** addition: "every agent has a named human sponsor") |
| `relates` | any | any (the fallback) |

**Rules that apply to every link:**
- Both ends must exist and must not be deleted.
- A link is unique per (from, relation, to).
- An org item may not link to a personal item. A personal item may link to an org item; that link lives in `personal.db`.

## 4. The change envelope

Each change is one JSON object.

| Field | Set by | Meaning |
|---|---|---|
| `v` | client | envelope version, 1 |
| `op_id` | client | UUIDv7; the same `op_id` twice is applied once |
| `org`, `space` | client | `local` and `personal` or `org` in phase 1 |
| `device`, `device_seq` | client | which device, and its own counter |
| `actor` | client | `{id, kind: human, agent or connector, on_behalf_of?, run_id?}` |
| `hlc` | client | hybrid logical clock stamp, for display order only (client clocks never decide a conflict) |
| `base_seq` | client | the last `seq` this device had seen |
| `kind` | client | the op kind (below) |
| `target` | client | item id, or for links `{from, rel, to}` |
| `patch` | client | what changes |
| `reason` | client | optional text: why |
| `payload_hash` | client | sha256 of the canonical patch (sorted keys, compact, UTF-8) |
| `preview_hash` | client | optional; the hash of the preview a human approved |
| `seq`, `epoch`, `prev_hash`, `hash`, `auth_ref` | store / server | order, restore epoch, hash chain, which grant allowed it |

In phase 1 the local store plays the server's part. It numbers changes, chains their hashes, sets `epoch` to 1 and `auth_ref` to `local`.

**Op kinds (proposed):**

| Kind | Patch | Rule |
|---|---|---|
| `item.create` | the new item's fields | id must be new; `class` must be `normal` |
| `item.update` | `{fields: {...}, title?}` | plain fields: the later `seq` wins, and history shows what was overwritten |
| `item.status` | `{from, to}` | compare-and-set: turned down if the current status isn't `from` |
| `item.delete` / `item.restore` | `{}` | an edit to a deleted item is turned down: "deleted" |
| `link.add` / `link.remove` | `{}` (target carries the link) | endpoint rules |
| `ledger.add` | `{amount_minor, currency, estimated, origin, rate?}` | creates a `ledger` item; entries are never edited |
| `ledger.reverse` | `{reverses: <entry id>, reason}` | a new entry with the opposite amount; one reversal per entry |

**A turned-down change** is never applied. The store records it in a `refused` table with its reason (`stale status`, `deleted`, `ledger entries can't be edited`, …), so the *Needs you* lane can show it later.

## 5. Money

- An amount is an integer count of minor units (cents, paras) plus an ISO 4217 code. `EUR`, `RSD` and `USD` are expected, and any three capital letters are accepted.
- Each entry carries `estimated` (true or false) and `origin`: `typed`, `csv`, `trioditis` or a connector name.
- A conversion records `rate: {value, source, date}`.
- An `item.update` or `item.delete` on a ledger entry is turned down; a reversal is the only correction.
- This is a tracker, not accounting software.

## 6. Platforms (how a stop opens)

- A stop may carry `fields.platforms`, a list of `{os, argv}` entries. `argv` is a list of strings: the program and its arguments, never a shell line.
- Phase 1 stores and checks the shape only. Nothing runs from the graph.
- Later, each machine approves an argv locally per (author, argv hash), and the preview shows the exact argv.

## 7. Stores on disk

- **Files:** `~/.lunete/graph/personal.db` and `~/.lunete/graph/org-local.db`, using SQLite through the stdlib `sqlite3`.
  - The folder is owner-only (0700).
  - WAL mode, one writer per file.
  - The sync code never opens `personal.db`.
- **Tables in each file:**
  - `ops`: the log, with `seq`, `op_id` unique, the envelope JSON and `hash`;
  - `refused`;
  - the view tables `items` and `links`;
  - `meta`: schema version and epoch.
- **Replay:** the view tables can be dropped and rebuilt by applying `ops` in `seq` order. `lunete graph check` does that in memory and compares.
  - A schema change is a new replay, not an in-place migration.

## 8. Connectors (proposed protocol)

A connector reads one source and never writes to it.

**What a connector provides:**
- a `name`;
- `poll(cursor) -> (observations, new_cursor)`;
- `health() -> {ok, line}`, the line its source card shows;
- an observation: `{external_id, kind, title, fields, links}`.

**How the app turns observations into changes:**
- An observation becomes an `item.create` or `item.update` with `actor.kind = connector` and `actor.id = <name>`.
- An unchanged observation makes no change.
- A connector owns only the fields it set. A re-sync never overwrites a field a person edited: that field is skipped, and the skip is counted in `health`.
- Cursors live in the connector's own state file, not in the log.

**The first connectors:** git commits (full sha plus repo as `external_id`), Claude Code runs, plan files, ICS, GitHub issues and Trioditis spend. Today's `sources.py` watchers are wrapped one at a time, and the old `poll()` runs until each is wrapped.

## 9. Sync and permissions (phase 3; the fields exist now)

**Transport:**
- The browser talks only to the local app.
- The app talks to the team server through `POST /ops` (in batches) and `GET /ops?after=seq`, with SSE only as a wake-up.

**The server orders and authorises.** It numbers each change and checks it against the grants in force at that number.

**Roles:**

| Role | May |
|---|---|
| Owner | members, org settings, money accounts, sealing |
| Line manager | edit the line, assign, approve agent actions, the line's budget |
| Member | create and edit items, move own stops, comment |
| Agent | only what its sponsor granted (area, verbs, expiry, spend cap); risky actions wait for a human |

**Failure cases:**

| Case | How it resolves |
|---|---|
| A role is revoked while the member is offline | Their changes are turned down "role revoked at #N" and land in their *Needs you* lane. A manager may adopt them. |
| An edit to a deleted item | Turned down "deleted". The item stays restorable. |
| A member leaves | Tokens revoked, authorship kept, their agents' grants lapse. Erasure blanks the name without breaking the log. |
| The server is restored from a backup | A new epoch starts. Clients resubmit; duplicates are dropped by `op_id` and each change is authorised again. |

**Tamper evidence:** each client keeps `(seq, head_hash)` and checks it on every pull.

## 10. Dependencies and safety

- Client and server use the standard library only. That is enough for 3 to 15 users.
- TLS comes from Caddy or Tailscale in front, and the docs will say how.
- An optional `[team]` extra adds `cryptography` for sealed content and signatures.
- The pure-Python ChaCha20 in `sync.py` stays limited to settings blobs.
- The cockpit's `bash -lc` runner is not ported. A teammate's or an agent's stop must never run code on your machine.

## 11. Front end

- The new shell is native ES modules under `lunete/static/shell/`, with no build step, served through an allowlisted module route next to `SCRIPTS`. The CSP is unchanged.
- Today's page stays at `/` until B1, then moves to `/desk` as the code desk. An iframe is not an option, because the CSP sets `frame-ancestors 'none'`.
- The look comes from design round 1: six palettes plus your own hue, light, dark or system, and four gradient amounts. Lane colours differ in lightness and stay at least 12 apart (OKLab × 100) under the three simulated colour-vision types.

## 12. What the skeleton branch builds, and what waits

| Built now (`build/core`) | Waits |
|---|---|
| `ids.py` (UUIDv7, HLC) | `/api/ops` and any route that writes the graph (B1) |
| `graph.py` (stores, envelope checks, view, replay) | the Today view and the stop panel (after the ten stills) |
| `ops.py` (apply, compare-and-set, ledger rules, refused table) | connectors (B2) |
| `lunete graph check` (read-only) | the team server, grants and sync (B3) |
| `/shell` with the look settings | sealing and the `[team]` extra |

## 13. Open questions

1. `sponsored_by` as a link, or a required field on agent items?
2. Should `item.update` carry the previous values, so the history can say "overwritten by X" without a replay?
3. Is the view keyed by space as well as by file? For now each file holds one space.
