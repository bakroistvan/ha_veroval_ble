# Veroval compact+ protocol discovery

Reverse-engineering notes for the **Veroval compact+ BPU 26** BLE link to **medi.connect**.
Status: **pairing + history dump decoded** from a local `fresh_pairing.btsnoop`. Standard SIG Blood Pressure Service. No RACP; enabling indications dumps stored records.

**Privacy:** MAC address, pairing PIN, and measurement examples below are **scrambled placeholders**. Protocol structure (GATT, flags, dump order) is real.

## Device behavior (from manual)

| Step | Behavior |
|------|----------|
| 1 | Press **User 1** or **User 2** on the cuff |
| 2 | Bluetooth symbol **flashes**; cuff advertises ~2 minutes. A new measurement is **not** required |
| 3 | Completing a measurement also starts the same advertise window; the result stays on the display |
| 4 | Open medi.connect (or a bonded host) while the symbol flashes → transfer starts |
| 5 | First pairing: cuff shows **6-digit PIN**; enter on the connecting device |

The PIN is handled by the **OS Bluetooth stack** (SMP Passkey Entry), not by custom app crypto.

## Capture checklist

- [x] `docs/captures/bonded_transfer.btsnoop` — first bugreport; **ads only** (HCI Reset, no connect)
- [x] `docs/captures/fresh_pairing.btsnoop` — local capture; Passkey Entry + full memory dump
- [ ] `docs/captures/ground_truth_bonded.md` filled with cuff + app values (local; gitignored)
- [ ] `docs/captures/ground_truth_pairing.md` filled (local; gitignored)
- [x] Run `python scripts/analyze_capture.py …` → exports under `docs/captures/exports/` (local; gitignored)

---

## 1. Advertisement

Unchanged from the first capture; same cuff in the pairing log.

| Field | Observed value |
|-------|----------------|
| Local name | `BPU26` (Complete Local Name) |
| MAC address | `AA:BB:CC:DD:EE:FF` (public; TI-class OUI — **scrambled**) |
| Address type | Public Device Address |
| Connectable | yes — ADV_IND (undirected) |
| Discoverable | **Limited** (`BR/EDR Not Supported`) |
| Service UUIDs in adv | Incomplete list: **Blood Pressure `0x1810`** |
| Manufacturer data | PAUL HARTMANN AG `0x0ABF`, payload `01 02` |
| Scan response | empty |

Wireshark: `bthci_evt.bd_addr == aa:bb:cc:dd:ee:ff`

---

## 2. Pairing (SMP)

Observed in `fresh_pairing.btsnoop` (92 SMP PDUs). LE **Secure Connections** Passkey Entry.

| Field | Expected | Observed |
|-------|----------|----------|
| Method | Passkey Entry | **yes** — 20 Confirm/Random rounds (20-bit passkey) |
| PIN digits | 6 | **6 digits** (value omitted for privacy) |
| Phone IO cap | Keyboard/Display | Pairing Request `0x04` Keyboard, Display |
| Cuff IO cap | Display Only | Pairing Response `0x00` Display Only |
| AuthReq (both) | Bonding + MITM + SC | Bonding, MITM, Secure Connection; max key 16 |
| Bonding | yes | IRK/CSRK exchanged after DHKey Check |
| Encrypted ATT after pair | yes | HCI `LE Start Encryption` then `Encrypt Change`; Android snoop still shows decrypted ATT |

Sequence (relative time ~1036–1063 s):

1. Pairing Request / Response
2. Pairing Public Key (both)
3. ~22 s pause (user entering PIN)
4. 20× Pairing Confirm + Random
5. Pairing DHKey Check
6. Encrypt, then Identity / Identity Address / Signing Information

Wireshark filter: `btsmp`

---

## 3. GATT table

Primary services from Read By Group Type (first connection, before encryption finished; rediscovered after bond).

| Handle | UUID | Name | Properties | Role |
|--------|------|------|------------|------|
| `0x0001`–`0x0009` | `0x1800` | Generic Access | | service |
| `0x0003` | `0x2A00` | Device Name | Read, Write | GAP |
| `0x0005` | `0x2A01` | Appearance | Read, Write | GAP |
| `0x0007` | `0x2A04` | PPCP | Read | GAP |
| `0x0009` | `0x2AC9` | Resolvable Private Address Only | | GAP |
| `0x000a`–`0x000d` | `0x1801` | Generic Attribute | | service |
| `0x000c` | `0x2A05` | Service Changed | | GATT |
| `0x000d` | `0x2902` | CCCD (Service Changed) | | descriptor |
| `0x000e`–`0x001c` | `0x180A` | Device Information | | service |
| `0x0010` | `0x2A23` | System ID | | DIS |
| `0x0012` | `0x2A24` | Model Number String | | DIS |
| `0x0014` | `0x2A25` | Serial Number String | | DIS |
| `0x0016` | `0x2A26` | Firmware Revision String | Read → `"11"` | DIS |
| `0x0018` | `0x2A27` | Hardware Revision String | | DIS |
| `0x001a` | `0x2A28` | Software Revision String | | DIS |
| `0x001c` | `0x2A29` | Manufacturer Name String | | DIS |
| `0x001d`–`0xffff` | `0x1810` | Blood Pressure | | service |
| `0x001f` | `0x2A35` | Blood Pressure Measurement | **Indicate** | measurement payloads |
| `0x0020` | `0x2902` | CCCD (`0x2A35`) | Write `0x0002` = Indicate | enables dump |
| `0x0022` | `0x2A36` | Intermediate Cuff Pressure | **Notify** | not used in this transfer |
| `0x0023` | `0x2902` | CCCD (`0x2A36`) | | not written |
| `0x0025` | `0x2A49` | Blood Pressure Feature | Read | not read in this capture |

