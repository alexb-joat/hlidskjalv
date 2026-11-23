# Lunete architecture (phase 1 core)

Status: draft 2, 23 Nov 2026, written before the code it describes. It folds in the 21 Nov review ([architecture-review.md](architecture-review.md)): findings 1 to 11, the reviewer's answers to the three open questions, and Alex's calls A, B and C (23 Nov: all three as recommended).

Most of this was decided in the work-cockpit plan (19 Nov) and its independent review. Rules marked **proposed** are new in this doc, and Alex may still change them.

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

1. Every change travels in one envelope. Ids are UUIDv7, except connector items, whose ids are derived (section 8).
2. Every store file gets a random id when it is created, and that id goes into every envelope it holds. Nothing is ever called `local`, so joining a team never rewrites history.
3. Two kinds of store: `personal.db` and `org-<id>.db`.
4. Links are typed rows with endpoint rules.
5. Large, personal or sealable content is held by reference (`payload_ref`) and hashed with a salt, so a guess can't be checked against the log and erasure doesn't break it.
6. `class` and `holder_of` are reserved for sealed items.
7. Money is a signed integer with its own scale, corrected only by a reversing entry. Pay entries wait for sealing.
8. Platforms (how a stop is opened) are stored as argv lists, with interpreters and batch files refused.
9. A person's id is kept apart from a device's credentials, and the store sets `actor` from the channel a change came in on.
10. Replay is the migration path: the state view can be rebuilt from the log, and replay never re-judges a change that was accepted.

## 3. Items and links

**Item kinds:** `line`, `task`, `event`, `person`, `agent`, `run`, `commit`, `doc`, `ledger`, `account`, `budget` and `fund`.

**Every item has these fields:**

| Field | Meaning |
|---|---|
| `id` | UUIDv7, global; for connector items the derived id of section 8 |
| `kind` | one of the kinds above |
| `title` | short text; for `person` items it is empty in the log and the name lives in the payload (section 4, erasure) |
| `status` | free text per kind (e.g. `open`, `done`); changes only by compare-and-set |
| `fields` | a JSON object, plain fields, no floats |
| `source`, `external_id` | where it came from: `typed`, or a connector name plus its own id. (source, external_id) is unique when external_id is set |
| `class` | `normal`; `sealed` is reserved (refused in phase 1) |
| `payload_ref` | a pointer to content kept outside the log, or null |
| `deleted` | soft delete; a deleted item can be restored |
| `ignored` | set when a person deletes a connector item; later observations of it are dropped (section 8) |

**Kinds with extra rules:**
- `agent`: `item.create` must carry `sponsor`, the id of a person in the same store who isn't deleted. An agent has exactly one sponsor. It changes only through `agent.sponsor` (section 4), and the view mirrors it as a `sponsored_by` link. A person who sponsors an agent that isn't deleted can't be deleted: "sponsors agent X; move the sponsorship first".
- `account`: where money sits (a bank account, a card, an API balance). It has a `currency` from the table in section 5 and never stores a balance: the balance is the sum of the ledger entries that name it.
- `ledger`: made only by `ledger.add` and `ledger.reverse` (section 5).

**Link relations, with their endpoint rules (proposed):**

| Relation | From | To | Extra rule |
|---|---|---|---|
| `on_line` | task, event, run, commit, doc, agent | line | |
| `part_of` | task / line | task / line (same kind) | no cycles |
| `depends_on` | task | task, event | no cycles |
| `assigned_to` | line, task | person, agent | |
| `produced_by` | commit, doc, run | person, agent, run | |
| `spawned_by` | run | run, agent | |
| `booked_to` | ledger | line, task, run, budget | set only by `ledger.add`; `link.add` and `link.remove` refuse it |
| `funds` | fund | line, budget | |
| `holder_of` | person | any sealed item | reserved, refused in phase 1 |
| `sponsored_by` | agent | person | mirrored from `sponsor`; `link.add` and `link.remove` refuse it |
| `relates` | any | any | the fallback |

`booked_to` replaces the earlier `spent_on`: the sign of the amount already says whether money went out or came in, so one relation covers both.

