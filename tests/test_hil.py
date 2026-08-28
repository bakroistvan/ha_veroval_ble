"""Hardware-in-the-loop tests (require paired BPU26 and VEROVAL_HIL=1)."""

from __future__ import annotations

import asyncio
import os

import pytest

from tests._veroval_loader import load_veroval_ble

pytestmark = pytest.mark.skipif(
    os.environ.get("VEROVAL_HIL") != "1",
    reason="Set VEROVAL_HIL=1 to run hardware tests",
)


def _cuff_user() -> int:
    raw = os.environ.get("VEROVAL_HIL_USER", "1")
    user = int(raw)
    if user not in (1, 2):
        raise ValueError("VEROVAL_HIL_USER must be 1 or 2")
    return user


@pytest.mark.hardware
def test_scan_finds_bpu26() -> None:
    """Scan should discover an advertising BPU26."""
    _, _, client = load_veroval_ble()
    timeout = float(os.environ.get("VEROVAL_HIL_SCAN_TIMEOUT", "20"))
    devices = asyncio.run(client.scan_bpu26(timeout))
    assert devices, "No BPU26 found — press User 1 or User 2 and retry"


@pytest.mark.hardware
def test_dump_latest_for_user() -> None:
    """Drain dump and validate latest reading for the configured cuff user."""
    _, parser, client = load_veroval_ble()
    cuff_user = _cuff_user()
    timeout = float(os.environ.get("VEROVAL_HIL_SCAN_TIMEOUT", "20"))

    devices = asyncio.run(client.scan_bpu26(timeout))
    assert devices, "No BPU26 found — press User 1 or User 2 and retry"

    result = asyncio.run(client.dump_latest(devices[0], cuff_user))
    assert not result.auth_error, client.AUTH_HINT
    assert not result.missing_characteristic
    assert result.records, "Expected non-empty indication dump"
    assert result.selected is not None, f"No records for cuff User {cuff_user}"

    selected = result.selected
    expected_ble = parser.cuff_user_to_ble_id(cuff_user)
    assert selected.user_id == expected_ble
    assert 40 <= selected.systolic <= 250
    assert 20 <= selected.diastolic <= 200
    assert 20 <= selected.pulse <= 250
