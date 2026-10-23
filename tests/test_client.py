"""Unit tests for BLE auth-error matching (no Home Assistant)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("bleak")
pytest.importorskip("bleak_retry_connector")

from tests._veroval_loader import load_client_module

_client = load_client_module()
AUTH_HINT = _client.AUTH_HINT
is_auth_error = _client.is_auth_error

# Non-auth BLE / OS errors that previously matched via "pair" / "insufficient" / "auth".
FALSE_POSITIVES = (
    "Could not repair HCI socket",
    "repair",
    "Device is pairable",
    "Insufficient Resources",
    "insufficient buffer",
    "Failed to connect: Protocol not available",
    "Device disconnected",
    "Not connected",
    "already encrypted",
    "link is already encrypted",
)

# Missing/broken OS bond and GATT security failures that should still match.
TRUE_POSITIVES = (
    "not paired",
    "org.bluez.Error.NotPaired",
    "NotPaired",
    "Insufficient Authentication",
    "Insufficient Encryption",
    "Authentication Failed",
    "org.bluez.Error.AuthenticationFailed",
    "not authorized",
    "NotAuthorized",
    "not permitted",
    "not encrypted",
)


@pytest.mark.parametrize("message", FALSE_POSITIVES)
def test_is_auth_error_false_positives(message: str) -> None:
    assert is_auth_error(Exception(message)) is False


@pytest.mark.parametrize("message", TRUE_POSITIVES)
def test_is_auth_error_true_positives(message: str) -> None:
    assert is_auth_error(Exception(message)) is True


def test_auth_hint_mentions_setup_medi_connect_and_proxy() -> None:
    lower = AUTH_HINT.lower()
    assert "setup" in lower
    assert "medi.connect" in lower
    assert "bluetooth proxy" in lower


def test_dump_latest_logs_reading_at_debug_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blood-pressure numbers stay out of INFO logs that people paste publicly."""
    measurement = _client.BloodPressureMeasurement(
        flags=0x1E,
        systolic=137.0,
        diastolic=86.0,
        mean_arterial=103.0,
        timestamp=datetime(2024, 6, 1, 8, 30, 0),
        pulse=64.0,
        user_id=0,
        status=0,
        raw=bytes.fromhex("deadbeef"),
    )

    async def _fake_dump(_device: object) -> object:
        return _client.DumpResult([measurement], None, {0: 1})

    monkeypatch.setattr(_client, "dump_records", _fake_dump)
    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")

    with caplog.at_level(logging.DEBUG, logger=_client.__name__):
        result = asyncio.run(_client.dump_latest(device, 1))

    assert result.selected is measurement
    records = [record for record in caplog.records if record.name == _client.__name__]
    info = [record.getMessage() for record in records if record.levelno >= logging.INFO]
    debug = [record.getMessage() for record in records if record.levelno == logging.DEBUG]
    reading = "systolic=137 mmHg diastolic=86 mmHg pulse=64 bpm"
    assert all(
        token not in message
        for message in info
        for token in ("137", "86", "64", "deadbeef", "mmHg")
    )
    assert any(reading in message for message in debug)
    assert any("deadbeef" in message for message in debug)
