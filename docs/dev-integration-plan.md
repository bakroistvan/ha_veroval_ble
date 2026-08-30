# Plan: integrate all open PRs into `dev`

Status: **proposal** (no merges in this PR).

This is the campaign plan for landing every still-open change onto `dev`
(`8ed4da6`, which already includes issue PRs #9–#16 and #21). Subagents
analyzed each PR, the leftover feature branch, pairwise `git merge-tree`
results, and the coordinator overlap between the two runtime window PRs.

Do **not** merge this plan document as a substitute for the code PRs.
Execute the waves below on `dev`, then (separately) promote `dev` → `main`
the way #17 did.

## Snapshot (2026-08-30)

| Item | State |
|------|--------|
| Integration target | `dev` @ `8ed4da6` |
| Default branch | `main` @ `5f3885a` (behind `dev`: HAOS deploy GUI + #21) |
| Open PRs | #18, #19, #22, #24, #25 |
| Branch with no PR | `feature/last-synchronized-sensor` |
| Already on `dev` | #9–#16, #21 |
| Open issues | #20 (fixed by #21 — close after verify), #23 (fixed by #25) |

`main` is **not** the integration target. Two open PRs still point at it
and must be retargeted.

## Inventory

| PR / branch | Base today | Kind | Unique value on `dev` | Merge-tree vs `dev` | Risk |
|-------------|------------|------|------------------------|---------------------|------|
| [#18](https://github.com/bakroistvan/ha_veroval_ble/pull/18) `cursor/setup-dev-environment-1bb8` | **main** | Cloud Agent env | Only `.cursor/environment.json` (4 lines). The User-1/User-2 entity commit is already on `dev` via #9. | Clean | Low |
| [#19](https://github.com/bakroistvan/ha_veroval_ble/pull/19) `cursor/hacs-repo-maturity-132d` | **main** | HACS packaging + CI | LICENSE, brand icon, workflows, issue templates, README badges, `hacs.json` cleanup, `CHANGELOG.md` | Clean (README auto-merges) | Low |
| [#22](https://github.com/bakroistvan/ha_veroval_ble/pull/22) `cursor/phone-grace-plan-6d07` | `dev` | Plan doc only | `docs/phone-grace-plan.md` | Clean | None |
| [#24](https://github.com/bakroistvan/ha_veroval_ble/pull/24) `cursor/phone-grace-6d07` | `dev` | Runtime: 60 s phone-first grace | `PHONE_GRACE_SECONDS`, grace/skip state on `VerovalBleDeviceData` | Clean alone | Med |
| [#25](https://github.com/bakroistvan/ha_veroval_ble/pull/25) `cursor/fix-second-window-skip-a6dc` | `dev` | Runtime: new advertise window + `force_dump` | Fixes #23. `AD_SILENCE_NEW_WINDOW_SECONDS`, idle-unavailable new window, service | Clean alone | Med–high |
| `feature/last-synchronized-sensor` | (no PR) | Runtime: diagnostic timestamp sensor | `last_synchronized` on device data + `VerovalBleLastSynchronizedSensor` | Clean alone | Low |

### Already done — do not re-integrate

| PR | Landed | Notes |
|----|--------|-------|
| #9–#16 | `dev` and `main` (#17) | Device split, shared dump, poll-window, pairing races, BPM flags, auth tokens, PIN log, discovery unique_id |
| #21 | `dev` only | Proxy-only discovery vs missing host advertisement. Still needs `dev` → `main` later. Closes the code side of #20. |

## Disposition

| Change | Action | Why |
|--------|--------|-----|
| #18 | **Retarget to `dev` and merge** | Unique leftover is the Cloud Agent install file. Do not cherry-pick the old entity commit. |
| #19 | **Retarget to `dev` and merge first among packaging** | Independent of coordinator code. Gives later waves pytest / hassfest / HACS Actions. CI already green vs `main`. |
| #22 | **Close without merging the PR** after #24 lands | Implementation is #24. Optionally copy `docs/phone-grace-plan.md` into `dev` as historical design notes when #24 merges (or leave it only in the closed PR). |
| #25 | **Merge into `dev` before #24** | Larger window state machine. Easier to rebase grace onto it than the reverse. Fixes user-visible #23. |
| #24 | **Rebase onto post-#25 `dev`, then merge** | Hard content conflicts with #25. Must be rewritten against the new window rules (see reconciliation). |
| last-synchronized | **Open a PR to `dev` after #25+#24**, then merge | Additive sensor. `coordinator.py` auto-merges; `tests/test_coordinator.py` conflicts. |
| #20 / #21 | **Close #20** once the #21 behavior is confirmed on a host radio | Not a merge task. |

## File overlap

```text
                    README  const  coordinator  DEBUG  test_coord  test_dump  strings  sensor  .github  env.json
#18                    ·       ·        ·         ·        ·          ·         ·       ·        ·        ●
#19                    ●       ·        ·         ·        ·          ·         ·       ·        ●        ·
#22                    ·       ·        ·         ·        ·          ·         ·       ·        ·        ·
#24                    ●       ●        ●         ●        ●          ●         ·       ·        ·        ·
#25                    ●       ●        ●         ●        ●          ●         ●       ·        ·        ·
last-sync              ●       ·        ●         ·        ●          ●         ●       ●        ·        ·
```

`#19` also touches `manifest.json`, `hacs.json`, `LICENSE`, `CHANGELOG.md`,
brand icon, and workflows — none of those files appear in the runtime PRs.

## Conflict matrix (`git merge-tree --write-tree`)

Each branch is **clean against current `dev`**. Conflicts appear only
when two *feature* branches are combined.

| Pair | Result | Conflicted files |
|------|--------|------------------|
| #25 + #24 | **Hard conflict** | `coordinator.py`, `docs/DEBUG.md`, `tests/test_coordinator.py`, `tests/test_dump_share.py` (`const.py` + `README.md` auto-merge) |
| #25 + last-sync | Conflict | `tests/test_coordinator.py` only (`coordinator.py` auto-merges) |
| #24 + last-sync | Conflict | `tests/test_coordinator.py` only |
| #19 + #25 | Clean | README auto-merges |
| #19 + #24 | Clean | README auto-merges |
| #18 + #19 | Clean | No shared unique files vs `dev` |

**Semantic** conflict (even after a textual merge): #24 and #25 both
own `poll_needed` / `mark_window_ended` on the shared
`VerovalBleDeviceData`. A naive auto-merge of those functions would
break either “wait for the phone” or “grab the next advertise window.”

## Recommended merge order

```text
Wave 0 — packaging (parallel, no coordinator risk)
  1. Retarget #18 → dev, merge (.cursor/environment.json)
  2. Retarget #19 → dev, merge (CI + HACS + LICENSE)
     GitHub Settings still needed: repo description + topics
     Then drop `ignore: description topics license` from validate.yml

Wave 1 — coordinator window (serial, one agent)
  3. Merge #25 as-is (already based on dev, MERGEABLE)
  4. Rebase #24 onto the new dev tip and apply the combined state
     machine below. Do not mash the two coordinator.py copies.
  5. Close #22 as superseded (link to #24)

Wave 2 — sensor (serial after Wave 1)
  6. Open PR from feature/last-synchronized-sensor (or a rebase of it)
     onto post-#24 dev. Resolve test_coordinator.py. Merge.

Wave 3 — house-keeping (not this campaign’s code)
  7. Close issue #20 if #21 is confirmed; #23 closes with #25
  8. Later PR: merge `dev` → `main` (same shape as #17) so HACS
     default-branch users get #21 + Waves 0–2
  9. First GitHub Release `0.1.0` only after that `main` promotion
     (PR #19’s release.yml compares the tag to manifest version)
```

Merge **#25 before #24** because #25 replaces the window model
(idle-unavailable + 20 s advertisement silence + `force_dump`).
#24 is a 60 s timer plus a skip flag on top of “first ad of a window.”
Rebasing the timer onto the richer model is smaller than rebuilding
the richer model on top of the timer.

Do **not** merge #24 and #25 in parallel. One agent, one branch,
one rebase.

## Wave 0 — packaging

### #18 (Cloud Agent environment)

Retarget base `main` → `dev`. The three-dot diff vs current `dev` is:

```json
{ "name": "Veroval BLE",
  "install": "python3 -m pip install --user -r requirements-dev.txt -r requirements-hil.txt" }
```

If GitHub still shows the old entity.py commit in the PR files list after
retarget, reset the branch:

```text
git checkout -B cursor/setup-dev-environment-1bb8 origin/dev
# add only .cursor/environment.json from the old tip
git push --force-with-lease
```

### #19 (HACS maturity)

Retarget base `main` → `dev`. `merge-tree` is clean. After merge:

1. Set GitHub **description** and **topics** (listed in the #19 body).
   SPDX license detection reads the default branch — `LICENSE` on `dev`
   will not flip the GitHub license badge until `main` has it.
2. Keep `ignore: description topics license` in `validate.yml` until
   those Settings exist **and** `LICENSE` is on `main`.
3. Do **not** tag `0.1.0` until `dev` has been promoted to `main`.
   Tagging `dev` while HACS tracks `main` would publish a stale tree.

`CHANGELOG.md` in #19 starts at `0.1.0`. After Waves 1–2, add Unreleased
notes for second-window, phone grace, `force_dump`, and last-synchronized
before the `main` promotion.

## Wave 1 — combined coordinator state machine

Preserve **both** user-facing behaviors after #25 then #24:

1. **#24 phone grace.** First advertisement of a *new* window starts a
   60 s wait in `poll_needed`. No `asyncio.sleep(60)` inside
   `async_poll` (that holds `_poll_lock` and hides a phone grab).
   If the cuff goes unavailable during grace → skip this window
   (`_window_skipped` + reuse `_window_polled_at` so the 180 s gap
   applies). If it is still advertising after 60 s → existing shared
   GATT dump.
2. **#25 next window.** After a dump (or a skip), a **new** advertise
   session starts a new dump without waiting 180 s:
   - idle `mark_window_ended` while not dumping
   - or `AD_SILENCE_NEW_WINDOW_SECONDS` (20 s) with no `poll_needed`
   Connect-induced unavailable while `_poll_lock` is held is ignored.
   User 2 may still consume the shared cache in the same window.
   180 s remains last-resort expiry.
3. **#25 `force_dump`.** Service `veroval_ble.force_dump` bypasses
   window skip and grace and connects immediately (one GATT per MAC).

### Constants that must coexist (`const.py`)

```python
PHONE_GRACE_SECONDS = 60              # from #24
AD_SILENCE_NEW_WINDOW_SECONDS = 20    # from #25
POLL_WINDOW_GAP_SECONDS = 180         # already on dev; last-resort
SERVICE_FORCE_DUMP = "force_dump"     # from #25
```

### Flags on `VerovalBleDeviceData` (union)

| Field | Source | Role |
|-------|--------|------|
| `_poll_lock` | existing | Held during GATT; unavailable is ignored |
| `_polled_this_window` / `_window_polled_at` | existing | Dump (or skip) timestamp; 180 s backstop |
| `_window_records` / `_consumed_slots` | existing | Shared dump across User 1 / User 2 |
| `_grace_started_at` / `_window_skipped` | #24 | Phone-first wait and skip-on-disappear |
| last-ad timestamp used for silence | #25 | 20 s gap → new window |
| force-poll path | #25 | Ignore skip/grace; still one connect per MAC |

### `poll_needed` decision order (after both land)

On every advertisement, **first** run expiry / new-window detection,
then decide whether to connect:

1. 180 s since `_window_polled_at` → clear dump, grace, skip, consumed
   slots (same reset #24 already applies on gap expiry).
2. Advertisement silence ≥ 20 s **and** not currently dumping → treat
   as a **new** window: clear consumed/skip/grace, allow a new cycle.
   (This is #25. It must also reset `#24` grace so the next cycle
   waits 60 s again — do not dump immediately after silence if that
   silence was “phone finished and cuff woke later.”)
3. `_poll_lock` held → `False`.
4. Cached records and this `cuff_user` not in `_consumed_slots` → `True`
   (other slot consumes; no new connect).
5. `_window_skipped` → `False` (phone grabbed this window).
6. `_polled_this_window` → `False` unless #25’s idle-unavailable /
   silence already opened a new window in step 2.
7. Grace not started → set `_grace_started_at`, return `False`.
8. Grace elapsed &lt; 60 s → `False`.
9. Else → existing `last_poll` / `UPDATE_INTERVAL` (usually `True`).

`force_dump` skips steps 5–8.

### `mark_window_ended` decision order

1. `_poll_lock` held → return (HA’s own connect stopped ads).
2. Grace in progress and not yet dumped → **#24 skip** (not #25 new
   window). Set `_window_skipped`, stamp `_window_polled_at`, clear
   `_grace_started_at`.
3. Else idle unavailable → **#25 new-window** so the next
   advertisement can start a fresh grace (then dump).

### Semantic traps (must write tests)

| Trap | Wrong outcome | Required outcome |
|------|---------------|------------------|
| Treat grace-period unavailable as #25 “idle new window” | HA dumps the moment the phone drops the cuff | Skip this window; next *new* session (silence or 180 s) starts a new grace |
| Treat #25’s new-window ads as “still in 180 s gap” | Second measurement ignored (bug #23 again) | New window starts; grace runs; dump if phone does not grab |
| Start grace only once per process | Second window dumps immediately | Every new window starts grace |
| `asyncio.sleep(60)` in `async_poll` | Phone grab ignored | Wait is `poll_needed` returning `False` |
| User 2 starts a second 60 s wait | Dual-slot sync takes 2 minutes | One `_grace_started_at` per MAC |
| `force_dump` during grace | User cannot override | Service connects now |

### Tests that must exist on `dev` after Wave 1

Union of both PRs, plus interaction cases:

**From #25:** consumed slot polls again after idle unavailable;
20 s silence starts a new window; ads &lt; 20 s apart do not;
User 2 still consumes cache after `mark_window_ended`;
`force_dump` while the window is open; two slots share one GATT;
device targeting; lock-held unavailable; 180 s backstop.

**From #24:** first `poll_needed` starts grace; 59 s still `False`;
60 s → `True`; unavailable during grace skips; ads after skip stay
`False` until gap; after skip + 180 s a **new grace** starts;
shared grace across slots; no second grace after a real dump until
a new window.

**New interaction tests (write these on the #24 rebase):**

1. Dump completes (#25 window consumed) → idle unavailable → next ad
   starts **grace**, not an immediate dump.
2. During grace, 20 s of no ads then ads resume → still the **same**
   grace (or skip if unavailable fired); do not open a new window
   that dumps at once.
3. `force_dump` during grace performs one GATT connect and publishes.
4. Phone skip, then a later new advertise session (silence or idle
   unavailable after the cuff was truly gone) starts a new grace.

Clock-based `FakeClock` tests only — no real `sleep`.

After the rebase: `python3 -m pytest tests/ -m "not hardware"`.

## Wave 2 — last-synchronized sensor

Open a PR (suggested title: `feat: last synchronized timestamp per user slot`)
from a rebase of `origin/feature/last-synchronized-sensor` onto the
Wave 1 tip.

Runtime change is additive:

- `VerovalBleDeviceData.last_synchronized[cuff_user]` stamped when a
  dump is **consumed** for that slot (including shared-cache consume)
- Diagnostic timestamp sensor, available only after the first success
- Injectable `utcnow` for tests

`coordinator.py` auto-merges with #25/#24. `tests/test_coordinator.py`
does not — take both sides’ new test functions.

Keep stamping `last_synchronized` when a slot consumes records, including
after `force_dump`. Do **not** stamp on auth failure, empty dump, or
missing characteristic (already covered by the feature-branch tests).

## Wave 3 — docs, issues, `main`

README is touched by #18 (noop vs `dev`), #19, #24, #25, and last-sync.
Git auto-merges #19 with the runtime PRs, but the **text** will need a
single editing pass after Wave 2 so badges, the 60 s wait, second-window
behavior, `force_dump`, and the last-synchronized row all appear once.

`docs/DEBUG.md` is a hard conflict between #24 and #25 — take both
log-line sections.

Issue hygiene:

- #23 closes when #25 merges (`Fixes #23` is already in the body).
- #20 should be closed after confirming #21 on a host adapter (PR is
  already on `dev`).
- #22 closes as superseded by #24.

Promote `dev` → `main` in a dedicated PR (same role as #17) **after**
Waves 0–2 are on `dev` and pytest/hassfest/HACS are green. Then tag
`0.1.0` using #19’s release workflow.

## Execution playbook (one agent per wave)

Use **separate** Cloud Agents so each PR stays reviewable:

| Wave | Agent job | Must not do |
|------|-----------|-------------|
| 0a | Retarget + merge #18 | Rewrite entity.py |
| 0b | Retarget + merge #19 | Tag a release; delete HACS ignores before Settings exist |
| 1a | Merge #25 (mark ready-for-review if still draft) | Rebase #24 in the same PR |
| 1b | Rebase #24 onto post-#25 `dev`, implement combined state machine + interaction tests, merge | Force-merge the conflicting `coordinator.py` |
| 1c | Close #22 with a pointer to #24 | Merge the plan-only PR unless you explicitly want the doc |
| 2 | Open + merge last-synchronized PR | Rewrite window logic |
| 3 | README/DEBUG/CHANGELOG tidy + `dev` → `main` PR | Skip tests |

Local commands after every merge:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/ -m "not hardware"
```

After #19 is on `dev`, GitHub Actions run pytest + hassfest + HACS
validate on each subsequent PR automatically.

## Hardware checklist (unit tests cannot replace this)

Run on a real BPU26 + host adapter after Wave 1, again after Wave 2:

1. Pair HA. Press User 1. Logs show 60 s grace, then dump; sensors update
   before the ~2 min flash ends.
2. Press User 1, open medi.connect within 60 s. HA must **not** connect.
   Sensors stay on the last HA reading.
3. After a successful HA dump, take a **second** measurement (new flash).
   HA must start a new grace, then dump — this is #23.
4. Two slots: one GATT after grace; both slots publish; last-synchronized
   updates on both when they consume.
5. Developer tools → `veroval_ble.force_dump` while flashing: immediate
   dump, including during grace.
6. Proxy-only advertisement still fails setup with the #21 copy (no
   regression).

HIL (`scripts/hil_dump.py`) stays immediate; grace and window policy are
coordinator-only.

## Out of scope

- Dual bonding / teaching the cuff two LTKs
- Options flow for grace length
- ESPHome Bluetooth Proxy pairing
- Sleeping inside `async_poll`
- Tagging or HACS-default submission before `main` has LICENSE +
  description + topics
- Re-opening #9–#16 or re-merging #21

## Success criteria

`dev` contains, in one tree:

- Cloud Agent `environment.json`
- HACS packaging + CI from #19
- #25 second-window + `force_dump`
- #24 phone grace, reconciled with #25
- last-synchronized diagnostic sensor
- `pytest tests/ -m "not hardware"` green (expect ~80+ tests after the
  union; current `dev` has 84 collected functions across 9 files, 2 of
  them hardware-skipped)
- README/DEBUG describe grace, second window, force sync, and the new
  sensor without contradictory “connect immediately” wording
- #22 closed; #23 closed; #20 closed or verified
