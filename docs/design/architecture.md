# Lunete architecture (phase 1 core)

Status: draft 3, 23 Nov 2026, written before the code it describes.
- Draft 2 folded in the 21 Nov review ([architecture-review.md](architecture-review.md)) and Alex's calls A, B and C (23 Nov: all three as recommended).
- Draft 3 folds in a second independent scan ([architecture-review-2.md](architecture-review-2.md)).

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
5. Personal or sealable content is held by reference (`payload_ref`), outside the log. The store hashes it with a salt, so a guess can't be checked against the log, and erasure doesn't break the chain.
6. `class` and `holder_of` are reserved for sealed items.
7. Money is a signed integer with its own scale, corrected only by a reversing entry. Pay entries wait for sealing.
8. Platforms (how a stop is opened) are stored as argv lists, with interpreters and batch files refused.
9. A person's id is kept apart from a device's credentials, and the store sets `actor` from the channel a change came in on.
10. Replay is the migration path: the state view can be rebuilt from the log. Replay never re-judges a change that was accepted, and the apply rules of each rules version are frozen.

## 3. Items and links

**Item kinds:** `line`, `task`, `event`, `person`, `agent`, `run`, `commit`, `doc`, `ledger`, `account`, `budget` and `fund`.

**Every item has these fields:**

| Field | Meaning |
|---|---|
| `id` | UUIDv7, global; for connector items the derived id of section 8 |
| `kind` | one of the kinds above |
| `title` | short text, at most 200 characters; always empty on `person` items (below) |
| `status` | free text per kind (e.g. `open`, `done`); changes only by compare-and-set |
| `fields` | a JSON object, plain fields, no floats |
| `source`, `external_id` | where it came from: `typed`, or a connector name plus its own id. (source, external_id) is unique when external_id is set |
| `class` | set by the store: `normal`; `sealed` is reserved |
| `payload_ref` | a pointer to content kept outside the log, or null |
| `deleted` | soft delete; a deleted item can be restored |

**Kinds with extra rules:**
- `person`: `title` is always empty and `fields` holds nothing. The name and contact details live only in the payload (section 4), so they can be erased.
- `agent`: `item.create` must carry `sponsor`, the id of a person in the same store who isn't deleted. An agent has exactly one sponsor. It changes only through `agent.sponsor` (section 4), and the view mirrors it as a `sponsored_by` link. A person who sponsors an agent that isn't deleted can't be deleted: "sponsors agent X; move the sponsorship first".
- `account`: where money sits (a bank account, a card, an API balance). Its `currency`, from the table in section 5, is set when it is created and never changes. It stores no balance: the balance is the sum of the ledger entries that name it.
- `ledger`: made only by `ledger.add` and `ledger.reverse` (section 5); `item.create` refuses the kind.

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
- On `link.add`, both ends must exist in this store and must not be deleted, except for the cross-store case below.
- A link from an item to itself is refused.
- A link is unique per (from, relation, to).
- **Cycles:** the check walks one relation at a time (`part_of` alone, then `depends_on` alone), over live links only, from the new link's `to` end. A link that would close a loop is refused: "would make a loop through X".
- `link.remove` works even when an end is deleted, so stale links can be cleaned up. Removing a link that doesn't exist changes nothing and is logged as a refusal: "no such link".
- **Tombstones:** a link a person removes stays as a tombstone row with who removed it. A connector can't bring it back (section 8); a person can, with a new `link.add`. A connector may remove only links it added itself.

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
| `hlc` | client | hybrid logical clock stamp, for display order and times only (client clocks never decide a conflict) |
| `base_seq` | client | the last `seq` this device had seen |
| `kind` | client | the op kind (below) |
| `target` | client | item id, or for links `{from, rel, to}` |
| `patch` | client | what changes |
| `reason` | client | optional text: why |
| `payload_hash` | client | `sha256(canon({kind, target, patch}))`; the store recomputes it and refuses a mismatch |
| `preview_hash` | client | reserved for B4; the hash of the preview a human approved |
| `actor` | **store** | `{id, kind: human, agent, connector or store, on_behalf_of?, run_id?}`, set from the channel (below), never taken from the client |
| `payload_salted_hash` | **store** | on a change that sets a payload: `sha256(salt ‖ content)` (below) |
| `seq`, `epoch`, `rules_v`, `prev_hash`, `hash`, `auth_ref` | store / server | order, restore epoch, the rules version the change was accepted under, hash chain, which grant allowed it |

