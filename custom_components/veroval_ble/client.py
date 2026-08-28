"""Home Assistant-free BLE client for Veroval BPU26 (scan + 0x2A35 dump)."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass

from bleak import BleakError, BleakScanner
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    CHARACTERISTIC_BLOOD_PRESSURE,
    DUMP_IDLE_SECONDS,
    DUMP_TIMEOUT_SECONDS,
    LOCAL_NAME,
    MANUFACTURER_ID,
)
from .parser import (
    BloodPressureMeasurement,
    ble_id_to_cuff_user,
    cuff_user_to_ble_id,
    parse_bpm_indication,
    select_latest_for_user,
)

_LOGGER = logging.getLogger(__name__)

AUTH_HINT = (
    "Pair the cuff again from Home Assistant setup (host Bluetooth adapter). "
    "Unpair medi.connect and other phones first. "
    "ESPHome Bluetooth Proxy cannot enter the cuff PIN."
)


def is_auth_error(err: BaseException) -> bool:
    """Return True if the BLE error looks like a missing or broken OS bond."""
    text = str(err).lower()
    return any(
        token in text
        for token in (
            "auth",
            "pair",
            "encrypt",
            "not permitted",
            "insufficient",
            "not authorized",
        )
    )


def is_bpu26_advertisement(device: BLEDevice, advertisement_data: object) -> bool:
    """Return True if this scan result looks like a Veroval BPU26."""
    name = device.name or ""
    if name == LOCAL_NAME or name.startswith(LOCAL_NAME):
        return True
    manufacturer_data = getattr(advertisement_data, "manufacturer_data", None) or {}
    return MANUFACTURER_ID in manufacturer_data


@dataclass(frozen=True)
class DumpResult:
    """Outcome of one connect → drain → select cycle."""

    records: list[BloodPressureMeasurement]
    selected: BloodPressureMeasurement | None
    counts: dict[int, int]
    auth_error: bool = False
    missing_characteristic: bool = False


async def scan_bpu26(timeout: float = 20.0) -> list[BLEDevice]:
    """Scan for connectable BPU26 cuffs for up to *timeout* seconds."""
    seen: dict[str, BLEDevice] = {}

    def _detection_callback(device: BLEDevice, advertisement_data: object) -> None:
        if not is_bpu26_advertisement(device, advertisement_data):
            return
        key = device.address.lower()
        if key not in seen:
            _LOGGER.debug(
                "Found %s (%s) rssi=%s",
                device.name or LOCAL_NAME,
                device.address,
                getattr(advertisement_data, "rssi", "?"),
            )
        seen[key] = device

    scanner = BleakScanner(detection_callback=_detection_callback)
    await scanner.start()
    try:
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()

    return list(seen.values())


async def drain_indications(
    client: BleakClientWithServiceCache,
) -> list[BloodPressureMeasurement]:
    """Collect all 0x2A35 indications until idle or timeout."""
    records: list[BloodPressureMeasurement] = []
    loop = asyncio.get_running_loop()

    def _on_payload(payload: bytes) -> None:
        try:
            records.append(parse_bpm_indication(payload))
        except ValueError:
            _LOGGER.warning(
                "Failed to parse BPM indication (%s bytes): %s",
                len(payload),
                payload.hex(),
            )

    def _handler(_sender: object, data: bytearray) -> None:
        loop.call_soon_threadsafe(_on_payload, bytes(data))

    _LOGGER.debug("start_notify %s", CHARACTERISTIC_BLOOD_PRESSURE)
    await client.start_notify(CHARACTERISTIC_BLOOD_PRESSURE, _handler)
    try:
        start = loop.time()
        last_count = 0
        last_change = start
        while True:
            now = loop.time()
            elapsed = now - start
            if elapsed >= DUMP_TIMEOUT_SECONDS:
                if not records:
                    _LOGGER.warning(
                        "Timed out waiting for BPM indications after %ss",
                        DUMP_TIMEOUT_SECONDS,
                    )
                else:
                    _LOGGER.warning(
                        "Indication dump hit %ss ceiling (%s records)",
                        DUMP_TIMEOUT_SECONDS,
                        len(records),
                    )
                break
            if len(records) != last_count:
                last_count = len(records)
                last_change = now
            elif last_count > 0 and (now - last_change) >= DUMP_IDLE_SECONDS:
                break
            await asyncio.sleep(0.05)
    finally:
        await _safe_stop_notify(client)

    _LOGGER.debug("Dump finished with %s records", len(records))
    return records


async def _safe_stop_notify(client: BleakClientWithServiceCache) -> None:
    """Unsubscribe from BPM indications; cuff often disconnects before this runs."""
    if not client.is_connected:
        _LOGGER.debug(
            "Cuff disconnected after dump; skipping stop_notify on %s",
            CHARACTERISTIC_BLOOD_PRESSURE,
        )
        return
    try:
        await client.stop_notify(CHARACTERISTIC_BLOOD_PRESSURE)
    except BleakError as err:
        if not client.is_connected or "not connected" in str(err).lower():
            _LOGGER.debug(
                "Cuff disconnected during stop_notify (expected after dump)"
            )
            return
        _LOGGER.debug("stop_notify failed: %s", err, exc_info=True)


async def dump_records(device: BLEDevice) -> DumpResult:
    """Connect, drain all BPM indications, disconnect. No slot filter."""
    _LOGGER.debug("Connecting to %s", device.address)
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            device.name or device.address,
        )
    except (BleakError, TimeoutError) as err:
        if is_auth_error(err):
            _LOGGER.warning(
                "Authentication failed for %s. %s (%s)",
                device.address,
                AUTH_HINT,
                err,
            )
            return DumpResult([], None, {}, auth_error=True)
        _LOGGER.warning("Failed to connect to %s: %s", device.address, err)
        return DumpResult([], None, {})

    records: list[BloodPressureMeasurement] = []
    missing = False
    try:
        if client.services.get_characteristic(CHARACTERISTIC_BLOOD_PRESSURE) is None:
            _LOGGER.warning(
                "Missing Blood Pressure Measurement characteristic 0x2A35 on %s",
                device.address,
            )
            missing = True
        else:
            records = await drain_indications(client)
    except (BleakError, TimeoutError) as err:
        if is_auth_error(err):
            _LOGGER.warning(
                "Authentication failed while dumping %s. %s (%s)",
                device.address,
                AUTH_HINT,
                err,
            )
            return DumpResult(records, None, dict(Counter()), auth_error=True)
        _LOGGER.warning("GATT error while dumping %s: %s", device.address, err)
    finally:
        await client.disconnect()
        _LOGGER.debug("Disconnected from %s", device.address)

    counts = dict(Counter(record.user_id for record in records))
    _LOGGER.debug("Dump count=%s per user_id=%s", len(records), counts)
    return DumpResult(records, None, counts, missing_characteristic=missing)


async def dump_latest(device: BLEDevice, cuff_user: int) -> DumpResult:
    """Drain dump and return newest record for *cuff_user* (1 or 2)."""
    ble_user_id = cuff_user_to_ble_id(cuff_user)
    result = await dump_records(device)
    if result.auth_error or result.missing_characteristic or not result.records:
        return result

    selected = select_latest_for_user(result.records, ble_user_id)
    if selected is None:
        _LOGGER.warning(
            "No records for cuff User %s / BLE user_id %s on %s (dump had %s records)",
            cuff_user,
            ble_user_id,
            device.address,
            len(result.records),
        )
        return DumpResult(result.records, None, result.counts)

    _LOGGER.debug(
        "Selected hex=%s sys=%s dia=%s pulse=%s ts=%s user_id=%s status=%s",
        selected.raw.hex(),
        selected.systolic,
        selected.diastolic,
        selected.pulse,
        selected.timestamp.isoformat(),
        selected.user_id,
        selected.status,
    )
    _LOGGER.info(
        "Latest reading User %s: systolic=%.0f mmHg diastolic=%.0f mmHg pulse=%.0f bpm",
        ble_id_to_cuff_user(ble_user_id),
        selected.systolic,
        selected.diastolic,
        selected.pulse,
    )
    return DumpResult(result.records, selected, result.counts)
