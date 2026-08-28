"""Unit tests for SIG Blood Pressure Measurement (0x2A35) parsing."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from math import isnan
from pathlib import Path

import pytest

_PARSER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "veroval_ble"
    / "parser.py"
)
_SPEC = importlib.util.spec_from_file_location("veroval_ble_parser", _PARSER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_parser = importlib.util.module_from_spec(_SPEC)
sys.modules["veroval_ble_parser"] = _parser
_SPEC.loader.exec_module(_parser)

BLE_USER_1 = _parser.BLE_USER_1
BLE_USER_2 = _parser.BLE_USER_2
CUFF_USER_1 = _parser.CUFF_USER_1
CUFF_USER_2 = _parser.CUFF_USER_2
STATUS_IRREGULAR_PULSE = _parser.STATUS_IRREGULAR_PULSE
ble_id_to_cuff_user = _parser.ble_id_to_cuff_user
cuff_user_to_ble_id = _parser.cuff_user_to_ble_id
decode_sfloat = _parser.decode_sfloat
parse_bpm_indication = _parser.parse_bpm_indication
select_latest_for_user = _parser.select_latest_for_user

# Synthetic example payload — 120/80/72, 2024-01-15 12:00:00, user 0 (not from a real capture).
CAPTURE_FIXTURE = bytes.fromhex("1e780050000000e807010f0c00004800000000")


def _sfloat_le(value: int, exponent: int = 0) -> bytes:
    """Encode a 16-bit IEEE 11073 SFLOAT (little-endian)."""
    if exponent < -8 or exponent > 7:
        raise ValueError("exponent out of 4-bit signed range")
    mantissa = value & 0x0FFF
    exp_nibble = exponent & 0x0F
    raw = (exp_nibble << 12) | mantissa
    return raw.to_bytes(2, "little")


def _bpm_payload(
    *,
    flags: int = 0x1E,
    systolic: int = 120,
    diastolic: int = 80,
    map_mmhg: int = 0,
    timestamp: datetime,
    pulse: int = 72,
    user_id: int = 0,
    status: int = 0,
) -> bytes:
    return (
        bytes([flags])
        + _sfloat_le(systolic)
        + _sfloat_le(diastolic)
        + _sfloat_le(map_mmhg)
        + timestamp.year.to_bytes(2, "little")
        + bytes(
            [
                timestamp.month,
                timestamp.day,
                timestamp.hour,
                timestamp.minute,
                timestamp.second,
            ]
        )
        + _sfloat_le(pulse)
        + bytes([user_id])
        + status.to_bytes(2, "little")
    )


def test_capture_fixture_120_80_72() -> None:
    m = parse_bpm_indication(CAPTURE_FIXTURE)
    assert m.flags == 0x1E
    assert m.systolic == 120.0
    assert m.diastolic == 80.0
    assert m.mean_arterial == 0.0
    assert m.pulse == 72.0
    assert m.timestamp == datetime(2024, 1, 15, 12, 0, 0)
    assert m.user_id == BLE_USER_1
    assert m.status == 0
    assert m.irregular_pulse is False
    assert m.raw == CAPTURE_FIXTURE


def test_decode_sfloat_uses_exponent_not_integer_shortcut() -> None:
    """151 with exponent -1 is 15.1, not the raw uint16 61591."""
    encoded = _sfloat_le(151, exponent=-1)
    assert encoded == bytes.fromhex("97f0")
    assert decode_sfloat(encoded) == pytest.approx(15.1)
    integer_shortcut = encoded[0] + encoded[1] * 256
    assert integer_shortcut == 0xF097
    assert decode_sfloat(encoded) != integer_shortcut


def test_decode_sfloat_specials() -> None:
    assert isnan(decode_sfloat(bytes.fromhex("ff07")))  # NaN 0x07FF
    assert decode_sfloat(bytes.fromhex("fe07")) == float("inf")
    assert decode_sfloat(bytes.fromhex("0108")) == float("-inf")


def test_irregular_pulse_status_bit_2() -> None:
    payload = bytearray(CAPTURE_FIXTURE)
    payload[17:19] = STATUS_IRREGULAR_PULSE.to_bytes(2, "little")
    m = parse_bpm_indication(bytes(payload))
    assert m.status == 0x0004
    assert m.irregular_pulse is True


def test_status_reserved_bit_15_is_not_irregular_pulse() -> None:
    payload = bytearray(CAPTURE_FIXTURE)
    payload[17:19] = (0x8000).to_bytes(2, "little")
    m = parse_bpm_indication(bytes(payload))
    assert m.status == 0x8000
    assert m.irregular_pulse is False


def test_parse_bpm_indication_too_short() -> None:
    with pytest.raises(ValueError):
        parse_bpm_indication(CAPTURE_FIXTURE[:-1])
    with pytest.raises(ValueError):
        parse_bpm_indication(b"")


def test_select_latest_for_user_first_packet_is_not_user_2() -> None:
    """Dump order is all user 0 (newest first), then user 1. First packet must
    not win when selecting cuff User 2 (BLE user_id 1).
    """
    user0_newest = parse_bpm_indication(CAPTURE_FIXTURE)
    user0_older = parse_bpm_indication(
        _bpm_payload(
            timestamp=datetime(2024, 1, 15, 11, 55, 0),
            user_id=BLE_USER_1,
            systolic=118,
            diastolic=78,
            pulse=70,
        )
    )
    user1_newest = parse_bpm_indication(
        _bpm_payload(
            timestamp=datetime(2024, 1, 14, 9, 30, 0),
            user_id=BLE_USER_2,
            systolic=130,
            diastolic=85,
            pulse=75,
            status=STATUS_IRREGULAR_PULSE,
        )
    )
    user1_older = parse_bpm_indication(
        _bpm_payload(
            timestamp=datetime(2024, 1, 10, 20, 10, 0),
            user_id=BLE_USER_2,
            systolic=125,
            diastolic=82,
            pulse=68,
        )
    )
    dump = [user0_newest, user0_older, user1_newest, user1_older]

    selected = select_latest_for_user(dump, BLE_USER_2)
    assert selected is not None
    assert selected is not dump[0]
    assert selected.user_id == BLE_USER_2
    assert selected.timestamp == datetime(2024, 1, 14, 9, 30, 0)
    assert selected.systolic == 130.0
    assert selected.irregular_pulse is True

    selected_u1 = select_latest_for_user(dump, BLE_USER_1)
    assert selected_u1 is not None
    assert selected_u1.timestamp == datetime(2024, 1, 15, 12, 0, 0)
    assert selected_u1.systolic == 120.0


def test_select_latest_for_user_empty() -> None:
    assert select_latest_for_user([], BLE_USER_1) is None
    other = parse_bpm_indication(CAPTURE_FIXTURE)
    assert select_latest_for_user([other], BLE_USER_2) is None


def test_cuff_user_ble_id_mapping() -> None:
    assert cuff_user_to_ble_id(CUFF_USER_1) == BLE_USER_1 == 0
    assert cuff_user_to_ble_id(CUFF_USER_2) == BLE_USER_2 == 1
    assert ble_id_to_cuff_user(BLE_USER_1) == CUFF_USER_1 == 1
    assert ble_id_to_cuff_user(BLE_USER_2) == CUFF_USER_2 == 2
