# Wireshark decode cheat sheet

Use after placing a capture at `docs/captures/bonded_transfer.btsnoop` or `fresh_pairing.btsnoop`.

## Open the capture

1. Wireshark → **File → Open** → select the `.btsnoop` file.
2. If prompted for encapsulation, choose **Bluetooth HCI H4** (Android snoop default).

Or run the repo script (needs `tshark` on PATH):

```powershell
python scripts/analyze_capture.py docs/captures/bonded_transfer.btsnoop
```

## Find the cuff

1. Filter (Android HCI snoop): `bthci_evt.le_meta_subevent == 0x02`
   - Link-layer captures instead: `btle.advertising_header`
2. Look for local name **BPU26** and service UUID **0x1810**.
3. Note **BD_ADDR** from the capture. Filter: `bthci_evt.bd_addr == aa:bb:cc:dd:ee:ff` (replace with your cuff's address).

## Pairing (fresh_pairing capture only)

Filter: `btsmp`

Look for:

- **Pairing Request / Response** — IO capability, OOB, bonding flags
- **Passkey Entry** — `Passkey Entry Request/Response` with 6-digit value
- After pairing: ATT traffic should be **encrypted** (Decrypt if keys available; Android snoop usually shows decrypted ATT in newer builds)

## GATT discovery (first connection)

Filters:

- `btatt.opcode == 0x10` — Read By Group Type (services)
- `btatt.opcode == 0x08` — Read By Type (characteristics)
- `btatt.opcode == 0x04` — Find Information (descriptors / UUID16)

Build a handle → UUID table in [`../protocol.md`](../protocol.md) section 3.

## Data transfer (bonded capture)

### Writes from phone (app → cuff)

Filter:

```
btatt.opcode == 0x12 || btatt.opcode == 0x52
```

Note each **Handle** and **Value** hex. CCCD writes use value `0100` (notify) or `0200` (indicate).

### Payloads from cuff (cuff → app)

Filter:

```
btatt.opcode == 0x1b || btatt.opcode == 0x1d
```

- `0x1b` = Notification
- `0x1d` = Indication (requires ATT confirm)

Export **Value** column; these are candidate measurement records.

### Follow a connection

1. Find **LE Connection Complete** or first ATT to the cuff MAC.
2. **Right-click → Follow → Bluetooth LE LL** or filter by connection handle if shown.

## SIG Blood Pressure Measurement hint

If payload starts like standard **0x2A35**:

| Byte | Meaning |
|------|---------|
| 0 | Flags (units, optional fields present) |
| 1–2 | Systolic (SFLOAT LE) |
| 3–4 | Diastolic |
| 5–6 | Mean arterial |
| … | Pulse, timestamp, user id, status per flags |

Compare decoded numbers to [`ground_truth_bonded.md`](ground_truth_bonded.md).

## Export for the repo

1. Filter to interesting ATT packets.
2. **File → Export Packet Dissections → As CSV** (include `btatt.value`, `btatt.handle`, `btatt.opcode`).
3. Or rely on `scripts/analyze_capture.py` output in `exports/`.
4. Paste summarized hex into [`../protocol.md`](../protocol.md) section 5.

## Common issues

| Problem | Fix |
|---------|-----|
| Empty log | Toggle HCI snoop off/on, reboot, retry transfer |
| Ads only, no ATT | Log is scan-only (often after toggling Bluetooth). Leave snoop enabled, open medi.connect while **BPU26** advertises, then pull |
| `adb pull` denied | Use `adb bugreport` and extract `FS/data/log/bt/btsnoop_hci.log` |
| No ATT payloads | Capture may have started after transfer; re-run with snoop enabled earlier |
| Encrypted ATT gibberish | Use phone build that logs decrypted ATT; ensure capture includes pairing |
