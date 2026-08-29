"""Unit tests for BlueZ pairing error formatting (no D-Bus / Home Assistant)."""

from __future__ import annotations

import importlib.util
import logging
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
