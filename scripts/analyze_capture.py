#!/usr/bin/env python3
"""Extract ATT/GATT events from Android btsnoop captures for Veroval protocol discovery."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Bluetooth SIG UUIDs we expect to find (hypothesis)
SIG_UUIDS = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
    "00001810-0000-1000-8000-00805f9b34fb": "Blood Pressure",
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a04-0000-1000-8000-00805f9b34fb": "Peripheral Preferred Connection Parameters",
    "00002a05-0000-1000-8000-00805f9b34fb": "Service Changed",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number String",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number String",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision String",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision String",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision String",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name String",
    "00002a35-0000-1000-8000-00805f9b34fb": "Blood Pressure Measurement",
    "00002a36-0000-1000-8000-00805f9b34fb": "Intermediate Cuff Pressure",
    "00002a49-0000-1000-8000-00805f9b34fb": "Blood Pressure Feature",
    "00002a52-0000-1000-8000-00805f9b34fb": "Record Access Control Point",
    "00002ac9-0000-1000-8000-00805f9b34fb": "Resolvable Private Address Only",
    "00002902-0000-1000-8000-00805f9b34fb": "Client Characteristic Configuration",
}

WIRESHARK_FILTERS = {
    # Android HCI snoop is HCI H4: ads show up as LE Meta events, not btle.*
    "advertising": "btle.advertising_header || bthci_evt.le_meta_subevent == 0x02",
    "pairing": "btsmp",
    "att": "btatt",
    "att_writes": "btatt.opcode == 0x12 || btatt.opcode == 0x52",
    "att_indications": "btatt.opcode == 0x1d",
    "att_notifications": "btatt.opcode == 0x1b",
    "cccd": "btatt.uuid16 == 0x2902",
    "le_connect": "bthci_evt.le_meta_subevent == 0x01 || bthci_evt.le_meta_subevent == 0x0a",
}


@dataclass
class CaptureSummary:
    source_file: str
    analyzed_at: str
    tshark_available: bool
    pairing_mode: bool
    att_events: list[dict] = field(default_factory=list)
    smp_events: list[dict] = field(default_factory=list)
    advertisements: list[dict] = field(default_factory=list)
    discovered_uuids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_tshark() -> str | None:
    exe = shutil.which("tshark")
    if exe:
        return exe
    for candidate in (
        r"C:\Program Files\Wireshark\tshark.exe",
        r"C:\Program Files (x86)\Wireshark\tshark.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def run_tshark(tshark: str, capture: Path, display_filter: str, fields: list[str]) -> list[dict]:
    cmd = [
        tshark,
        "-r",
        str(capture),
        "-Y",
        display_filter,
        "-T",
        "fields",
    ]
    for f in fields:
        cmd.extend(["-e", f])
    cmd.extend(["-E", "separator=|", "-E", "quote=d", "-E", "occurrence=f"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return []

    if proc.returncode != 0 and not proc.stdout.strip():
        return []

    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip('"') for p in line.split("|")]
        if len(parts) != len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def normalize_uuid(value: str) -> str:
    value = value.strip().lower()
    if not value:
        return value
    if re.fullmatch(r"0x[0-9a-f]{4}", value):
        return f"0000{value[2:]}-0000-1000-8000-00805f9b34fb"
    if re.fullmatch(r"[0-9a-f]{4}", value):
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    return value


def att_opcode_name(opcode: str) -> str:
    mapping = {
        "0x01": "Error Response",
        "0x02": "Exchange MTU Request",
        "0x03": "Exchange MTU Response",
        "0x04": "Find Information Request",
        "0x05": "Find Information Response",
        "0x08": "Read By Type Request",
        "0x09": "Read By Type Response",
        "0x0a": "Read Request",
        "0x0b": "Read Response",
        "0x10": "Read By Group Type Request",
        "0x11": "Read By Group Type Response",
        "0x12": "Write Request",
        "0x13": "Write Response",
        "0x1b": "Handle Value Notification",
        "0x1d": "Handle Value Indication",
        "0x1e": "Handle Value Confirmation",
        "0x52": "Write Command",
    }
    return mapping.get(opcode.lower(), opcode or "unknown")


def analyze_with_tshark(tshark: str, capture: Path, pairing_mode: bool) -> CaptureSummary:
    summary = CaptureSummary(
        source_file=str(capture),
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        tshark_available=True,
        pairing_mode=pairing_mode,
    )

    # Advertisements (link-layer or HCI LE Advertising Report)
    adv_rows = run_tshark(
        tshark,
        capture,
        WIRESHARK_FILTERS["advertising"],
        [
            "frame.number",
            "frame.time_relative",
            "btle.advertising_address",
            "bthci_evt.bd_addr",
            "bthci_evt.le_advts_event_type",
            "btcommon.eir_ad.entry.device_name",
            "btcommon.eir_ad.entry.uuid_16",
            "btcommon.eir_ad.entry.company_id",
            "btcommon.eir_ad.entry.data",
            "bthci_evt.le_rssi",
        ],
    )
    cuff_addrs: set[str] = set()
    named: dict[str, str] = {}
    for row in adv_rows:
        addr = (row.get("bthci_evt.bd_addr") or row.get("btle.advertising_address") or "").lower()
        name = row.get("btcommon.eir_ad.entry.device_name") or ""
        uuid16 = (row.get("btcommon.eir_ad.entry.uuid_16") or "").lower()
        if addr and name:
            named[addr] = name
        if name.upper() == "BPU26" or uuid16 in {"0x1810", "1810"}:
            if addr:
                cuff_addrs.add(addr)
            uuid = normalize_uuid(uuid16)
            if uuid and uuid not in summary.discovered_uuids:
                summary.discovered_uuids.append(uuid)
    cuff_ads = [
        row
        for row in adv_rows
        if (row.get("bthci_evt.bd_addr") or row.get("btle.advertising_address") or "").lower() in cuff_addrs
    ]
    # Keep only the cuff in exports (nearby phones/scales stay out of git)
    summary.advertisements = cuff_ads[:200]
    if cuff_ads:
        first = cuff_ads[0]
        last = cuff_ads[-1]
        addr = first.get("bthci_evt.bd_addr") or first.get("btle.advertising_address")
        summary.notes.append(
            f"Cuff advertising: name={first.get('btcommon.eir_ad.entry.device_name') or 'BPU26'} "
            f"addr={addr} uuid16={first.get('btcommon.eir_ad.entry.uuid_16')} "
            f"company={first.get('btcommon.eir_ad.entry.company_id')} "
            f"mfg_data={first.get('btcommon.eir_ad.entry.data')} "
            f"reports={len(cuff_ads)} t={first.get('frame.time_relative')}s..{last.get('frame.time_relative')}s"
        )
    elif adv_rows:
        summary.notes.append(
            f"{len(adv_rows)} LE advertising reports, none named BPU26 / UUID 0x1810. "
            f"Named devices: {', '.join(sorted(set(named.values())) ) or '(none)'}"
        )

    # SMP pairing
    if pairing_mode:
        smp_rows = run_tshark(
            tshark,
            capture,
            WIRESHARK_FILTERS["pairing"],
            ["frame.number", "frame.time_relative", "btsmp.opcode"],
        )
        for row in smp_rows:
            summary.smp_events.append(row)
        if not smp_rows:
            summary.notes.append("No btsmp packets found; try filter 'btle' or verify capture includes pairing.")
        else:
            opcodes = [r.get("btsmp.opcode") for r in smp_rows]
            summary.notes.append(
                f"{len(smp_rows)} SMP packets; first opcodes="
                + ", ".join(opcodes[:8])
                + ("; ..." if len(opcodes) > 8 else "")
                + ". LE Secure Connections Passkey Entry uses many Confirm/Random rounds (20-bit passkey)."
            )

    # ATT traffic (writes, notifications, indications)
    att_rows = run_tshark(
        tshark,
        capture,
        WIRESHARK_FILTERS["att"],
        [
            "frame.number",
            "frame.time_relative",
            "btatt.opcode",
            "btatt.handle",
            "btatt.uuid16",
            "btatt.uuid128",
            "btatt.value",
            "btatt.blood_pressure_measurement.compound_value.systolic.mmhg",
            "btatt.blood_pressure_measurement.compound_value.diastolic.mmhg",
            "btatt.blood_pressure_measurement.pulse_rate",
            "btatt.year",
            "btatt.month",
            "btatt.day",
            "btatt.hours",
            "btatt.minutes",
            "btatt.blood_pressure_measurement.user_id",
            "btatt.blood_pressure_measurement.status",
        ],
    )
    for row in att_rows:
        uuid = normalize_uuid(row.get("btatt.uuid128") or row.get("btatt.uuid16") or "")
        if uuid and uuid not in summary.discovered_uuids:
            summary.discovered_uuids.append(uuid)
        evt = {
            "frame": row.get("frame.number"),
            "time_s": row.get("frame.time_relative"),
            "opcode": row.get("btatt.opcode"),
            "opcode_name": att_opcode_name(row.get("btatt.opcode", "")),
            "handle": row.get("btatt.handle"),
            "uuid": uuid,
            "uuid_name": SIG_UUIDS.get(uuid, ""),
            "value_hex": row.get("btatt.value"),
        }
        sys_v = row.get("btatt.blood_pressure_measurement.compound_value.systolic.mmhg") or ""
        if sys_v:
            evt["sys"] = sys_v
            evt["dia"] = row.get("btatt.blood_pressure_measurement.compound_value.diastolic.mmhg")
            evt["pulse"] = row.get("btatt.blood_pressure_measurement.pulse_rate")
            evt["user"] = row.get("btatt.blood_pressure_measurement.user_id")
            evt["status"] = row.get("btatt.blood_pressure_measurement.status")
            y, mo, d = row.get("btatt.year"), row.get("btatt.month"), row.get("btatt.day")
            h, mi = row.get("btatt.hours"), row.get("btatt.minutes")
            if y:
                evt["timestamp"] = f"{y}-{mo.zfill(2) if mo else '00'}-{d.zfill(2) if d else '00'} {h.zfill(2) if h else '00'}:{mi.zfill(2) if mi else '00'}"
        summary.att_events.append(evt)

    writes = [e for e in summary.att_events if e.get("opcode_name") in ("Write Request", "Write Command")]
    for w in writes:
        summary.notes.append(
            f"frame {w['frame']}: {w['opcode_name']} handle={w['handle']} "
            f"uuid={w.get('uuid_name') or w.get('uuid')} value={w.get('value_hex')}"
        )

    bpm = [e for e in summary.att_events if e.get("sys")]
    if bpm:
        users = {}
        for e in bpm:
            users[e.get("user") or "?"] = users.get(e.get("user") or "?", 0) + 1
        first, last = bpm[0], bpm[-1]
        summary.notes.append(
            f"{len(bpm)} Blood Pressure Measurement indications on handle {first.get('handle')}: "
            f"first {first.get('sys')}/{first.get('dia')} p={first.get('pulse')} "
            f"t={first.get('timestamp')} user={first.get('user')} status={first.get('status')}; "
            f"last {last.get('sys')}/{last.get('dia')} p={last.get('pulse')} "
            f"t={last.get('timestamp')} user={last.get('user')} status={last.get('status')}; "
            f"per-user {users}"
        )
    else:
        for evt in summary.att_events:
            name = evt.get("opcode_name", "")
            if name in ("Handle Value Notification", "Handle Value Indication"):
                summary.notes.append(
                    f"frame {evt['frame']}: {name} handle={evt['handle']} "
                    f"uuid={evt.get('uuid_name') or evt.get('uuid')} value={evt.get('value_hex')}"
                )

    if not summary.att_events:
        connect_rows = run_tshark(
            tshark,
            capture,
            WIRESHARK_FILTERS["le_connect"],
            ["frame.number", "bthci_cmd.opcode", "bthci_evt.le_meta_subevent"],
        )
        if connect_rows:
            summary.notes.append(
                f"No ATT payloads, but {len(connect_rows)} LE connect command/complete events — "
                "connection may have failed or ATT was encrypted/not logged."
            )
        else:
            summary.notes.append(
                "No ATT and no LE Create Connection / Connection Complete. "
                "This capture is scan-only: enable snoop before the transfer, keep Bluetooth on, "
                "open medi.connect while the cuff advertises, then pull the log."
            )

    sig_hits = [SIG_UUIDS[u] for u in summary.discovered_uuids if u in SIG_UUIDS]
    if sig_hits:
        summary.notes.append(f"Standard SIG services/characteristics seen: {', '.join(sorted(set(sig_hits)))}")
    elif summary.att_events:
        summary.notes.append("No standard Blood Pressure / Battery UUIDs in ATT dissector output; may be proprietary or handle-only.")

    return summary


def print_manual_guide(capture: Path, pairing_mode: bool) -> None:
    print("tshark/Wireshark not found. Manual analysis steps:\n")
    print(f"1. Open in Wireshark: {capture}")
    print("2. Useful display filters:")
    for key, filt in WIRESHARK_FILTERS.items():
        print(f"   - {key}: {filt}")
    print("3. For bonded transfer:")
    print("   - Find when cuff starts advertising (btle.advertising_header)")
    print("   - Follow connection; list ATT Write Request / Write Command (trigger)")
    print("   - List Handle Value Notification / Indication (measurement payloads)")
    print("4. For fresh pairing (--pairing):")
    print("   - Filter btsmp; confirm Passkey Entry and 6-digit flow")
    print("   - Then Read By Group Type / Read By Type for GATT discovery")
    print("5. Export interesting ATT values to docs/captures/exports/")
    print("6. Fill ground_truth_*.md and update docs/protocol.md")


def write_exports(summary: CaptureSummary, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(summary.source_file).stem

    json_path = out_dir / f"{stem}_summary.json"
    json_path.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")

    if summary.att_events:
        csv_path = out_dir / f"{stem}_att.csv"
        keys: list[str] = []
        for evt in summary.att_events:
            for k in evt:
                if k not in keys:
                    keys.append(k)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summary.att_events)

    bpm_rows = [e for e in summary.att_events if e.get("sys")]
    if bpm_rows:
        bpm_path = out_dir / f"{stem}_bpm.csv"
        bpm_keys = ["frame", "time_s", "handle", "sys", "dia", "pulse", "timestamp", "user", "status"]
        with bpm_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=bpm_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(bpm_rows)

    if summary.advertisements:
        adv_csv = out_dir / f"{stem}_adv.csv"
        with adv_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary.advertisements[0].keys())
            writer.writeheader()
            writer.writerows(summary.advertisements)

    md_path = out_dir / f"{stem}_notes.md"
    lines = [
        f"# Analysis notes: {stem}",
        "",
        f"- Source: `{summary.source_file}`",
        f"- Analyzed: {summary.analyzed_at}",
        f"- Pairing mode: {summary.pairing_mode}",
        "",
        "## Highlights",
        "",
    ]
    for note in summary.notes:
        lines.append(f"- {note}")
    lines.extend(["", "## Discovered UUIDs", ""])
    for uuid in summary.discovered_uuids:
        name = SIG_UUIDS.get(uuid, "")
        lines.append(f"- `{uuid}` {name}".rstrip())
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    if summary.att_events:
        print(f"Wrote {csv_path}")
    if bpm_rows:
        print(f"Wrote {out_dir / f'{stem}_bpm.csv'}")
    if summary.advertisements:
        print(f"Wrote {out_dir / f'{stem}_adv.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Path to btsnoop / btsnoop_hci.log file")
    parser.add_argument(
        "--pairing",
        action="store_true",
        help="Expect fresh pairing capture (emphasize SMP + discovery)",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("docs/captures/exports"),
        help="Directory for JSON/CSV/Markdown exports",
    )
    args = parser.parse_args()

    if not args.capture.is_file():
        print(f"Capture not found: {args.capture}", file=sys.stderr)
        print("\nRun a capture first — see docs/captures/README.md", file=sys.stderr)
        return 1

    tshark = find_tshark()
    if not tshark:
        print_manual_guide(args.capture, args.pairing)
        return 2

    print(f"Using tshark: {tshark}")
    summary = analyze_with_tshark(tshark, args.capture, args.pairing)
    write_exports(summary, args.export_dir)

    print("\n--- Summary ---")
    print(f"ATT events: {len(summary.att_events)}")
    print(f"SMP events: {len(summary.smp_events)}")
    print(f"Advertisements (sampled): {len(summary.advertisements)}")
    for note in summary.notes[:20]:
        print(f"  * {note}")
    if len(summary.notes) > 20:
        print(f"  ... and {len(summary.notes) - 20} more (see export notes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
