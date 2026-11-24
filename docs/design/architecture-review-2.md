# Second review of architecture.md (23 Nov 2026)

- **Who reviewed:** a fresh, independent Sonnet agent, read-only. It got draft 2 ([architecture.md](architecture.md) as of 110d44a), the first review ([architecture-review.md](architecture-review.md)) and the code (`ids.py` and its tests; `server.py`, `__main__.py`, `sync.py`, `settings.py` and `sources.py`). It didn't get my reasoning.
- **Labels:** CONFIRMED means it checked the doc or code text; PLAUSIBLE means it did not.
- **What it confirmed as fine:**
  - the first review's findings 1, 2, 4, 5, 7, 8, 9 and 11 are folded in;
  - the doc's claims about `sync.py` (ChaCha20), `sessions/*.jsonl`, the CSP's `frame-ancestors 'none'` and `SCRIPTS`.
- **Status:** all 15 findings are folded into draft 3 the same day. The one change from the reviewer's fix is under 6.

## Findings, most severe first, and what draft 3 does

| # | Severity | Finding | Draft 3 |
|---|---|---|---|
| 1 | high, CONFIRMED | The payload scheme was circular: the client's `payload_hash` covered a salted hash that only the store can make. No op renamed a person or erased a payload. | The content travels beside the envelope. The store adds `payload_salted_hash`. New ops `payload.set` and `payload.erase`; erasure keeps `(ref, erased_seq)` (section 4). |
| 2 | high, CONFIRMED | The duplicate rule contradicted itself, and a retry of a refused change made a fresh refusal or even got accepted. | A `client_hash` over the client-set fields. A retry gets the original answer, refusals included. "op_id reused" isn't logged (section 4). |
| 3 | high, CONFIRMED | Section 12 said `ids.py` had UUIDv5 and the drift cap. It has neither, and `Hlc.seen()` folds in any stamp. | Added in `ids.py` the same day (`item_uuid5`, `is_item_id`, `Hlc.seen` with `max_ahead_ms`), with 5 new tests. |
| 4 | high, CONFIRMED | The hash input wasn't defined byte for byte, and `json.loads` accepts duplicate keys, NaN and lone surrogates. | The store keeps the exact canonical text; the chain formula is given in bytes. Strict parsing, and absent is never null (section 4). |
| 5 | high, CONFIRMED | Replay ran today's apply code, so a change in apply rules would make `graph check` flag healthy stores. Several view effects had no stated source. | One frozen `apply` branch per `rules_v`, and a golden replay test. Ignored items, tombstones, mirrored links and null writes each have a stated rule (sections 3, 4, 7, 8). |
| 6 | medium, CONFIRMED | A reversal couldn't copy links to a deleted end. Reversal of a reversal, the copied keys, `income` with a negative amount, a mutable account currency and SQL `SUM` overflow were all unspecified. | Reversal copies every key and the links even to deleted ends, and can't itself be reversed. Account currency is fixed. Totals are summed at scale 9 in Python. **Changed from the fix:** instead of tying sign to category, `income` is dropped and the sign alone says the direction, so a refund from a tool can be a positive `tool` entry (section 5). |
| 7 | medium, CONFIRMED | `field_prov` didn't say which fields it covers or whether the backstop refuses a whole change, and racing connector creates made refusals. | It covers `title`, `status`, `payload_ref` and each `fields.<key>`. The backstop refuses the whole write. A second create of a connector item is acknowledged and counted (sections 4, 8). |
| 8 | medium, CONFIRMED | No strict patch schema: a create could set `deleted` or `class`, and an update could put a person's name in the log. No size limits. | A whitelist of keys per op kind; store-set fields are refused. A `person`'s title and fields are always empty. 64 KB, 8 levels, 200-character titles. Id-shape rules for typed and connector items (sections 3, 4). |
| 9 | medium, CONFIRMED | The argv flag rule missed `--eval=…`, attached values and Windows paths, and several runners were missing. | Matching covers `--flag=` and any single-dash argument holding the letter. Paths split on both `/` and `\`. New rows: package runners, `busybox`, `py`, `ssh`, `find`, `rsync`. The check runs for every actor (section 6). |
| 10 | medium, CONFIRMED | The refusal envelope wasn't defined, malformed input had nothing to record, and expected drops would flood the log. | Refusals are written by `{kind: store}` with the store's own device and counter. Malformed input gets `refused_op_id: null` and a 200-character reason. Expected drops are counted in `meta` (section 4). |
| 11 | medium-low, CONFIRMED | The 10-minute cap didn't say which clock, and nothing checked that a stamp parses or matches its device. | Checked against the wall clock at arrival only. The stamp must parse, and its device part must equal `device`. The times are labelled as the device's stamps (sections 4, 7). |
| 12 | medium-low, CONFIRMED | The cycle check read as a mixed walk, self-links weren't named, and links to deleted items could never be removed. | One relation at a time over live links. Self-links are refused. `link.remove` works when an end is deleted (section 3). |
| 13 | low-medium, CONFIRMED | The UUIDv5 namespace, the name's bytes and the git id format were open, and `ssh://host:port` wasn't covered. | The namespace is the org id (a UUIDv7). The name is UTF-8 `source\nexternal_id`. Git ids are `host[:port]/path@sha`. Per-device personal ids are noted as by design (sections 7, 8). |
| 14 | low, CONFIRMED | The build list missed store creation, the `personal.db` import test, CLI wiring and the golden test. Section 11 described the look as if built, against today's `settings.py`. | Section 12 lists them, and section 11 says the look is planned for step 5. |
| 15 | low, PLAUSIBLE | `sqlite3`'s default transaction handling and `AUTOINCREMENT` gaps would break `BEGIN IMMEDIATE` and the `applied_seq` check. Backups keep erased bytes. | `isolation_level=None` with an explicit `BEGIN IMMEDIATE`, and `seq` = head + 1 inside the transaction. The note on backups is added (sections 4, 7). |

The report announced 14 findings and listed 15; all 15 are above.

It also noted that the `bash -lc` runner isn't in this repo. That is right, because it lives in `~/cockpit`, and section 10 now says so.
