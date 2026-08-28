"""SIG Blood Pressure Measurement (0x2A35) parser.

Decodes IEEE 11073 SFLOAT fields from Veroval compact+ (BPU 26) indications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STATUS_IRREGULAR_PULSE = 0x0004
CUFF_USER_1 = 1  # cuff button
CUFF_USER_2 = 2
BLE_USER_1 = 0  # payload user_id
BLE_USER_2 = 1

_BPM_MIN_LENGTH = 19

# IEEE 11073-20601 SFLOAT specials apply when exponent is 0.
_SFLOAT_NAN = 2047
_SFLOAT_NRES = -2048
_SFLOAT_POS_INF = 2046
_SFLOAT_NEG_INF = -2047
_SFLOAT_RESERVED = -2046


def cuff_user_to_ble_id(cuff_user: int) -> int:
    """Map cuff button User 1/2 to BLE payload user_id 0/1."""
    if cuff_user == CUFF_USER_1:
        return BLE_USER_1
    if cuff_user == CUFF_USER_2:
        return BLE_USER_2
    raise ValueError(f"unknown cuff user {cuff_user}")


def ble_id_to_cuff_user(ble_id: int) -> int:
    """Map BLE payload user_id 0/1 to cuff button User 1/2."""
    if ble_id == BLE_USER_1:
        return CUFF_USER_1
    if ble_id == BLE_USER_2:
        return CUFF_USER_2
    raise ValueError(f"unknown BLE user id {ble_id}")


def decode_sfloat(data: bytes, offset: int = 0) -> float:
    """Decode a little-endian IEEE 11073 16-bit SFLOAT at *offset*.

    Layout: 12-bit signed mantissa, 4-bit signed exponent.
    Value = mantissa * 10 ** exponent.
    """
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("SFLOAT truncated")
    raw = int.from_bytes(data[offset : offset + 2], "little")
    mantissa = raw & 0x0FFF
    exponent = (raw >> 12) & 0x0F
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    if exponent >= 0x08:
        exponent -= 0x10
    if exponent == 0:
        if mantissa in (_SFLOAT_NAN, _SFLOAT_NRES, _SFLOAT_RESERVED):
            return float("nan")
        if mantissa == _SFLOAT_POS_INF:
            return float("inf")
        if mantissa == _SFLOAT_NEG_INF:
            return float("-inf")
    return float(mantissa * (10**exponent))


@dataclass(frozen=True)
class BloodPressureMeasurement:
    flags: int
    systolic: float
    diastolic: float
    mean_arterial: float
    timestamp: datetime  # naive local cuff clock
    pulse: float
    user_id: int
    status: int
    raw: bytes

    @property
    def irregular_pulse(self) -> bool:
        return bool(self.status & STATUS_IRREGULAR_PULSE)


def parse_bpm_indication(data: bytes) -> BloodPressureMeasurement:
    """Parse a SIG Blood Pressure Measurement indication (flags 0x1E layout)."""
    if len(data) < _BPM_MIN_LENGTH:
        raise ValueError(
            f"Blood Pressure Measurement indication too short: "
            f"{len(data)} bytes (need {_BPM_MIN_LENGTH})"
        )
    year = int.from_bytes(data[7:9], "little")
    return BloodPressureMeasurement(
        flags=data[0],
        systolic=decode_sfloat(data, 1),
        diastolic=decode_sfloat(data, 3),
        mean_arterial=decode_sfloat(data, 5),
        timestamp=datetime(
            year, data[9], data[10], data[11], data[12], data[13]
        ),
        pulse=decode_sfloat(data, 14),
        user_id=data[16],
        status=int.from_bytes(data[17:19], "little"),
        raw=bytes(data),
    )


def select_latest_for_user(
    records: list[BloodPressureMeasurement], ble_user_id: int
) -> BloodPressureMeasurement | None:
    """Return the newest-timestamp record for *ble_user_id*, or None."""
    matching = [r for r in records if r.user_id == ble_user_id]
    if not matching:
        return None
    return max(matching, key=lambda r: r.timestamp)
