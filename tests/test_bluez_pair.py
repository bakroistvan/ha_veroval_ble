"""Unit tests for BlueZ pairing error formatting (no D-Bus / Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

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
is_local_bluez_device = _bluez_pair.is_local_bluez_device
bluez_path_from_device = _bluez_pair.bluez_path_from_device
rssi_from_device_props = _bluez_pair.rssi_from_device_props
connected_from_device_props = _bluez_pair.connected_from_device_props


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


def test_rssi_from_device_props_missing_while_asleep() -> None:
    assert rssi_from_device_props({}) is None
    assert rssi_from_device_props({"Name": "BPU26"}) is None


def test_rssi_from_device_props_unwraps_variant() -> None:
    class _Variant:
        def __init__(self, value: object) -> None:
            self.value = value

    assert rssi_from_device_props({"RSSI": _Variant(-54)}) == -54
    assert rssi_from_device_props({"RSSI": -65}) == -65
    assert rssi_from_device_props({"RSSI": True}) is None


def test_connected_from_device_props_matches_bluetoothctl() -> None:
    class _Variant:
        def __init__(self, value: object) -> None:
            self.value = value

    assert connected_from_device_props({}) is False
    assert connected_from_device_props({"Connected": False}) is False
    assert connected_from_device_props({"Connected": True}) is True
    assert connected_from_device_props({"Connected": _Variant(True)}) is True


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


def test_display_passkey_and_pin_code_do_not_log_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DisplayPasskey / DisplayPinCode must not write the pairing secret to logs."""
    pytest.importorskip("dbus_fast")
    from dbus_fast import DBusError

    session = _bluez_pair.BlueZPairSession("aa:bb:cc:dd:ee:ff")
    agent = _bluez_pair._build_passkey_agent(session)
    device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    # Distinctive 6-digit pairing secret used only as a method argument.
    secret = "246813"
    entered = 4

    with caplog.at_level(logging.DEBUG, logger="veroval_ble_bluez_pair"):
        agent.DisplayPasskey(device, int(secret), entered)
        agent.DisplayPinCode(device, secret)
        try:
            agent.RequestConfirmation(device, int(secret))
        except DBusError:
            pass

    leaked = secret in caplog.text
    assert leaked is False
    assert "DisplayPasskey" in caplog.text
    assert f"entered={entered}" in caplog.text
    assert "DisplayPinCode" in caplog.text
    assert "RequestConfirmation" in caplog.text


def test_is_local_bluez_device_accepts_hci_path() -> None:
    device = SimpleNamespace(
        details={"path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"}
    )
    assert is_local_bluez_device(device) is True
    assert (
        bluez_path_from_device(device) == "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    )


def test_is_local_bluez_device_nested_path_not_props() -> None:
    nested = SimpleNamespace(
        details={"details": {"path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"}}
    )
    assert is_local_bluez_device(nested) is True
    assert is_local_bluez_device(
        SimpleNamespace(details={"props": {"Address": "AA:BB:CC:DD:EE:FF"}})
    ) is False
    assert is_local_bluez_device(
        SimpleNamespace(
            details={"props": {"path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"}}
        )
    ) is False


def test_is_local_bluez_device_false_for_empty_and_proxy() -> None:
    assert is_local_bluez_device(SimpleNamespace(details={})) is False
    assert is_local_bluez_device(SimpleNamespace(details=None)) is False
    assert is_local_bluez_device(
        SimpleNamespace(details={"source": "esphome-kitchen"})
    ) is False


def test_device_resolve_retry_window_is_about_eight_seconds() -> None:
    assert (
        _bluez_pair.DEVICE_RESOLVE_ATTEMPTS
        * _bluez_pair.DEVICE_RESOLVE_INTERVAL_SECONDS
        >= 8
    )


def test_start_discovery_noop_when_unsupported() -> None:
    original = _bluez_pair.is_bluez_pairing_supported
    _bluez_pair.is_bluez_pairing_supported = lambda: False
    try:

        async def _run() -> None:
            stopper = await _bluez_pair.async_start_host_adapter_discovery()
            await stopper()

        asyncio.run(_run())
    finally:
        _bluez_pair.is_bluez_pairing_supported = original