**Absent:** Battery `0x180F` / `0x2A19`, Record Access Control Point `0x2A52`.

---

## 4. Connection sequence (fresh pairing + transfer)

Connection: LE Enhanced Connection Complete, central role, interval 48.75 ms, timeout 5 s, public peer `AA:BB:CC:DD:EE:FF` (**scrambled**).

```
1. [x] Connect (frame 600, t≈1036.0 s)
2. [ ] Exchange MTU — not seen
3. [x] GATT discovery (Read By Group / By Type / Find Information) — starts before pairing finishes
4. [x] SMP Passkey Entry + encrypt
5. [x] GATT rediscovery after bond
6. [x] CCCD write handle 0x0020 = Indication (0x0002)  [frame 979]
7. [x] Read Firmware Revision handle 0x0016 → "11"  [frames 981–983]
8. [x] 102× Indication handle 0x001f (0x2A35) + Confirmation  [frames 985–1238]
9. [x] Disconnect Complete  [frame 1241, t≈1075.0 s]
```

No Write to RACP. **Enabling BPM indications is the transfer trigger;** the cuff then streams stored records.

Wireshark: `btatt.opcode == 0x12` (the CCCD write), `btatt.opcode == 0x1d` (indications)

---

## 5. Measurement payload

SIG Blood Pressure Measurement (`0x2A35`), flags `0x1E` on every record in this capture.

### Layout (19 bytes)

| Offset | Size | Field | Example (scrambled) |
|--------|------|-------|---------------------|
| 0 | 1 | Flags | `1e` (status + user ID + pulse + timestamp, mmHg) |
| 1–2 | 2 | Systolic SFLOAT LE | `78 00` → **120** mmHg |
| 3–4 | 2 | Diastolic SFLOAT LE | `50 00` → **80** mmHg |
| 5–6 | 2 | Mean arterial SFLOAT LE | `00 00` → **0** (not populated) |
| 7–13 | 7 | Timestamp | year 2024, 2024-01-15 12:00:00 |
| 14–15 | 2 | Pulse SFLOAT LE | `48 00` → **72** bpm |
| 16 | 1 | User ID | `00` = slot 1, `01` = slot 2 |
| 17–18 | 2 | Measurement status uint16 LE | see below |

Raw ATT value (example): `1e 78 00 50 00 00 00 e8 07 01 0f 0c 00 00 48 00 00 00 00`

### This dump

| | Count |
|--|------:|
| Indications | 102 (all unique timestamps) |
| User `0x00` | 94 |
| User `0x01` | 8 |
| Order | **newest first**, user 0 block then user 1 block |
| Span | multi-day history ending at newest record |
| Newest (likely this session) | **120 / 80 / 72**, 2024-01-15 12:00, user 0, status `0x0000` (**scrambled example**) |

Full per-record table: run `python scripts/analyze_capture.py docs/captures/fresh_pairing.btsnoop` locally → `docs/captures/exports/fresh_pairing_bpm.csv` (gitignored).

### Field mapping

| Field | Offset / format | Example | Status |
|-------|-----------------|---------|--------|
| Flags | byte 0 = `0x1E` | always in this capture | **confirmed** |
| Systolic | SFLOAT mmHg | 120 | **confirmed** (Wireshark IEEE 11073) |
| Diastolic | SFLOAT mmHg | 80 | **confirmed** |
| Mean arterial | SFLOAT | 0 | present, always 0 here |
| Pulse | SFLOAT bpm | 72 | **confirmed** |
| Timestamp | 7-byte date-time | 2024-01-15 12:00:00 | **confirmed** (cuff clock) |
| User ID | uint8 | 0 or 1 | **confirmed** (two memory slots) |
| Measurement status | uint16 LE | `0x0000` / `0x0004` / `0x8000` | partial |
| Battery | | | **not in GATT** |

Status bits (SIG):

| Value | SIG meaning | Count |
|-------|-------------|------:|
| `0x0000` | no flags | 23 |
| `0x0004` | Irregular Pulse | 9 |
| `0x8000` | reserved bit 15 | 70 |

`0x8000` is not a documented SIG flag. Candidate for Veroval rest / quality; **unconfirmed** without ground truth. Cuff-fit and body-movement bits were all false in this dump.

---

## 6. History vs single reading

- [x] Full memory dump (102 records; device spec 2×100)
- [ ] Only latest measurement transferred — **no**, the whole store was indicated
- [x] RACP command used: **not present** (no `0x2A52` in GATT)

A driver that only wants the latest reading can take the **first** indication after CCCD enable (newest), or filter by timestamp.

---

## 7. Open questions

1. Does opening the app **before** measurement change the sequence?
2. Cuff clock: timestamps look like device-local time. Is it ever written from the phone? (Device Name/Appearance are writable.)
3. What is status `0x8000`? Rest indicator vs other quality bit — need a labeled capture.
4. Bonded reconnect: is the sequence CCCD-write → dump only, skipping SMP?
5. Intermediate Cuff Pressure (`0x2A36`): live values during inflation, or unused by medi.connect?
6. Blood Pressure Feature (`0x2A49`) bits not read in this session.

---

## References

- [Capture guide](captures/README.md)
- [BPU 26 manual (Bluetooth chapter)](https://www.manual.nz/hartmann/veroval-compact-bpu26/manual)
- [Bluetooth Blood Pressure Service 0x1810](https://www.bluetooth.com/specifications/specs/blood-pressure-service-1-1-1/)
- Analysis script: `scripts/analyze_capture.py`
- Local exports: `docs/captures/exports/` (gitignored)

## Later (out of scope for discovery)

Python `veroval_ble` client and Home Assistant `custom_components/veroval` — GATT write CCCD `0x0020` = `0200`, parse `0x2A35` indications as above.