**Rules that apply to every link:**
- Both ends must exist in this store and must not be deleted, except for the cross-store case below.
- A link is unique per (from, relation, to).
- A cycle check walks `part_of` and `depends_on` from the new link's `to` end. A link that would close a loop is refused: "would make a loop through X".
- Removing a link that doesn't exist changes nothing and is logged as a refusal: "no such link".
- A link a person removes stays as a tombstone row with who removed it. A connector can't bring it back (section 8); a person can, with a new `link.add`.

**Cross-store links:**
- An org item may not link to a personal item.
- A personal item may link to an org item. That link lives in `personal.db` and its patch names the org (`to_org`).
- The personal store doesn't check that the org item exists, because it doesn't hold it. The screen shows such a link as "unavailable" until the org store holds the item.
- An org store refuses any id it doesn't hold.

## 4. The change envelope

Each change is one JSON object.

| Field | Set by | Meaning |
|---|---|---|
| `v` | client | envelope version, 1 |
| `op_id` | client | UUIDv7; see "Duplicates" below |
| `org`, `space` | client | the store's random org id and `personal` or `org`; a change whose pair doesn't match the store's `meta` is refused |
| `device`, `device_seq` | client | which device, and its own counter |
| `actor` | **store** | `{id, kind: human, agent or connector, on_behalf_of?, run_id?}`, set from the channel (below), never trusted from the client |
| `hlc` | client | hybrid logical clock stamp, for display order and times only (client clocks never decide a conflict) |
| `base_seq` | client | the last `seq` this device had seen |
| `kind` | client | the op kind (below) |
| `target` | client | item id, or for links `{from, rel, to}` |
| `patch` | client | what changes |
| `reason` | client | optional text: why |
| `payload_hash` | client | `sha256(canon({kind, target, patch}))`; the store recomputes it and refuses a mismatch |
| `preview_hash` | client | reserved for B4; the hash of the preview a human approved |
| `seq`, `epoch`, `rules_v`, `prev_hash`, `hash`, `auth_ref` | store / server | order, restore epoch, the rules version the change was accepted under, hash chain, which grant allowed it |

In phase 1 the local store plays the server's part. It numbers changes, chains their hashes, sets `epoch` to 1 and `auth_ref` to `local`.

**Canonical JSON (`canon`):** keys sorted, no spaces (`,` and `:`), UTF-8 without escaping non-ASCII, strings exactly as given (no Unicode normalisation). Integers must fit in 64 bits. Floats, NaN and infinities are refused anywhere in an envelope; a fractional number travels as a decimal string (`"1.0825"`).

**Hashes:**
- `payload_hash = sha256(canon({kind, target, patch}))`, so a link change binds its target too.
- `hash = sha256(prev_hash ‖ "\n" ‖ canon(envelope without hash))`, with hex strings. The first change in a store uses 64 zeros as `prev_hash`.
- Order is `seq` alone. `hlc` never orders anything in the store.

**Content behind `payload_ref` (personal, sealable or large):**
- It is kept in a `payloads` table, outside the log: `(ref, salt, content)`.
- The log holds only `payload_ref` and `payload_salted_hash = sha256(salt ‖ content)`, with a random 16-byte salt per payload.
- A guess at a name can't be checked against the log, because the salt isn't in the log.
- **Erasure** deletes the content and its salt. The log and its chain still verify, because they never covered the content.
  - The store runs with `secure_delete` on and checkpoints the WAL after an erasure, so the old pages don't linger in the file.
- Phase 1 puts a person's name and contact details there. Free text a person types elsewhere (a task title that names someone) is in the log and isn't erasable; the docs will say so.

