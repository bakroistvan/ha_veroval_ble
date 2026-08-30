# Plan: phone-first grace before Home Assistant dumps the cuff

Status: **implemented** (merged onto `dev` with PR #25 second-window + `force_dump`; every new GATT window waits 60s, cache consume and force_dump do not).

After a measurement (or a User 1 / User 2 press), the BPU26 advertises for about two minutes. Today Home Assistant connects on the first advertisement and drains the `0x2A35` dump immediately. That wins the radio before **medi.connect** can. This plan adds a one-minute wait so the phone can take the transfer; if the cuff disappears during that wait, Home Assistant skips the dump.

## Goal

1. First advertisement of a new window starts a **60-second phone grace**.
2. During grace, Home Assistant must **not** connect or drain GATT.
3. If the cuff **stops advertising** during grace, treat that as “the phone grabbed it” and **skip** this window.
4. If the cuff is **still advertising** after 60 seconds, run the existing dump (one connect, shared across User 1 and User 2).

```text
User 1 / User 2  →  cuff advertises (~2 min)
                         │
                         ▼
              first BPU26 advertisement
                         │
                         ▼
              phone grace (60 s)  ── ads stop ──► skip dump
                         │
                    still advertising
                         │
                         ▼
              existing GATT dump + publish
```

## Why this fits the current code

Polling already lives in `VerovalBleDeviceData` (`coordinator.py`):

- Home Assistant’s `ActiveBluetoothDataUpdateCoordinator` calls `needs_poll` on **every** advertisement. Returning `False` for 60 seconds is enough; the cuff keeps advertising, so a later packet can start the dump.
- User 1 and User 2 already share one `VerovalBleDeviceData` per MAC (`__init__.py`). The grace timer must be **per cuff**, not per slot.
- Connecting **stops** advertisements. `_async_handle_unavailable` already exists. Today `mark_window_ended` **ignores** unavailable while `_poll_lock` is held, so Home Assistant’s own dump does not look like “window ended.”
- After a dump, `_polled_this_window` plus `POLL_WINDOW_GAP_SECONDS` (180 s, longer than the ~2 min advertise period) blocks a second connect in the same window.

Do **not** implement the wait as `asyncio.sleep(60)` inside `async_poll`. That would hold `_poll_lock`, so a phone grab would be ignored and Home Assistant would still connect afterward.

## Bonding (read this before implementing)

The cuff still bonds to **one** client. Waiting does not create dual pairing.

| Who is bonded | What the grace actually does |
|---------------|------------------------------|
| Home Assistant only | Delay HA by 60 s. The phone cannot complete a transfer unless the user re-pairs it. |
| Phone only | If medi.connect connects, ads stop → HA skips (avoids a failed/auth connect). If the phone is not opened, HA may try after 60 s and fail auth. |
| User opens the phone while HA is waiting | Ads stop → skip. That is the success path this feature is for. |

Keep the existing setup copy: unpair medi.connect **for pairing**. After pairing, this feature is a courtesy delay plus skip-on-disappear, not multi-bond support.

## Recommended design

### Constants (`const.py`)

```python
PHONE_GRACE_SECONDS = 60
```

No options flow in the first change. Zero would mean “current behavior”; add that later if someone wants it off.

### New state on `VerovalBleDeviceData` (shared per MAC)

| Field | Role |
|-------|------|
| `_grace_started_at: float \| None` | Monotonic time of the first advertisement that opened this window’s grace. |
| `_window_skipped: bool` | Unavailable fired during grace; do not dump until the 180 s window gap expires. |

Reuse `_window_polled_at` when skipping so the existing gap timer applies. Do not introduce a second clock.

### `poll_needed` (order matters)

On each advertisement, after the existing 180 s gap expiry (which must also clear grace + skip):

1. Poll lock held → `False` (unchanged).
2. Cached `_window_records` and an unconsumed slot → `True` (unchanged; other user consumes the same dump, no new connect).
3. `_polled_this_window` → `False` (unchanged).
4. `_window_skipped` → `False`.
5. `_grace_started_at is None` → set it to now, return `False`.
6. `now - _grace_started_at < PHONE_GRACE_SECONDS` → `False`.
7. Else → existing `last_poll` / `UPDATE_INTERVAL` check (usually `True`).

Gap expiry (already in `poll_needed`) should also reset `_grace_started_at` and `_window_skipped`.

### Unavailable during grace → skip

Extend `mark_window_ended` (called from `VerovalBleCoordinator._async_handle_unavailable`):

- If `_poll_lock` is held → return immediately (unchanged; HA’s own connect).
- If grace is in progress (`_grace_started_at` set, not yet dumped, not already skipped) → set `_window_skipped = True`, set `_window_polled_at` to now, clear `_grace_started_at`. Do **not** clear `_polled_this_window` in a way that allows an immediate reconnect.
- Else → keep today’s idle reset (`_polled_this_window = False`, clear `_window_polled_at` when there is no cached dump).

After a skip, later advertisements in the same ~2 min window (phone disconnected, cuff still flashing) must **not** start a new grace or dump. The 180 s gap is the same rule used after a successful HA dump.

### What not to change

- `client.dump_latest` / parser / pairing / config flow.
- Address lock between User 1 and User 2.
- `connectable=False` on the coordinator (ads still tracked; poll still requires a connectable `BLEDevice`).

## False “unavailable” (scan gaps)

Home Assistant marks a BLE device unavailable after advertisements go stale (often on the order of tens of seconds; depends on Core / `habluetooth`). A short passive-scan miss during the 60 s wait could look like a phone grab and skip a good window.

First implementation: skip on the existing unavailable callback. It is the same signal the coordinator already uses for “cuff gone.”

If hardware tests show false skips, add a short confirm (for example 10 s): on unavailable during grace, wait; if ads return, continue the **same** grace timer; if they stay gone, skip. Do not reset the 60 s clock on a gap, or a flaky scan would starve the dump.

You cannot both (a) recover from every short gap and (b) skip when the phone connects and the cuff later advertises again. The confirm delay is the compromise.

## Tests (`tests/test_coordinator.py`, FakeClock)

Keep the no-Home-Assistant loader style.

| Case | Expect |
|------|--------|
| First `poll_needed` of a window | Starts grace, returns `False`, does not call `dump_latest` |
| 59 s later | Still `False` |
| 60 s later, still “available” | `True` |
| Unavailable during grace | `_window_skipped`, later `poll_needed` is `False` |
| Ads resume after skip, before 180 s | Still `False` (same window) |
| After skip + `POLL_WINDOW_GAP_SECONDS` | New grace starts (`False` again, not an immediate dump) |
| Unavailable while `_poll_lock` held | Unchanged: do not treat as skip/end |
| Two slots, one `VerovalBleDeviceData` | One `_grace_started_at`; after dump, second slot consumes cache |
| After a real dump | No second grace until the 180 s gap |

Add a dump-share case: grace is shared; User 2 does not start a second 60 s wait after User 1’s grace elapsed and the dump ran.

## Docs and logging

- README: after setup, Home Assistant waits one minute so medi.connect can sync first; if the Bluetooth symbol goes out, HA skips.
- `docs/DEBUG.md`: example lines for grace start, skip, and grace elapsed.
- Setup strings can stay “unpair medi.connect” (pairing still needs a single bond).

Log at **DEBUG** (do not INFO-spam advertisements):

- `Waiting %ss for phone app before polling %s`
- `Cuff disappeared during phone grace; skipping dump for %s`
- `Phone grace elapsed; polling %s`

## Manual check (real cuff)

1. Pair HA as today. Press User 1. Confirm logs show grace, then a dump after ~60 s, sensors update.
2. Press User 1, open medi.connect within 60 s. Ads should stop; HA must **not** connect; sensors stay on the last HA reading.
3. Two slots configured: one dump after grace, both slots publish from the shared records.
4. Leave the cuff flashing with no phone: dump still happens before the ~2 min advertise window ends (60 s wait + connect + drain still fits).

HIL (`scripts/hil_dump.py`) stays immediate; this delay is coordinator-only.

## Implementation order

1. Constant + `VerovalBleDeviceData` state and `poll_needed` / `mark_window_ended`.
2. Unit tests above (clock-based; no sleep).
3. README / DEBUG notes.
4. Hardware pass on the three manual cases.

## Out of scope

- Options flow / configurable grace.
- Teaching the cuff two bonds.
- Changing pairing, PIN UI, or proxy rules.
- Sleeping inside `async_poll` or a background “wait then connect” task that fights the advertisement-driven coordinator.