In phase 1 the local store plays the server's part. It numbers changes, chains their hashes, sets `epoch` to 1 and `auth_ref` to `local`.

**Absent and null:** an optional field that isn't used is left out, never sent as `null`. `null` is allowed only as a value inside `patch.fields`, where it removes a key.

**Canonical JSON (`canon`):** keys sorted, no spaces (`,` and `:`), UTF-8 without escaping non-ASCII, strings exactly as given (no Unicode normalisation).
- **Parsing is strict.** A change is refused if it has a duplicate key, `NaN` or `Infinity`, a float anywhere, a lone surrogate in a string, `true` or `false` where a number is expected, or an integer outside signed 64 bits. A fractional number travels as a decimal string (`"1.0825"`).
- **Limits:** the canonical envelope is at most 64 KB and nests at most 8 levels deep.
- **Patch keys:** each op kind allows only the keys in its row below. Any other key, or a field the store sets (`class`, `deleted`, `actor`, `payload_salted_hash`), is refused: "unknown key X".

**Hashes:**
- `payload_hash = sha256(canon({kind, target, patch}))` as lowercase hex, so a link change binds its target too.
- The store keeps the exact canonical text of each change and never re-serialises it.
- `hash = sha256(ascii(prev_hash) + b"\n" + utf8(canon(envelope without hash)))` as lowercase hex, where `prev_hash` is the previous change's 64 hex characters. The first change in a store uses 64 zeros.
- `lunete graph check` hashes the stored text.
- Order is `seq` alone. `hlc` never orders anything in the store.

**Payloads (personal or sealable content):**
- **Sending one:** the patch carries only `payload_ref`, a UUIDv7 the client makes. The content travels beside the envelope, not inside it, so `payload_hash` never covers it.
- **Storing it:** the store keeps it in a `payloads` table, outside the log, as `(ref, salt, content)`.
  - The salt is 16 random bytes per payload.
  - The store adds `payload_salted_hash = sha256(salt ‖ utf8(content))` to the envelope as lowercase hex, so the chain covers it.
  - A guess at a name can't be checked against the log, because the salt isn't in the log.
- **Changing it:** `payload.set` replaces the content under a new ref; renaming a person works this way.
- **Erasing it:** `payload.erase` deletes the content and its salt, and keeps `(ref, erased at seq)`. The log and its chain still verify, because they never covered the content, and `graph check` tells an erased payload from a lost one.
  - The store runs with `secure_delete` on and checkpoints the WAL after an erasure, so the old pages don't stay in the file.
  - Backups and copies made earlier still hold the old bytes; the docs will say so.
- **What goes there in phase 1:** a person's name and contact details. Free text a person types elsewhere (a task title that names someone) is in the log and isn't erasable; the docs will say so.

