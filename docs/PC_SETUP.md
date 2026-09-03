# PC setup guide (Windows)

Tools to install on **this PC** for Veroval BLE protocol discovery.
The cuff and medi.connect app run on your phone; this machine pulls captures and decodes them.

## Summary

| Tool | Required? | Purpose |
|------|-----------|---------|
| [Python 3.10+](#python) | **Yes** | Run `scripts/analyze_capture.py` |
| [Android platform-tools (`adb`)](#android-platform-tools-adb) | **Yes** | Pull HCI snoop logs from the phone |
| [Wireshark](#wireshark) | **Recommended** | GUI decode + `tshark` for automatic ATT export |
| [Git](#git-optional) | Optional | Clone/update this repo |
| [Android USB driver](#android-usb-driver) | If `adb devices` is empty | Phone visible over USB |

No Python pip packages are required — the analysis script uses only the standard library.

### Quick install (winget)

Run in PowerShell (admin prompt may appear for Wireshark):

```powershell
winget install Google.PlatformTools --accept-package-agreements --accept-source-agreements
winget install WiresharkFoundation.Wireshark --accept-package-agreements --accept-source-agreements
```

Package IDs: `Google.PlatformTools`, `WiresharkFoundation.Wireshark` (not `Wireshark.Wireshark`).

Open a **new terminal** after install, then run the [verify](#verify-full-toolchain) commands below.

---

## Python

**Status on this PC:** Python 3.13.1 is available.

If you need to install or reinstall:

1. Download from [python.org/downloads](https://www.python.org/downloads/)
2. Run the installer and check **“Add python.exe to PATH”**
3. Verify:

   ```powershell
   python --version
   ```

---

## Android platform-tools (`adb`)

**Status on this PC:** not installed yet (`adb` not found on PATH).

Used to copy `btsnoop_hci.log` from the phone after a capture.

### Install

**Option A — standalone zip (simplest)**

1. Download [SDK Platform-Tools for Windows](https://developer.android.com/tools/releases/platform-tools)
2. Extract to e.g. `C:\platform-tools`
3. Add to PATH:
   - **Settings → System → About → Advanced system settings → Environment Variables**
   - Under **User variables**, edit **Path** → **New** → `C:\platform-tools`
4. Open a **new** PowerShell window and verify:

   ```powershell
   adb version
   ```

**Option B — winget**

```powershell
winget install Google.PlatformTools --accept-package-agreements --accept-source-agreements
```

### Phone setup

1. **Settings → Developer options → USB debugging** — ON
2. Connect phone by USB; accept **“Allow USB debugging?”** on the phone
3. Verify:

   ```powershell
   adb devices
   ```

   You should see a device serial and `device` (not `unauthorized`).

### Pull a capture

From the repo root (after a measurement + app transfer on the phone):

```powershell
adb pull /data/log/bt/btsnoop_hci.log docs/captures/bonded_transfer.btsnoop
```

On Pixel / stock Android the log is at `/data/misc/bluetooth/logs/btsnoop_hci.log` instead.

If pull fails with *permission denied*, use a bugreport instead:

```powershell
adb bugreport docs/captures/bugreport.zip
```

Then extract `FS/data/log/bt/btsnoop_hci.log` from the zip.

---

## Wireshark

**Status on this PC:** installed (Wireshark / TShark 4.6.8 at `C:\Program Files\Wireshark\`). Open a **new terminal** or reboot if `tshark` is not on PATH yet.

**Recommended** — without it you can still analyze manually, but the repo script cannot auto-export ATT packets.

### Install

**Option A — winget**

```powershell
winget install WiresharkFoundation.Wireshark --accept-package-agreements --accept-source-agreements
```

**Option B — installer**

1. Download from [wireshark.org/download](https://www.wireshark.org/download.html)
2. Run the installer
3. Enable **Tshark** / command-line tools when asked (default on Windows installer)
4. Verify:

   ```powershell
   tshark -v
   ```

   If not found, add Wireshark’s install folder to PATH, typically:

   ```
   C:\Program Files\Wireshark
   ```

### Use

- **GUI:** open `.btsnoop` files; see [captures/WIRESHARK.md](captures/WIRESHARK.md)
- **CLI:** used automatically by:

  ```powershell
  python scripts/analyze_capture.py docs/captures/bonded_transfer.btsnoop
  ```

---

## Git (optional)

Only needed to clone or push this repo.

```powershell
winget install Git.Git
git --version
```

---

## Android USB driver

If `adb devices` shows nothing (phone connected, no prompt):

| Phone brand | Driver |
|-------------|--------|
| Google Pixel | [Google USB Driver](https://developer.android.com/studio/run/win-usb) |
| Samsung | [Samsung USB Driver](https://developer.samsung.com/android-usb-driver) |
| Other OEM | Install OEM suite or use **Wireless debugging** (Android 11+) |

### Wireless adb (no USB driver)

On the phone: **Developer options → Wireless debugging → Pair device with pairing code**

On the PC (platform-tools installed):

```powershell
adb pair <phone-ip>:<pairing-port>
adb connect <phone-ip>:<debug-port>
adb devices
```

---

## Verify full toolchain

Run from the repo root after installing everything:

```powershell
python --version
adb version
tshark -v
```

Expected: all three commands print version info without “not recognized”.

Then, once you have a capture file:

```powershell
python scripts/analyze_capture.py docs/captures/bonded_transfer.btsnoop
```

Outputs land in `docs/captures/exports/` (JSON, CSV, Markdown notes).

During a capture, stamp wall-clock events (measurement, Bluetooth flash, app open) with:

```powershell
python scripts/capture_session.py
```

Logs land in `docs/captures/sessions/` (gitignored). See [captures/README.md](captures/README.md).

---

## What stays on the phone (not this PC)

| Item | Where |
|------|--------|
| Veroval medi.connect app | Android — Play Store |
| HCI snoop toggle | Android Developer options |
| nRF Connect (optional GATT browser) | Android — Play Store |
| Blood pressure cuff | Hardware |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `adb` not recognized | Install platform-tools; restart terminal; check PATH |
| `unauthorized` in `adb devices` | Revoke USB debugging authorizations on phone; replug USB; accept prompt |
| Empty btsnoop log | Toggle HCI snoop off/on; reboot phone; redo transfer with app open |
| Ads only, no ATT | Log started at HCI Reset (Bluetooth toggled). Keep snoop on, do not toggle BT, open the app while **BPU26** advertises, then pull |
| `tshark` not recognized | Reinstall Wireshark with CLI tools; add `C:\Program Files\Wireshark` to PATH |
| Script exits with code 2 | Wireshark missing — install it or follow manual steps printed by the script |
| Script exits with code 1 | Capture file path wrong — place `.btsnoop` under `docs/captures/` |

---

## Next steps

1. Install **platform-tools** and **Wireshark** on this PC
2. Follow [captures/README.md](captures/README.md) on the phone
3. Analyze and update [protocol.md](protocol.md)