**Where `actor` comes from (Alex's call B):**
- The store derives `actor` from the channel that delivered the change: the CLI and the paired browser are the human who owns this install, and a connector's adapter is that connector.
- A client-supplied `actor` that doesn't match the channel is refused: "actor doesn't match the channel".
- A write from an agent is refused outright in phase 1: "agent writes wait for B4".
- Proposals, approvals and grants have no op yet. They come with B4 (agents as workers), together with `preview_hash`. `on_behalf_of` and `run_id` are reserved until then.

**Op kinds (proposed):**

| Kind | Patch | Rule |
|---|---|---|
| `item.create` | the new item's fields | id must be new; `class` must be `normal`; an `agent` needs `sponsor` |
| `item.update` | `{fields?: {...}, title?}` | `fields` is a shallow merge: a key set to `null` is removed, other keys are replaced whole. `title` is replaced. The later `seq` wins. Refused on `ledger` items |
| `item.status` | `{from, to}` | compare-and-set: turned down if the current status isn't `from` |
| `item.delete` / `item.restore` | `{}` | an edit to a deleted item is turned down: "deleted". Refused on `ledger` items |
| `link.add` / `link.remove` | `{}`, or `{to_org}` for a cross-store link | endpoint rules, cycle check, tombstones |
| `agent.sponsor` | `{from, to}` | compare-and-set on the sponsor; `to` must be a person who isn't deleted |
| `ledger.add` | section 5 | creates a `ledger` item; entries are never edited |
| `ledger.reverse` | `{reverses: <entry id>, reason}` | a new entry with the amount negated and the same `booked_to` links; one reversal per entry |
| `refusal` | `{refused_op_id, kind, target, reason}` | written by the store only (below) |

**Duplicates:**
- An `op_id` the store already holds, with the same `payload_hash`, `kind` and `target`, is acknowledged with its original `seq` and isn't applied again.
- The same `op_id` with anything different is refused: "op_id reused".

**A turned-down change** is never applied. The store appends a `refusal` op instead, so the log is the one record and the *Needs you* lane can show it later.
- The refusal carries the refused change's `op_id`, kind, target, actor and reason, and **not** its patch or `payload_hash`. Otherwise a refused pay entry or a refused name would sit in the log for good.
- A refusal is itself an op: it has a `seq`, it is chained, and replay skips it.

**Times:** anything the view records with a time (`created_at`, `updated_at`, `deleted_at`) takes it from the change's `hlc`. Applying a change never reads the wall clock, so a replay gives the same view.

**History:** `item.update` doesn't carry previous values. The view keeps `field_prov(item, field, seq, actor_kind, actor_id)`: who last wrote each field, and in which change. "Overwritten by X" comes from it plus an indexed scan of `ops` by target, and undo works out the inverse from the log.

## 5. Money

**Amount:**
- `amount` is a signed integer, `scale` is how many decimal places it has: the value is `amount × 10^-scale`.
  - **Sign:** a negative amount is money going out (a cost), a positive amount is money coming in. Zero is refused.
  - **Scale:** at least the currency's minor-unit exponent and at most 9. AI spend uses scale 6 (millionths), so a $0.0004 call is `-400` at scale 6, not 0 cents.
- `currency` must be in a fixed table, with its ISO 4217 minor-unit exponent: `EUR` 2, `USD` 2, `RSD` 2. A new code is added to the table in code, with a `rules_v` bump.

**Other fields on `ledger.add`:**

| Field | Meaning |
|---|---|
| `category` | `ai`, `tool`, `service`, `income` or `other`; `pay` is reserved and refused (below) |
| `estimated` | true or false |
| `origin` | `typed`, `csv`, `trioditis` or a connector name |
| `account` | optional: an `account` item in this store with the same currency |
| `booked_to` | optional list of ids (line, task, run, budget); becomes `booked_to` links that never change |
| `orig`, `rate` | for a conversion: `orig: {amount, scale, currency}` as it was paid, and `rate: {value, source, date}` with `value` as a decimal string |

**Corrections:** an `item.update` or `item.delete` on a ledger entry is turned down ("ledger entries can't be edited"). A reversal is the only correction, and it copies the entry's `booked_to` links.

**Pay entries (Alex's call A):** an entry for a person's pay (salary, a contractor's fee, a rate) is refused until sealing exists: "pay entries wait for sealing". The log is never rewritten, so a pay amount written in plaintext could never be sealed later.
- The check sees `category: pay` only. An amount typed under another category can't be told apart, and the entry form will say so next to the field.

This is a tracker, not accounting software.

## 6. Platforms (how a stop opens)

- A stop may carry `fields.platforms`, a list of `{os, argv}` entries.
  - `os` is `linux`, `macos` or `windows`.
  - `argv` is a list of 1 to 64 strings: the program and its arguments, never a shell line. No string is empty or holds a NUL or a line break.
- Phase 1 stores and checks them. Nothing runs from the graph.

**Refused at validation time (Alex's call C).** The program name is `argv[0]`'s last path part, lower-cased, with `.exe` dropped.

| Refused | Why |
|---|---|
| Shells and script hosts, with any arguments: `sh`, `bash`, `dash`, `zsh`, `ksh`, `fish`, `csh`, `tcsh`, `cmd`, `powershell`, `pwsh`, `wscript`, `cscript`, `mshta` | they run a line of code |
| Wrappers that start another program: `env`, `sudo`, `doas`, `su`, `pkexec`, `runas`, `nohup`, `setsid`, `timeout`, `nice`, `xargs`, `start` | they would hide what really runs |
| Interpreters given inline code: `python`/`python3`/`python3.N` with `-c`; `node` with `-e`, `-p`, `--eval` or `--print`; `perl` with `-e` or `-E`; `ruby` with `-e`; `php` with `-r`; `lua` with `-e`; `osascript` with `-e`; `deno eval`; `awk` and `gawk` with any arguments | the code sits in the argv itself |
| Terminals told to run a command: `konsole`, `xterm` or `alacritty` with `-e`; `gnome-terminal` with `-e`, `-x` or `--` | the same, one step removed |
| `git` with `-c`, `--config-env` or `--exec-path` | a config value can name a program to run (`core.sshCommand`) |
| On Windows, a program ending in `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.wsf` or `.hta` | batch files re-parse their arguments, so a value can inject a command |

- A flag counts when it appears as its own argument or inside a short-flag cluster (`-Ic` has `-c`), anywhere in the argv. That turns down some harmless lines, such as `python tool.py -c 3`; this is on purpose.
- The list is a first filter, not the safeguard: it can't name every program that runs code. The safeguard comes before anything runs (a later step): each machine approves an exact (item, os, argv, cwd) once, and the preview shows the argv as it will run.

## 7. Stores on disk

**Files:** `~/.lunete/graph/personal.db` and one `~/.lunete/graph/org-<id>.db` per org, using SQLite through the stdlib `sqlite3`.
- The folder is owner-only (0700).
- `<id>` is the org's random id, made when the file is created. Phase 1 creates one org file; joining a team later creates a new `org-<team id>.db` and never adopts an existing one.
- `personal.db` also gets a random org id, which is never shared; it is the namespace for derived ids (section 8).

**Tables in each file:**
- `ops`: the log, with `seq`, `op_id` unique, `target` indexed, the envelope JSON and `hash`;
- the view tables: `items`, `links` (with tombstones) and `field_prov`;
- `payloads`: content behind `payload_ref`, with its salt;
- `meta`: schema version, `rules_v`, org id, space, epoch and `applied_seq`.

**Writing (one writer at a time):**
- WAL mode, `synchronous=FULL`, `secure_delete` on, and a `busy_timeout` of 5 seconds.
- Each change is one `BEGIN IMMEDIATE` transaction: read the head, append the op, update the view and `field_prov`, and set `meta.applied_seq`. A second process waits for the first rather than interleaving with it.
- **On open:** the store checks that `meta.applied_seq` equals the highest `seq`. If not, it opens read-only and the error names the file, both numbers and `lunete graph check`.
- **The HLC on open:** the device clock is seeded from the highest `hlc` in the file. A change whose stamp is more than 10 minutes ahead of the store's clock is refused: "clock ahead".

**Replay:**
- The view tables can be dropped and rebuilt by applying `ops` in `seq` order.
- Replay applies each accepted change without judging it again, and skips `refusal` ops. A change accepted under older rules stays accepted, and `rules_v` on each change says which rules those were.
- `lunete graph check` replays in memory and compares the result with the view. It also verifies the hash chain and every `payload_hash`, and it counts (never refuses) old changes that today's rules would turn down.
- A schema change is a new replay, not an in-place migration.

**Keeping the two kinds apart:** the code that opens `personal.db` lives in a module the sync code doesn't import, and a test fails if it ever does. So the sync code can't open `personal.db` by accident.

## 8. Connectors (proposed protocol)

A connector reads one source and never writes to it.

**What a connector provides:**
- a `name`;
- `poll(cursor) -> (observations, new_cursor)`;
- `health() -> {ok, line}`, the line its source card shows;
- an observation: `{external_id, kind, title, fields, links}`.

**Where its items go:** each source instance is bound to one space in its config, `personal` by default. An org space has to be chosen on purpose.

**Ids:** a connector item's id is `UUIDv5(org id, source + "\n" + external_id)`. Two devices that see the same commit make the same id, so the org store holds it once.
- Git's `external_id` is the normalised remote URL plus the full sha. Normalising lower-cases the host, drops credentials and a trailing `.git`, and turns `git@host:path` into `host/path`.

**How the app turns observations into changes:**
- An observation becomes an `item.create` or `item.update` with `actor.kind = connector` and `actor.id = <name>`.
- An unchanged observation makes no change.
- **A person's edits win.** Before writing, the adapter reads `field_prov` and leaves out every field a person wrote last; the skip is counted in `health`. As a backstop the store refuses a connector write to a field a person wrote last.
- **A link a person removed** stays removed: the adapter skips links that have a tombstone, and the store refuses them from a connector.
- **An item a person deleted** is marked `ignored`. The adapter drops later observations of it and counts them in `health`, so it doesn't make a refusal on every poll.
- Cursors live in the connector's own state file, not in the log.

**The first connectors:** git commits, Claude Code runs, plan files, ICS, GitHub issues and Trioditis spend. Today's `sources.py` watchers are wrapped one at a time, and the old `poll()` runs until each is wrapped.

## 9. Sync and permissions (phase 3; the fields exist now)

**Transport:**
- The browser talks only to the local app.
- The app talks to the team server through `POST /ops` (in batches) and `GET /ops?after=seq`, with SSE only as a wake-up.

**The server orders and authorises.** It numbers each change, sets `actor` from the token that sent it, and checks it against the grants in force at that number.

**Roles:**

| Role | May |
|---|---|
| Owner | members, org settings, money accounts, sealing |
| Line manager | edit the line, assign, approve agent actions, the line's budget |
| Member | create and edit items, move own stops, comment |
| Agent | only what its sponsor granted (area, verbs, expiry, spend cap); risky actions wait for a human. Grants and approvals come with B4 |

**Failure cases:**

| Case | How it resolves |
|---|---|
| A role is revoked while the member is offline | Their changes are turned down "role revoked at #N" and land in their *Needs you* lane. A manager may adopt them. |
| An edit to a deleted item | Turned down "deleted". The item stays restorable. |
| A member leaves | Tokens revoked, authorship kept, their agents' grants lapse. Erasure deletes their name from `payloads`; the log still verifies. |
| The server is restored from a backup | A new epoch starts. Clients resubmit; duplicates are acknowledged by `op_id` and each change is authorised again. |

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
| `ids.py` (UUIDv7, UUIDv5 for connector ids, HLC with the drift cap) | `/api/ops` and any route that writes the graph (B1) |
| `graph.py` (stores, canonical JSON, hashes, envelope checks, view, `field_prov`, payloads, replay) | the Today view and the stop panel (after the ten stills) |
| `ops.py` (apply, duplicates, compare-and-set, cycle check, ledger and argv rules, refusals as ops) | connectors (B2) |
| `lunete graph check` (read-only) | the team server, grants and sync (B3) |
| `/shell` with the look settings | proposals, approvals, grants and agent writes (B4); sealing, pay entries and the `[team]` extra |

## 13. Settled questions

The three open questions of draft 1, as the review answered them:

1. **The sponsor** is a required field on agent creation, checked to be a live person, changed through `agent.sponsor` and mirrored as a link (section 3).
2. **"Overwritten by X"** comes from `field_prov` and the log; `item.update` carries no previous values (section 4).
3. **Space** isn't a column per row. Each file holds one (org, space) pair in `meta`, and a change that doesn't match is refused (section 4).

No question is open in draft 2.
