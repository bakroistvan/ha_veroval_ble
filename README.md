# ha_veroval_ble

Home Assistant custom integration (**Veroval Blood Pressure BLE**) for the **Veroval compact+ BPU 26** blood pressure cuff (BLE name `BPU26`). Domain: `veroval_ble`.

**Not for diagnosis, treatment, or medical decision-making.** Readings are for personal tracking only.

## Requirements

- Home Assistant 2024.8.0 or later
- A **local Bluetooth adapter on the Home Assistant host** (built-in, USB dongle, or HAOS Bluetooth)

**ESPHome Bluetooth Proxy is not supported for pairing** — there is no PIN UI on the proxy. Later GATT sessions are also likely to fail unless that same radio holds the bond (LTK). Pair and poll on the host adapter.

## Install (HACS)

1. [HACS](https://hacs.xyz/) → **Integrations** → ⋮ → **Custom repositories**
2. Repository URL: `https://github.com/bakroistvan/ha_veroval_ble`  
   Category: **Integration**
3. Download **Veroval Blood Pressure BLE**
4. Restart Home Assistant

Confirm Bluetooth is already set up under **Settings → Devices & services**.

## Wake the cuff

Press **User 1** or **User 2** on the cuff. The Bluetooth symbol flashes and the cuff advertises as `BPU26` for about **2 minutes**.

A new measurement is **not** required to pair or sync. If the window ends, press User 1 or User 2 again.

Unpair the cuff from **medi.connect** (and do not leave a phone connected). Only one BLE client should hold the bond.

## Pair once (in the Home Assistant UI)

The cuff shows a **6-digit PIN** during Bluetooth pairing (SMP Passkey Entry). Setup collects that PIN in the Home Assistant UI on the **host adapter** (built-in or USB). Later presses reuse the bond — no PIN again.

1. Unpair the cuff from **medi.connect** and any phone.
2. Press **User 1** or **User 2** so Bluetooth flashes.
3. Add **Veroval Blood Pressure BLE** (or open the discovery card).
4. When prompted, enter the **6-digit PIN** shown on the cuff.
5. Choose **User 1** or **User 2** for this config entry.

**ESPHome Bluetooth Proxy cannot pair this cuff** (no way to enter the PIN on the proxy radio). Use the HAOS host adapter.

If the UI says the pairing agent is unavailable, close any open `bluetoothctl` session and retry. As a last resort on HAOS:

```text
bluetoothctl
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

Then run setup again (already-paired cuffs skip the PIN step).

## Home Assistant setup

1. Press **User 1** or **User 2** so the cuff advertises.
2. **Settings → Devices & services** — Home Assistant should discover **Veroval Blood Pressure BLE**, or add it manually and scan.
3. Enter the cuff PIN when the UI asks (host adapter only).
4. Select **User 1** or **User 2** — the slot whose latest reading this config entry will publish.

Sensors belong to **that slot only**. A second person can add the same cuff again and choose the other user (already paired — no PIN again).

Each sync **drains** the BLE history dump, then publishes the record with the **newest timestamp for the selected slot** — not the first packet.

## Sensors

| Entity | Notes |
|--------|--------|
| Systolic | mmHg |
| Diastolic | mmHg |
| Pulse | bpm |
| Measured time | Cuff timestamp of the published reading |
| User slot | Label **User 1** / **User 2** |
| Irregular pulse | Binary sensor (not atrial fibrillation) |

There is **no battery** entity (the cuff does not expose one).

## Automations

Trigger on **measured time** changing, not on systolic. Systolic can repeat across days; the timestamp is unique per reading.

## Hardware-in-the-loop (no Home Assistant)

Test the cuff from this PC with only Bleak — same drain + parse logic as the integration.

1. **Pair once in Windows:** Settings → Bluetooth → Add device while **User 1** or **User 2** is flashing on the cuff. Enter the 6-digit PIN when prompted. Unpair medi.connect and other phones first.
2. Press **User 1** or **User 2** so the cuff advertises (~2 minutes).
3. From the repo root:

```powershell
pip install -r requirements-hil.txt
python scripts/hil_dump.py --user 1
```

Use `--user 2` for the second slot, `--address AA:BB:...` to skip scan, `-v` for DEBUG. Exit code **2** means no cuff found (wake it and retry). Auth errors mean the cuff is not paired on this machine or another device still holds the bond.

Optional pytest (same hardware):

```powershell
$env:VEROVAL_HIL=1
python -m pytest tests/test_hil.py -m hardware -s
```

See **[docs/DEBUG.md](docs/DEBUG.md)** for HIL logging details.

## Troubleshooting

See **[docs/DEBUG.md](docs/DEBUG.md)** for debug logging (UI, YAML, and `logger.set_level`).

Typical causes: advertise window expired (press User 1/2 again), host not paired/trusted, medi.connect or a phone still bonded, or Bluetooth Proxy used instead of the host adapter.

## Protocol and captures

- [docs/protocol.md](docs/protocol.md) — decoded BLE protocol
- [docs/DEBUG.md](docs/DEBUG.md) — Home Assistant debug logging
- [docs/captures/README.md](docs/captures/README.md) — HCI snoop capture guide

## License

MIT — see [LICENSE](LICENSE).
