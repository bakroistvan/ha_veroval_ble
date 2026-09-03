# Veroval compact+ capture guide

Capture BLE traffic between the cuff and **Veroval medi.connect** using Android HCI snoop.
Goal: see pairing (SMP passkey) and the post-measurement data-transfer GATT sequence.

## Prerequisites

**On the phone**

- Veroval compact+ (BPU 26) with Bluetooth enabled on the cuff
- Android phone with medi.connect installed
- USB cable (or wireless adb) to pull logs from the PC

**On this PC** — install guide: **[PC_SETUP.md](../PC_SETUP.md)**

```powershell
winget install Google.PlatformTools --accept-package-agreements --accept-source-agreements
winget install WiresharkFoundation.Wireshark --accept-package-agreements --accept-source-agreements
```

- Python 3.10+ (already on this machine)
- Android platform-tools (`adb`) — **required**
- Wireshark — **recommended** (GUI + `tshark` for the analysis script)

Optional on phone: [nRF Connect](https://play.google.com/store/apps/details?id=no.nordicsemi.android.mcp) for a live GATT tree after bonding

## Enable HCI snoop on Android

1. **Settings → About phone** → tap **Build number** seven times (Developer options).
2. **Settings → Developer options**:
   - Enable **Developer options**
   - Enable **Enable Bluetooth HCI snoop log** (wording varies by OEM)
3. Toggle Bluetooth **off**, then **on** (some phones require this after enabling snoop).
4. Reboot if the log stays empty after a test transfer.

## Capture session timer (wall clock)

HCI snoop timestamps follow the **phone** clock; Home Assistant logs use the HA host clock. Click events on this PC as they happen so you can line up “result on display” vs first ADV vs connect.

```powershell
python scripts/capture_session.py
```

1. Pick a **kind** (A–D match this guide; Q quality flags; H HA-only window; G generic SIG `0x1810` for another cuff/app).
2. Set **User**, **Device**, and **Host app** (`medi.connect` or another).
3. Follow the flowchart (**two rows**, left to right, then wrap). The **What to do now** panel describes the current box. Every step stays on screen; only the current box is enabled. Branches stack in the same column. **Phone clock** / **Note** are always available.
4. Each click autosaves JSON + Markdown under `docs/captures/sessions/` (gitignored).

Escape undoes the last click.

## Capture A — bonded transfer (do this first)

Use this when the cuff is **already paired** with the phone. This shows the application protocol (what a future driver must replay).

1. Clear or note the current snoop log (optional: toggle snoop off/on in Developer options).
2. Take a **blood pressure measurement** on the cuff.
3. Wait until the **Bluetooth symbol flashes** on the cuff display (~2 minute window). Do not power off.
4. Open **medi.connect** and let the transfer finish.
5. Write down ground truth in `ground_truth_bonded.md` (copy from [`ground_truth_bonded.md.template`](ground_truth_bonded.md.template); values shown on cuff + in app).
6. Pull the log (see below). Save as:

   ```
   docs/captures/bonded_transfer.btsnoop
   ```

## Capture B — fresh pairing + GATT discovery

Use this to confirm **SMP Passkey Entry** (6-digit PIN) and first-connection service discovery.

1. In **Android Bluetooth settings**, forget/unpair the cuff.
2. In **medi.connect**, remove the device if the app keeps it registered.
3. Enable HCI snoop **before** opening the app.
4. Take a measurement; wait for the flashing Bluetooth symbol.
5. Open medi.connect; when prompted, enter the **6-digit PIN** shown on the cuff.
6. Let pairing and transfer complete.
7. Record notes in `ground_truth_pairing.md` (copy from [`ground_truth_pairing.md.template`](ground_truth_pairing.md.template)).
8. Save the log as:

   ```
   docs/captures/fresh_pairing.btsnoop
   ```

## Pull the snoop log

Install [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (`adb`).

```powershell
# List connected devices
adb devices

# This phone (OEM): /data/log/bt/btsnoop_hci.log
adb pull /data/log/bt/btsnoop_hci.log docs/captures/bonded_transfer.btsnoop

# Pixel / stock Android instead:
# adb pull /data/misc/bluetooth/logs/btsnoop_hci.log docs/captures/bonded_transfer.btsnoop

# If pull fails (permission denied), use bugreport:
adb bugreport docs/captures/bugreport.zip
# Then extract FS/data/log/bt/btsnoop_hci.log from the zip
```

Rename files clearly: `bonded_transfer.btsnoop`, `fresh_pairing.btsnoop`, `user_button_only.btsnoop`, `post_measurement.btsnoop`.

## Capture C — User button only (no new measurement)

Issue #29: contrast with Capture D. Keep the existing bond; do **not** unpair. Phone Bluetooth **on**. medi.connect **closed** until the cuff is flashing.

1. Cuff asleep. Do **not** take a reading.
2. Press **User 1** or **User 2**. Bluetooth flashes.
3. Open medi.connect and let the transfer finish.
4. Note wall-clock time, user slot, and that the display was flash-only (no new result).
5. Pull the log (see above). Save as:

   ```
   docs/captures/user_button_only.btsnoop
   ```

On this Samsung, `adb pull` of `/data/log/bt/btsnoop_hci.log` is permission-denied; use `adb bugreport` and extract `FS/data/log/bt/btsnoop_hci.log`.

## Capture D — post-measurement auto Bluetooth

Same phone, same cuff, same bond as Capture C.

1. Cuff asleep. Take a **new** blood pressure measurement.
2. Do not press User 1/2 extra; wait for the cuff to turn Bluetooth on by itself.
3. Wait ~15–20 s after the symbol flashes, then open medi.connect and let the transfer finish.
4. Note whether the **result stayed on the display** during the flash.
5. Save as:

   ```
   docs/captures/post_measurement.btsnoop
   ```

The OEM snoop file is a **rolling log**: a later bugreport often contains Capture C and D. Decode both windows by relative time / a second CCCD write, then summarize in [`../protocol.md`](../protocol.md) §7. Do not commit the `.btsnoop`.

## Optional: second bonded capture (quality flags)

If you can, repeat Capture A twice:

- **Clean reading** — resting, cuff positioned correctly.
- **Bad quality** — move/talk during measurement or loose cuff.

Compare differing ATT payloads; status/quality bits often change between the two.

## Analyze on this PC

From the repo root:

```powershell
python scripts/analyze_capture.py docs/captures/bonded_transfer.btsnoop
python scripts/analyze_capture.py docs/captures/fresh_pairing.btsnoop --pairing
python scripts/analyze_capture.py docs/captures/user_button_only.btsnoop
python scripts/analyze_capture.py docs/captures/post_measurement.btsnoop
```

If [Wireshark/tshark](https://www.wireshark.org/) is installed, the script exports ATT summaries automatically.
Otherwise it prints Wireshark filter steps and manual checklist items.

See [`../protocol.md`](../protocol.md) for the decoded protocol write-up.

Detailed Wireshark filters and field mapping: [`WIRESHARK.md`](WIRESHARK.md).

## Privacy

Do **not** commit raw logs, analyzer exports, or filled ground-truth worksheets — they contain MAC addresses, pairing PINs, and health readings.

- Raw captures: `docs/captures/*.btsnoop`, `bugreport*.zip` (gitignored)
- Exports: `docs/captures/exports/` (gitignored; run the analyzer locally)
- Session notes: copy `ground_truth_*.md.template` → `ground_truth_*.md` locally (gitignored when filled)
- Timing clicks: `docs/captures/sessions/` from `scripts/capture_session.py` (gitignored)

[`../protocol.md`](../protocol.md) keeps the decoded protocol structure; MAC/PIN/measurement examples there are **scrambled placeholders**.
