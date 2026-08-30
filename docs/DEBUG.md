# Debug logging

Logger: `custom_components.veroval_ble` (includes `config_flow`, `bluez_pair`, `client`, `coordinator`).

Measurement payload hex is logged only at **DEBUG**. Enable it for a short session, then turn it off.

## Where to read the logs

| Place | What it is |
|-------|------------|
| **Settings → System → Logs** | Home Assistant Core log. Filter by `veroval_ble`. Use **Download full log** if the on-screen tail is too short. |
| Host journal (HAOS / Supervised) | BlueZ itself (`bluetoothd`) is **not** in Core logs. SSH or the Terminal add-on: `journalctl -u bluetooth.service -n 200 --no-pager` |

A pairing abort in the UI now includes the BlueZ/D-Bus error in the message (`Pairing failed ({error}). …`). The same detail is written at **WARNING** even without debug logging.

## Max verbosity

### UI (integration only)

1. **Settings → Devices & services**
2. Open **Veroval Blood Pressure BLE**
3. ⋮ → **Enable debug logging**

That sets `custom_components.veroval_ble` to DEBUG until you disable it the same way (or restart). Enough for pairing stages, Device1 snapshots, and PIN-request timing. Does **not** enable `bleak` / `habluetooth`.

### YAML (persists across restart — use this for “max”)

In `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.veroval_ble: debug
    homeassistant.components.bluetooth: debug
    habluetooth: debug
    bleak: debug
    dbus_fast: debug
```

Restart Home Assistant after saving. `bleak` / `habluetooth` / `dbus_fast` are noisy; turn them off when finished.

### Action (until restart)

Use this when you do not want to edit `configuration.yaml` or restart. The new levels apply immediately and **revert on the next Home Assistant restart**. You must be an **administrator**.

1. Open **Developer tools** in the sidebar (the hammer icon).
2. Open the **Actions** tab (older HA labeled this **Services**).
3. In **Action**, search for `logger.set_level` (UI name: **Logger: Set logger level**).
4. This action has no form fields. Switch to YAML (**⋮ → Edit in YAML**, or **Go to YAML mode**).
5. Replace the contents with:

```yaml
action: logger.set_level
data:
  custom_components.veroval_ble: debug
  homeassistant.components.bluetooth: debug
  habluetooth: debug
  bleak: debug
  dbus_fast: debug
```

6. **Perform action**. You should get a green success; nothing else is printed here.
7. Confirm: **Settings → System → Logs**. Filter `veroval_ble`. A pairing attempt should show `DEBUG` lines such as `Starting UI pairing` / `BlueZ Device1`.

For pairing diagnosis, `custom_components.veroval_ble: debug` is the one that matters. The other four are the Bluetooth stack and are noisy.

Turn it back down without restarting (same action, YAML):

```yaml
action: logger.set_level
data:
  custom_components.veroval_ble: info
  homeassistant.components.bluetooth: info
  habluetooth: warning
  bleak: warning
  dbus_fast: warning
```

If YAML mode shows only the `data:` map (no `action:` line), paste just the keys under `data:` — Home Assistant already selected the action in the dropdown.

## Capture a pairing failure

1. Enable max verbosity as above.
2. Press **User 1** or **User 2** so Bluetooth flashes.
3. Run setup until it aborts.
4. **Settings → System → Logs** → download the log.
5. Search for `Pairing failed`, `Calling BlueZ Pair()`, `BlueZ Device1`, and `Aborting pairing`.
6. Optionally capture `journalctl -u bluetooth.service -n 200 --no-pager` from the same window.
7. Disable debug logging.

Typical Core lines:

- `DEBUG` `Starting UI pairing for aa:bb:…`
- `DEBUG` `BlueZ Device1 … path=/org/bluez/hci0/dev_… Paired=False Connected=… RSSI=…`
- `INFO` `Calling BlueZ Pair() for …`
- `DEBUG` `RequestPasskey for /org/bluez/…` (cuff showed a PIN)
- `WARNING` `Pairing failed for … at Device1.Pair: org.bluez.Error.…; Address=… Paired=… Connected=…`
- `WARNING` `Aborting pairing for …: org.bluez.Error.…`

The 6-digit PIN is not written to the log.

## Force a dump (debug action)

When the cuff is advertising but Home Assistant did not connect, grab the measurement immediately:

1. Press **User 1** or **User 2** so Bluetooth flashes.
2. **Developer tools → Actions**.
3. Choose **Veroval Blood Pressure BLE: Force data sync** (`veroval_ble.force_dump`).
4. Target the **BPU26 User 1** or **User 2** device (or leave the target empty to sync every configured slot).
5. Turn on **See response** if you want the systolic / diastolic / pulse / timestamp in the result.
6. **Perform action**.

This ignores the advertise-window skip **and the 60-second phone-first grace** and starts a GATT dump now. If the cuff is not advertising, the action fails with *No connectable BPU26*. Look for `Force dump` / `Starting new advertise window (force dump)` in the log.

## Capture a measurement session

1. Enable debug as above.
2. Press **User 1** or **User 2** on the cuff so it advertises (~2 minutes).
3. Home Assistant waits **60 seconds** for medi.connect, then connects and drains the dump (or skips if the cuff disappeared). Run **Force data sync** to connect immediately.
4. **Settings → System → Logs** → download the log.
5. Disable debug logging.

Typical coordinator lines:

- `DEBUG` `Waiting 60s for phone app before polling aa:bb:…`
- `DEBUG` `Phone grace elapsed; polling aa:bb:…`
- `DEBUG` `Cuff disappeared during phone grace; skipping dump for aa:bb:…`
- `DEBUG` `Starting new advertise window (advertisement silence)` / `(idle unavailable)`
- `DEBUG` `Ignoring stale or cached advertisement for aa:bb:…`
- `INFO` `Force dump aa:bb:…`

## What appears in the log

| Level | What you see |
|-------|----------------|
| **DEBUG** | Advertisement seen; phone-first grace start / elapsed / skip; new advertise window; poll started or skipped; connect; `start_notify`; dump count and per-user counts; **selected** record hex and decoded fields (not every payload); `stop_notify` and disconnect; config-flow steps; BlueZ Device1 snapshot; agent PIN request (not the PIN value); pairing stage tracebacks |
| **INFO** | Config entry setup (address and User 1/2); successful latest reading for that slot (systolic / diastolic / pulse / time, no raw hex); `Pair()` started; pair+trust succeeded |
| **WARNING** | Connect timeout, missing Blood Pressure Measurement characteristic, parse failure, pairing failures with the BlueZ error name and Device1 properties |
| **ERROR** | Unexpected exceptions around a poll |

Advertisements are not logged at INFO (the cuff can advertise for ~2 minutes). Health hex stays at DEBUG only.

## Pairing (Home Assistant OS)

Setup pairs on the **host Bluetooth adapter** and asks for the cuff’s 6-digit PIN in the UI. Close any open `bluetoothctl` session first so the integration can register the BlueZ agent.

ESPHome Bluetooth Proxy cannot enter the PIN — use the HAOS built-in or USB adapter for pairing.

If the UI says **Pairing failed**, the `{error}` text is the BlueZ D-Bus name (for example `org.bluez.Error.AuthenticationFailed` or `org.bluez.Error.ConnectionAttemptFailed`). Common causes: cuff Bluetooth not flashing, medi.connect or a phone still bonded, wrong PIN, or a stale bond on the HA adapter.

## Hardware-in-the-loop (no Home Assistant)

Use the same drain logic as the integration without installing Home Assistant.

### Windows pairing

1. Unpair the cuff from medi.connect and other phones.
2. Press **User 1** or **User 2** on the cuff (Bluetooth symbol flashing).
3. **Settings → Bluetooth → Add device** → select `BPU26` → enter the 6-digit PIN once.

### CLI

From the repo root:

```powershell
pip install -r requirements-hil.txt
python scripts/hil_dump.py --user 1
python scripts/hil_dump.py --user 2 -v
python scripts/hil_dump.py --user 1 --address AA:BB:CC:DD:EE:FF
```

| Exit code | Meaning |
|-----------|---------|
| 0 | Success — dump counts and latest reading printed |
| 2 | No BPU26 during scan — press User 1/2 and retry |
| 3 | Auth/pairing failure — pair on this PC, unbond phones |
| 5 | Connected but no indications received |
| 6 | Dump received but no records for the requested user slot |

Logger name for `-v`: root `hil_dump` plus `custom_components.veroval_ble.client` when imported via the loader (DEBUG shows connect, notify, per-user counts, selected hex).

### Pytest

```powershell
$env:VEROVAL_HIL=1
$env:VEROVAL_HIL_USER=1
python -m pytest tests/test_hil.py -m hardware -s
```

Default `pytest` skips hardware tests. Set `VEROVAL_HIL_SCAN_TIMEOUT` (seconds) to override the 20 s scan window.