**Where `actor` comes from (Alex's call B):**
- The store derives `actor` from the channel that delivered the change. The CLI and the paired browser are the human who owns this install, and a connector's adapter is that connector. The apply function takes the channel as an argument.
- A write from an agent is refused outright in phase 1: "agent writes wait for B4".
- Proposals, approvals and grants have no op yet. They come with B4 (agents as workers), together with `preview_hash`. `on_behalf_of` and `run_id` are reserved until then.

**Op kinds (proposed).** The patch column lists every key allowed.

| Kind | Patch | Rule |
|---|---|---|
| `item.create` | `kind`, `title`, `status?`, `fields?`, `source?`, `external_id?`, `payload_ref?`, `sponsor?` | the target id must be new. A typed item's id must be a UUIDv7; a connector item's id must equal its derived id (section 8). `ledger` is refused; `agent` needs `sponsor` |
| `item.update` | `fields?`, `title?` | `fields` is a shallow merge: a key set to `null` is removed, other keys are replaced whole. `title` is replaced. The later `seq` wins. Refused on `ledger` items, on a `person`'s title and fields, and on an `account`'s `currency` |
| `item.status` | `from`, `to` | compare-and-set: turned down if the current status isn't `from` |
| `item.delete` / `item.restore` | none | an edit to a deleted item is turned down: "deleted". Refused on `ledger` items |
| `link.add` | `to_org?` | endpoint rules, cycle check, tombstones; `to_org` only for a cross-store link |
| `link.remove` | none | works when an end is deleted |
| `agent.sponsor` | `from`, `to` | compare-and-set on the sponsor; `to` must be a person who isn't deleted |
| `payload.set` | `payload_ref` | replaces the item's payload; the content travels beside |
| `payload.erase` | `payload_ref` | deletes the content and salt (above) |
| `ledger.add` | section 5 | creates a `ledger` item; entries are never edited |
| `ledger.reverse` | `reverses`, `reason` | section 5 |
| `refusal` | `refused_op_id`, `kind?`, `target?`, `refused_actor`, `reason` | written by the store only (below) |

**Duplicates:**
- The store keeps `client_hash = sha256(canon(the client-set fields))` for every accepted or refused change.
- A change whose `op_id` the store already holds, with the same `client_hash`, gets the original answer: the accepted change's `seq`, or the refusal's. Nothing new is written.
- The same `op_id` with a different `client_hash` gets "op_id reused" and is not logged, since the id already belongs to a change; the store counts it in `meta`.

**A turned-down change** is never applied. The store appends a `refusal` op instead, so the log is the one record and the *Needs you* lane can show it later.
- **What it records:** the refused change's `op_id`, kind, target, actor and reason. It never records the refused patch or `payload_hash`, so a refused pay entry or a refused name doesn't sit in the log for good.
- **Its envelope:** `actor` is `{kind: store}`, `device` is the store's own id with its own `device_seq`, and `hlc` comes from the store's clock. The refused actor goes into the patch.
- **Malformed input** (it doesn't parse, or its `op_id` isn't a UUIDv7) is recorded with `refused_op_id: null`, and its reason is cut to 200 characters.
- **Expected drops aren't logged.** An observation of an item a person deleted, a connector link a person removed, and a second create of the same connector item are counted in `meta`.
- **Replay skips refusals.** A refusal still has a `seq` and is chained.

**Times:** anything the view records with a time (`created_at`, `updated_at`, `deleted_at`) takes it from the change's `hlc`. Applying a change never reads the wall clock, so a replay gives the same view. These times are what the device stamped, not proof of when a change was made.

**History:** `item.update` doesn't carry previous values. The view keeps `field_prov(item, field, seq, actor_kind, actor_id)`: who last wrote `title`, `status`, `payload_ref` and each `fields.<key>`, and in which change. A write that sets a key to `null` records provenance too. "Overwritten by X" comes from `field_prov` plus an indexed scan of `ops` by target, and undo works out the inverse from the log.

## 5. Money

**Amount:**
- `amount` is a signed integer, `scale` is how many decimal places it has: the value is `amount × 10^-scale`.
  - **Sign:** a negative amount is money going out, a positive amount is money coming in, whatever the category. A refund from a tool is a positive `tool` entry. Zero is refused.
  - **Scale:** at least the currency's minor-unit exponent and at most 9. AI spend uses scale 6 (millionths), so a $0.0004 call is `-400` at scale 6, not 0 cents.
- `currency` must be in a fixed table, with its ISO 4217 minor-unit exponent: `EUR` 2, `USD` 2, `RSD` 2. A new code is added to the table in code, with a `rules_v` bump.
- **Totals** are summed in Python integers after bringing every amount to scale 9, never with SQL `SUM`, which would overflow.

**Keys on `ledger.add`:**

| Key | Meaning |
|---|---|
| `amount`, `scale`, `currency` | above |
| `category` | `ai`, `tool`, `service`, `client` or `other`; `pay` is reserved and refused (below) |
| `estimated` | true or false |
| `origin` | `typed`, `csv`, `trioditis` or a connector name |
| `account` | optional: an `account` item in this store with the same currency |
| `booked_to` | optional list of ids (line, task, run, budget); becomes `booked_to` links that never change |
| `orig`, `rate` | for a conversion: `orig: {amount, scale, currency}` as it was paid, and `rate: {value, source, date}` with `value` as a decimal string |

**Corrections:** an `item.update` or `item.delete` on a ledger entry is turned down ("ledger entries can't be edited"). A reversal is the only correction:
- `ledger.reverse` makes a new entry that copies every key of the original, with `amount` and `orig.amount` negated.
- It copies the `booked_to` links too, even when an end has since been deleted.
- An entry can be reversed once. A reversal can't itself be reversed: make a new entry instead.

**Pay entries (Alex's call A):** an entry for a person's pay (salary, a contractor's fee, a rate) is refused until sealing exists: "pay entries wait for sealing". The log is never rewritten, so a pay amount written in plaintext could never be sealed later.
- The check sees `category: pay` only. An amount typed under another category can't be told apart, and the entry form will say so next to the field.

This is a tracker, not accounting software.

## 6. Platforms (how a stop opens)

- A stop may carry `fields.platforms`, a list of `{os, argv}` entries.
  - `os` is `linux`, `macos` or `windows`.
  - `argv` is a list of 1 to 64 strings: the program and its arguments, never a shell line. No string is empty or holds a NUL or a line break.
- The check runs on every write that sets `fields.platforms`, from any actor, connectors included.
- Phase 1 stores and checks them. Nothing runs from the graph.

**Refused at validation time (Alex's call C).** The program name is `argv[0]`'s last path part, split on both `/` and `\` whatever the `os`, lower-cased, with `.exe` dropped.

| Refused | Why |
|---|---|
| Shells and script hosts, with any arguments: `sh`, `bash`, `dash`, `zsh`, `ksh`, `fish`, `csh`, `tcsh`, `busybox`, `cmd`, `powershell`, `pwsh`, `wscript`, `cscript`, `mshta` | they run a line of code |
| Wrappers that start another program: `env`, `sudo`, `doas`, `su`, `pkexec`, `runas`, `nohup`, `setsid`, `timeout`, `nice`, `xargs`, `start` | they would hide what really runs |
| Package runners that fetch and run code: `npx`, `bunx`, `uvx`, `pipx`, `npm exec`, `pnpm dlx`, and `deno` with any arguments | they run code that isn't on the machine yet |
| Interpreters given inline code: `python`/`python3`/`python3.N` and `py` with `-c`; `node` with `-e`, `-p`, `--eval` or `--print`; `perl` with `-e` or `-E`; `ruby` with `-e`; `php` with `-r`; `lua` with `-e`; `osascript` with `-e`; `awk` and `gawk` with any arguments | the code sits in the argv itself |
| Terminals told to run a command: `konsole`, `xterm` or `alacritty` with `-e`; `gnome-terminal` with `-e`, `-x` or `--` | the same, one step removed |
| Programs with a run-a-command option: `git` with `-c`, `--config-env` or `--exec-path`; `ssh` with `-o` or `-F`; `find` with `-exec`, `-execdir`, `-ok` or `-okdir`; `rsync` with `-e` or `--rsh` | an option value names a program to run (`core.sshCommand`, `ProxyCommand`) |
| On Windows, a program ending in `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.wsf` or `.hta` | batch files re-parse their arguments, so a value can inject a command |

**How a flag is matched,** anywhere in the argv:
- **A long flag** (`--eval`) counts when an argument equals it or starts with it plus `=` (`--eval=…`).
- **A short flag** (`-c`) counts when an argument starts with a single `-` and contains its letter. So `-Ic`, `-cprint(1)` and `-e'…'` all count.
- This turns down some harmless lines, such as `python tool.py -c 3`; that is on purpose.

The list is a first filter, not the safeguard: it can't name every program that runs code. The safeguard comes before anything runs (a later step): each machine approves an exact (item, os, argv, cwd) once, and the preview shows the argv as it will run.

## 7. Stores on disk

**Files:** `~/.lunete/graph/personal.db` and one `~/.lunete/graph/org-<id>.db` per org, using SQLite through the stdlib `sqlite3`.
- The folder is owner-only (0700).
- `<id>` is the org's id, a UUIDv7 made when the file is created. Phase 1 creates one org file; joining a team later creates a new `org-<team id>.db` and never adopts an existing one.
- `personal.db` also gets a UUIDv7 org id, which is never shared; it is the namespace for derived ids (section 8).

**Tables in each file:**
- `ops`: the log. Columns: `seq` (an integer primary key the store sets, no `AUTOINCREMENT`), `op_id` (unique), `refused_op_id` (unique where set), `target` (indexed), `client_hash`, the canonical envelope text and `hash`;
- the view tables: `items`, `links` (with tombstones) and `field_prov`;
- `payloads`: `(ref, salt, content, erased_seq)`;
- `meta`: schema version, `rules_v`, org id, space, epoch, `applied_seq`, the store's own device id and counter, and the counts of drops that aren't logged.

**Writing (one writer at a time):**
- The connection opens with `isolation_level=None`, so `sqlite3` doesn't start transactions on its own, and every write begins with an explicit `BEGIN IMMEDIATE`.
- WAL mode, `synchronous=FULL`, `secure_delete` on, and a `busy_timeout` of 5 seconds.
- Each change is one transaction: read the head, set `seq` to head + 1, append the op, update the view and `field_prov`, and set `meta.applied_seq`. A second process waits for the first rather than interleaving with it, and `seq` has no gaps.
- **On open:** the store checks that `meta.applied_seq` equals the highest `seq`. If not, it opens read-only and the error names the file, both numbers and `lunete graph check`.
- **The clock:**
  - On open, the store's HLC is seeded from the highest `hlc` in the file.
  - When a change arrives, its `hlc` must parse, and its device part must equal the envelope's `device`.
  - Its milliseconds may be at most 10 minutes ahead of the wall clock. Otherwise it is refused: "clock ahead".
  - The wall clock is read for this check only, never while applying.

**Replay:**
- The view tables can be dropped and rebuilt by applying `ops` in `seq` order.
- Replay applies each accepted change without judging it again, and skips `refusal` ops.
- `apply` keeps one branch per `rules_v`. A new rules version adds a branch and never edits an old one, so a log written under old rules rebuilds the same view. A golden test replays a fixed log to a fixed view.
- `lunete graph check`:
  - replays in memory and compares the result with the view, row by row in canonical form, ordered by key;
  - verifies the hash chain over the stored text, every `payload_hash`, and the salted hash of every payload still present;
  - counts, and never refuses, old changes that today's rules would turn down.
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

**Ids:**
- A connector item's id is `UUIDv5(namespace = the store's org id as a UUID, name = utf8(source + "\n" + external_id))`. Two devices that see the same commit make the same id, so the org store holds it once.
- A second `item.create` of the same connector item is acknowledged without a change and counted in `meta`, not refused.
- Git's `external_id` is `host[:port]/path@sha`: the host and the 40-character sha lower-cased, the port kept, credentials and a trailing `.git` dropped, and `git@host:path` turned into `host/path`.
- In `personal.db` the namespace differs per device, so the same commit has a different id on each machine. That is by design: personal stores don't sync.

**How the app turns observations into changes:**
- An observation becomes an `item.create` or `item.update` with `actor.kind = connector` and `actor.id = <name>`.
- An unchanged observation makes no change.
- **A person's edits win.** Before writing, the adapter reads `field_prov` and leaves out every field a person wrote last; the skip is counted in `health`. As a backstop, the store refuses a whole connector write that touches a field a person wrote last.
- **A link a person removed** stays removed. The adapter skips links that have a tombstone, and the store drops them from a connector and counts them.
- **An item a person deleted** is ignored while it stays deleted. The adapter drops later observations of it and counts them in `health`, so it doesn't make a refusal on every poll. Restoring the item lets them through again.
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
| Owner | members, org settings, money accounts, sealing, erasure |
| Line manager | edit the line, assign, approve agent actions, the line's budget |
| Member | create and edit items, move own stops, comment |
| Agent | only what its sponsor granted (area, verbs, expiry, spend cap); risky actions wait for a human. Grants and approvals come with B4 |

**Failure cases:**

| Case | How it resolves |
|---|---|
| A role is revoked while the member is offline | Their changes are turned down "role revoked at #N" and land in their *Needs you* lane. A manager may adopt them. |
| An edit to a deleted item | Turned down "deleted". The item stays restorable. |
| A member leaves | Tokens revoked, authorship kept, their agents' grants lapse. `payload.erase` deletes their name; the log still verifies. |
| The server is restored from a backup | A new epoch starts. Clients resubmit; duplicates are answered by `op_id` and each change is authorised again. |

**Tamper evidence:** each client keeps `(seq, head_hash)` and checks it on every pull.

## 10. Dependencies and safety

- Client and server use the standard library only. That is enough for 3 to 15 users.
- TLS comes from Caddy or Tailscale in front, and the docs will say how.
- An optional `[team]` extra adds `cryptography` for sealed content and signatures.
- The pure-Python ChaCha20 in `sync.py` stays limited to settings blobs.
- The cockpit's `bash -lc` runner (in `~/cockpit`, not this repo) is not ported. A teammate's or an agent's stop must never run code on your machine.

## 11. Front end

- The new shell is native ES modules under `lunete/static/shell/`, with no build step, served through an allowlisted module route next to `SCRIPTS`. The CSP is unchanged.
- Today's page stays at `/` until B1, then moves to `/desk` as the code desk. An iframe is not an option, because the CSP sets `frame-ancestors 'none'`.
- **Planned for step 5:** the look from design round 1. It has six palettes plus your own hue, light, dark or system, and four gradient amounts.
  - Lane colours differ in lightness and stay at least 12 apart (OKLab × 100) under the three simulated colour-vision types.
  - Today's `settings.py` has the old page's seven palettes and no gradient setting; step 5 adds the new keys.

## 12. What the skeleton branch builds, and what waits

| To build on `build/core` | Waits |
|---|---|
| `ids.py` (done): UUIDv7, the HLC with the 10-minute cap on stamps from elsewhere, UUIDv5 for connector ids and an item-id check (v7 or derived v5) | `/api/ops` and any route that writes the graph (B1) |
| `graph.py` (done): store creation (random org id, `meta`), strict parsing and `canon`, hashes, envelope checks, view, `field_prov`, payloads, replay | the Today view and the stop panel (after the ten stills) |
| the module that opens `personal.db`, and the test that the sync code never imports it | connectors (B2) |
| `ops.py` (done; `ledger.*` refused until the ledger step): `apply(envelope, channel)`, duplicates, compare-and-set, cycle check, ledger and argv rules, refusals as ops | the team server, grants and sync (B3) |
| tests (done): replay equals the live view on 500 seeded random changes, and a golden log that replays to a fixed view | proposals, approvals, grants and agent writes (B4) |
| `lunete graph check` (read-only), wired into `__main__.py` | sealing, pay entries and the `[team]` extra |
| `/shell` with the look settings (step 5) | |

## 13. Settled questions

The three open questions of draft 1, as the first review answered them:

1. **The sponsor** is a required field on agent creation, checked to be a live person, changed through `agent.sponsor` and mirrored as a link (section 3).
2. **"Overwritten by X"** comes from `field_prov` and the log; `item.update` carries no previous values (section 4).
3. **Space** isn't a column per row. Each file holds one (org, space) pair in `meta`, and a change that doesn't match is refused (section 4).

No question is open in draft 3.
