#!/usr/bin/env python3
"""Hardware-in-the-loop dump for Veroval BPU26 without Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._veroval_loader import load_veroval_ble  # noqa: E402

_LOGGER = logging.getLogger("hil_dump")


def _format_measurement(record: object, ble_id_to_cuff_user: object) -> str:
    return (
        f"  User {ble_id_to_cuff_user(record.user_id)} | "
        f"sys={record.systolic:.0f} dia={record.diastolic:.0f} "
        f"pulse={record.pulse:.0f} bpm | "
        f"time={record.timestamp.isoformat()} | "
        f"status=0x{record.status:04X} | "
        f"hex={record.raw.hex()}"
    )


async def _run(args: argparse.Namespace) -> int:
    _, parser, client = load_veroval_ble()

    if args.address:
        from bleak.backends.device import BLEDevice

        device = BLEDevice(args.address, args.address, {}, -100, -100, False)
        devices = [device]
        _LOGGER.info("Using address %s (no scan)", args.address)
    else:
        _LOGGER.info("Scanning up to %.0fs for BPU26...", args.timeout)
        devices = await client.scan_bpu26(args.timeout)
        if not devices:
            print(
                "No BPU26 found. Press User 1 or User 2 on the cuff, then retry.",
                file=sys.stderr,
            )
            return 2
        if len(devices) > 1:
            _LOGGER.warning("Multiple BPU26 devices; using first: %s", devices[0].address)
        device = devices[0]
        _LOGGER.info("Found %s (%s)", device.name or "BPU26", device.address)

    result = await client.dump_latest(device, args.user)
    if result.auth_error:
        print(client.AUTH_HINT, file=sys.stderr)
        return 3
    if result.missing_characteristic:
        print("Blood Pressure characteristic 0x2A35 missing on device.", file=sys.stderr)
        return 4
    if not result.records:
        print("Connected but received no BPM indications.", file=sys.stderr)
        return 5

    print(f"Dump: {len(result.records)} records")
    for ble_id, count in sorted(result.counts.items()):
        cuff = parser.ble_id_to_cuff_user(ble_id)
        print(f"  User {cuff} (BLE user_id {ble_id}): {count}")

    if result.selected is None:
        print(f"No record for cuff User {args.user}.", file=sys.stderr)
        return 6

    print(f"Latest for User {args.user}:")
    print(_format_measurement(result.selected, parser.ble_id_to_cuff_user))
    return 0


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="Scan, connect, and dump Veroval BPU26 BPM history (no Home Assistant).",
    )
    ap.add_argument("--user", type=int, choices=[1, 2], required=True, help="Cuff user slot")
    ap.add_argument("--address", help="Skip scan; connect to this BLE address")
    ap.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Scan duration in seconds (default: 20)",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for name in ("bleak", "bleak_retry_connector"):
        logging.getLogger(name).setLevel(logging.WARNING)

    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
