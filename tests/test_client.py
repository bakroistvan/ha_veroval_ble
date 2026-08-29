"""Unit tests for BLE auth-error matching (no Home Assistant)."""

from __future__ import annotations

import pytest

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
