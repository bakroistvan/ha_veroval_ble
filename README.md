# Veroval Blood Pressure BLE

[![GitHub release](https://img.shields.io/github/v/release/bakroistvan/ha_veroval_ble?style=flat-square)](https://github.com/bakroistvan/ha_veroval_ble/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/bakroistvan/ha_veroval_ble?style=flat-square)](LICENSE)
[![Validate](https://img.shields.io/github/actions/workflow/status/bakroistvan/ha_veroval_ble/validate.yml?branch=main&label=validate&style=flat-square)](https://github.com/bakroistvan/ha_veroval_ble/actions/workflows/validate.yml)

Home Assistant custom integration for the **Veroval compact+ BPU 26** blood pressure cuff (BLE name `BPU26`). Domain: `veroval_ble`.

It pairs on the **Home Assistant host Bluetooth adapter**, drains the cuff history dump, and publishes the newest reading for the selected **User 1** or **User 2** slot.

**Not for diagnosis, treatment, or medical decision-making.** Readings are for personal tracking only.

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bakroistvan&repository=ha_veroval_ble&category=integration)

## Requirements

- Home Assistant **2024.8.0** or later
- A **local Bluetooth adapter on the Home Assistant host** (built-in, USB dongle, or HAOS Bluetooth)

**ESPHome Bluetooth Proxy is not supported for pairing** — there is no PIN UI on the proxy. Later GATT sessions are also likely to fail unless that same radio holds the bond (LTK). Pair and poll on the host adapter.

## Install

### HACS (custom repository)

This integration is not in the HACS default store yet. Add it as a custom repository, or use the My Home Assistant button above.

1. [HACS](https://hacs.xyz/) → **Integrations** → ⋮ → **Custom repositories**
2. Repository URL: `https://github.com/bakroistvan/ha_veroval_ble`  
   Category: **Integration**
3. Download **Veroval Blood Pressure BLE**
4. Restart Home Assistant

Confirm Bluetooth is already set up under **Settings → Devices & services**.

### Manual

1. Download the latest release archive from [Releases](https://github.com/bakroistvan/ha_veroval_ble/releases).
2. Copy `custom_components/veroval_ble` into `/config/custom_components/veroval_ble`.
3. Restart Home Assistant.

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

Sensors belong to **that slot only**. A second person can add the same cuff again and choose the other user (already paired — no PIN again). Each slot is a **separate Home Assistant device** (`BPU26 User 1` / `BPU26 User 2`). If an older version merged both slots into one device, delete that device and reload the integration.

Each sync **drains** the BLE history dump, then publishes the record with the **newest timestamp for the selected slot** — not the first packet.

After the cuff starts advertising, Home Assistant **waits 20 seconds** before connecting so **medi.connect** can take the transfer first. If the Bluetooth symbol goes out during that wait (the phone connected), Home Assistant skips that window. Pairing still needs a single bond — unpair the phone during setup. This wait does not keep both the phone and Home Assistant paired at once. A later advertise window (new measurement) starts the same 20-second wait; **Force data sync** (`veroval_ble.force_dump`) connects immediately.

**Delete:** Removing the **last** Veroval device for a cuff also removes the host Bluetooth bond. Deleting only one of two user slots (User 1 or User 2) leaves the bond so the other slot keeps working.

## Sensors

| Entity | Notes |
|--------|--------|
| Systolic | mmHg |
| Diastolic | mmHg |
| Pulse | bpm |
| Measured time | Cuff timestamp of the published reading |
| Last synchronized | Home Assistant time of the last successful dump for this slot |
| Connected | Diagnostic: **on** while the Home Assistant host Bluetooth adapter has a GATT link to the cuff (`bluetoothctl` **Connected: yes**), **off** when the cuff is asleep or only advertising. |
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

To force a dump while Bluetooth is flashing: **Developer tools → Actions → Veroval Blood Pressure BLE: Force data sync** (`veroval_ble.force_dump`). See **[docs/DEBUG.md](docs/DEBUG.md)**.

## Protocol and captures

- [docs/protocol.md](docs/protocol.md) — decoded BLE protocol
- [docs/DEBUG.md](docs/DEBUG.md) — Home Assistant debug logging
- [docs/captures/README.md](docs/captures/README.md) — HCI snoop capture guide

## Releases

HACS uses **published GitHub Releases**, not loose tags. Bump `version` in `custom_components/veroval_ble/manifest.json`, add a [CHANGELOG](CHANGELOG.md) section, then tag that same version (for example `0.1.0`) and push the tag. CI creates the GitHub Release.

## License

MIT — see [LICENSE](LICENSE).
