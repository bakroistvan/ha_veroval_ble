# Debug logging

Logger: `custom_components.veroval_ble`.

Measurement payload hex is logged only at **DEBUG**. Enable it for a short session, then turn it off.

## Enable

### UI (preferred)

1. **Settings → Devices & services**
2. Open **Veroval Blood Pressure BLE**
3. ⋮ → **Enable debug logging**

Disable the same way when finished.

### YAML (persists across restart)

```yaml
logger:
  default: info
  logs:
    custom_components.veroval_ble: debug
```

Optional stack loggers (noisy):

```yaml
    habluetooth: debug
    bleak: debug
```

### Action (until restart)

**Developer tools → Actions → `logger.set_level`**

```yaml
custom_components.veroval_ble: debug
```

## Capture a session

1. Enable debug as above.
2. Press **User 1** or **User 2** on the cuff so it advertises (~2 minutes).
3. Wait for Home Assistant to connect and drain the dump.
4. **Settings → System → Logs** → download the log.
5. Disable debug logging.

## What appears in the log

| Level | What you see |
|-------|----------------|
| **DEBUG** | Advertisement seen; poll started or skipped; connect; `start_notify`; dump count and per-user counts; **selected** record hex and decoded fields (not every payload); `stop_notify` and disconnect; config-flow steps including user choice |
| **INFO** | Config entry setup (address and User 1/2); successful latest reading for that slot (systolic / diastolic / pulse / time, no raw hex) |
| **WARNING** | Connect timeout, missing Blood Pressure Measurement characteristic, parse failure, auth/pairing errors (pair on the host with `bluetoothctl`) |
| **ERROR** | Unexpected exceptions around a poll |

Advertisements are not logged at INFO (the cuff can advertise for ~2 minutes). Health hex stays at DEBUG only.

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
