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

Rename files clearly: `bonded_transfer.btsnoop`, `fresh_pairing.btsnoop`.

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

[`../protocol.md`](../protocol.md) keeps the decoded protocol structure; MAC/PIN/measurement examples there are **scrambled placeholders**.
