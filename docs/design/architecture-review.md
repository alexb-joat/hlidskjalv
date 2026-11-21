# Review of architecture.md (21 Nov 2026)

- **Who reviewed:** an independent Sonnet agent. It got the doc, the plan's decisions (enchanted-gadget lines 73-246) and the code (server.py, `__main__.py`, sync.py), not my conclusions.
- **Labels:** CONFIRMED means it checked the doc, plan or code text. PLAUSIBLE means it did not.
- **Status:** none of the fixes below is in `architecture.md` yet. Folding them in is the first step of the next session, before `graph.py`.

## Findings, most severe first, with the fix to adopt

1. **Hashes (CONFIRMED).**
   - **Problems:**
     - `payload_hash` covers only the patch, so for links it is a constant and `preview_hash` can't bind an approval to its target.
     - `hash` and `prev_hash` have no stated input.
     - "Canonical JSON" leaves floats and NaN open.
     - A plain sha256 of a small value (a pay amount, a name) can be confirmed by guessing, which leaks sealed content and breaks erasure.
   - **Fix:**
     - `payload_hash = sha256(canon({kind, target, patch}))` and `hash = sha256(prev_hash ‖ canon(envelope without hash))`.
     - No floats in envelopes: decimal strings.
     - Anything sealable or erasable sits behind `payload_ref` and is hashed salted or as ciphertext.
2. **Replay across code versions (CONFIRMED).**
   - **Problems:**
     - Re-checking old ops under new rules can refuse what was accepted back then.
     - `item.update` merge semantics are undefined.
     - Times must not come from the wall clock during a check.
   - **Fix:**
     - Replay applies logged ops without re-validating them.
     - `meta.rules_v`.
     - `fields` is a shallow merge where null deletes a key.
     - Times come from the hlc.
3. **Money (CONFIRMED).**
   - **Problems:**
     - Whole cents round AI spend to 0.
     - There is no exponent table and no sign convention.
     - A conversion loses the original amount.
     - `spent_on` links can move money without a reversal.
   - **Fix:**
     - A `scale` per entry (micro-units for AI spend).
     - A signed amount with a written convention.
     - `orig_amount` next to `rate`.
     - A fixed currency table.
     - `spent_on` on ledger items is immutable, and a reversal copies the links.
4. **Connectors vs human edits (CONFIRMED).**
   - **Problems:**
     - There is no per-field provenance.
     - A link a person removed comes back on the next poll.
     - A deleted item makes a refusal on every poll.
     - Observations have no space.
     - Ids aren't deterministic, so two devices make duplicates.
   - **Fix:**
     - `field_prov(item, field, seq, actor)` in the view, and connector writes to human-written fields are refused.
     - Removed links become tombstones.
     - An `ignored` state for deleted items.
     - Each source instance is bound to a space in config, with `personal` as the default.
     - Connector item id = UUIDv5(org, source, external_id); git is keyed on the normalised remote URL.
5. **Duplicate `op_id` and the refused table (CONFIRMED).**
   - **Problems:**
     - A reused `op_id` with a different payload is dropped silently.
     - `refused` sits outside the log.
   - **Fix:**
     - An identical envelope is acknowledged with its original seq; a different one is refused "op_id reused".
     - Refusals go into the log as ops.
6. **Crash and two writers (PLAUSIBLE).**
   - **Fix:**
     - Write with `BEGIN IMMEDIATE`, with head, insert, view and `meta.applied_seq` in one transaction, `busy_timeout`, and `synchronous=FULL`.
     - On open, check that applied_seq equals max(seq).
     - Seed the HLC from max(hlc) at open and cap the drift of foreign stamps.
     - Order only by seq.
7. **`org = local` in envelopes (CONFIRMED).**
   - **Problem:** joining a team later would need history rewritten.
   - **Fix:**
     - Every org store gets a random org id when it is created.
     - Joining a team creates a new `org-<id>.db`; an existing one is never adopted.
8. **Cross-store links (CONFIRMED).**
   - **Problem:** a personal replay can't check an org item it doesn't hold.
   - **Fix:**
     - The personal store doesn't check foreign ids; a dangling link shows as "unavailable".
     - The org store refuses ids it doesn't hold.
     - The personal-store path lives in a module the sync code can't import, with a test that checks this.
9. **Actor and approvals (CONFIRMED).**
   - **Problems:**
     - `actor.kind` is set by the client.
     - There is no op for a proposal, an approval or a grant.
10. **Platform argv (PLAUSIBLE).**
    - **Problems:**
      - A shape check passes `bash -lc …` and `git -c core.sshCommand=…`.
      - Windows re-quotes argv, and `.bat`/`.cmd` allow argument injection.
11. **Smaller.**
    - **Fix:**
      - a cycle check on `part_of` and `depends_on`;
      - how many sponsors an agent may have;
      - rules for `account`;
      - a relation for money in (`spent_on` only covers costs);
      - removing a missing link is a no-op with a refusal.

## Open questions: the reviewer's answers

1. **The sponsor:** a required field on agent creation, checked to be a live person. Changes go through an `agent.sponsor` op, and the view mirrors it as a link.
2. **"Overwritten by X":** no previous values in `item.update`. The history comes from `field_prov` plus an indexed `ops(target)` scan; undo works out its inverse from the log.
3. **Space:** no space column per row. (org, space) go into `meta` when the store is created, and an envelope that doesn't match is refused.

## For Alex to decide (my recommendation first)

- **A. Pay amounts in phase 1:** refuse pay entries until sealing exists (recommended), because a plaintext entry in a log that is never rewritten can't be sealed later. Or allow them, labelled as never sealable.
- **B. Proposals, approvals and grants (finding 9):** defer them explicitly to B4, agents as workers (recommended). Until then the local app derives `actor` from the signed-in channel, and an agent's writes are refused outright.
- **C. Platform argv (finding 10):** start refusing, at validation time, interpreters with `-c` or `-e` style flags and `.bat`/`.cmd` targets (recommended). Approval by (item, os, argv, cwd) waits until anything runs.
