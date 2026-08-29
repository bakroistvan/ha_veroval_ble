"""Unit tests for BlueZ pairing error formatting (no D-Bus / Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "veroval_ble"
    / "bluez_pair.py"
)
_SPEC = importlib.util.spec_from_file_location("veroval_ble_bluez_pair", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_bluez_pair = importlib.util.module_from_spec(_SPEC)
sys.modules["veroval_ble_bluez_pair"] = _bluez_pair
_SPEC.loader.exec_module(_bluez_pair)

BlueZPairSession = _bluez_pair.BlueZPairSession
format_device_snapshot = _bluez_pair.format_device_snapshot
format_pairing_error = _bluez_pair.format_pairing_error
PairingFailedError = _bluez_pair.PairingFailedError


class _FakeDBusError(Exception):
    def __init__(self, type_: str, text: str) -> None:
        super().__init__(text)
        self.type = type_
        self.text = text


def test_format_pairing_error_includes_bluez_type_and_hint() -> None:
    err = _FakeDBusError(
        "org.bluez.Error.AuthenticationFailed", "Authentication Failed"
    )
    text = format_pairing_error(err)
    assert "org.bluez.Error.AuthenticationFailed" in text
    assert "Authentication Failed" in text
    assert "another device still bonded" in text


def test_format_pairing_error_walks_cause_chain() -> None:
    dbus_err = _FakeDBusError(
        "org.bluez.Error.ConnectionAttemptFailed", "Page Timeout"
    )
    wrapped = PairingFailedError(str(dbus_err))
    wrapped.__cause__ = dbus_err
    text = format_pairing_error(wrapped)
    assert "org.bluez.Error.ConnectionAttemptFailed" in text
    assert "Bluetooth flashes" in text


def test_format_pairing_error_timeout_message() -> None:
    err = PairingFailedError("Pairing timed out waiting for the cuff PIN")
    text = format_pairing_error(err)
    assert "timed out waiting for the cuff PIN" in text


def test_format_device_snapshot_unwraps_variants() -> None:
    class _Variant:
        def __init__(self, value: object) -> None:
            self.value = value

    snapshot = format_device_snapshot(
        {
            "Address": _Variant("AA:BB:CC:DD:EE:FF"),
            "Paired": _Variant(False),
            "Connected": _Variant(True),
            "RSSI": _Variant(-67),
            "UUIDs": _Variant(["ignored"]),
        }
    )
    assert "Address='AA:BB:CC:DD:EE:FF'" in snapshot
    assert "Paired=False" in snapshot
    assert "Connected=True" in snapshot
    assert "RSSI=-67" in snapshot
    assert "UUIDs" not in snapshot


def test_provide_passkey_second_call_does_not_raise() -> None:
    async def _run() -> None:
        session = BlueZPairSession("aa:bb:cc:dd:ee:ff")
        session._passkey_future = asyncio.get_running_loop().create_future()
        session.provide_passkey("123456")
        session.provide_passkey("654321")
        assert session._passkey_future.result() == 123456

    asyncio.run(_run())


def test_provide_passkey_cancelled_future_fails() -> None:
    async def _run() -> None:
        session = BlueZPairSession("aa:bb:cc:dd:ee:ff")
        future = asyncio.get_running_loop().create_future()
        future.cancel()
        session._passkey_future = future
        with pytest.raises(PairingFailedError, match="No passkey request is pending"):
            session.provide_passkey("123456")

    asyncio.run(_run())


def test_provide_passkey_exception_future_does_not_hide_failure() -> None:
    async def _run() -> None:
        session = BlueZPairSession("aa:bb:cc:dd:ee:ff")
        future = asyncio.get_running_loop().create_future()
        future.set_exception(RuntimeError("prior pairing failure"))
        session._passkey_future = future
        with pytest.raises(PairingFailedError, match="prior pairing failure"):
            session.provide_passkey("123456")

    asyncio.run(_run())


def test_wait_for_passkey_or_done_does_not_leave_pending_cancelled_waiter() -> None:
    async def _run() -> None:
        session = BlueZPairSession("aa:bb:cc:dd:ee:ff")

        async def _pair() -> None:
            return None

        session._pair_task = asyncio.create_task(_pair())
        result = await session.wait_for_passkey_or_done()
        assert result == "done"
        pending_waiters = [
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "veroval_ble_passkey_wait" and not task.done()
        ]
        assert pending_waiters == []

    asyncio.run(_run())
