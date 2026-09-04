"""Unit tests for VerovalBleSettings / options merge."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PKG = "veroval_ble_settings_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


def _load_const() -> ModuleType:
    full = f"{_PKG}.const"
    if full in sys.modules:
        return sys.modules[full]
    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    spec = importlib.util.spec_from_file_location(full, _ROOT / "const.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load_const()
VerovalBleSettings = _const.VerovalBleSettings
settings_from_options = _const.settings_from_options
options_schema_defaults = _const.options_schema_defaults
CONF_PHONE_GRACE_SECONDS = _const.CONF_PHONE_GRACE_SECONDS
PHONE_GRACE_SECONDS = _const.PHONE_GRACE_SECONDS
AD_SILENCE_NEW_WINDOW_SECONDS = _const.AD_SILENCE_NEW_WINDOW_SECONDS
CUFF_ADVERTISE_SECONDS = _const.CUFF_ADVERTISE_SECONDS
POLL_WINDOW_GAP_SECONDS = _const.POLL_WINDOW_GAP_SECONDS
DUMP_TIMEOUT_SECONDS = _const.DUMP_TIMEOUT_SECONDS
DUMP_IDLE_SECONDS = _const.DUMP_IDLE_SECONDS


def test_settings_from_options_empty_uses_defaults() -> None:
    settings = settings_from_options({})
    assert settings == VerovalBleSettings()
    assert settings.phone_grace_seconds == PHONE_GRACE_SECONDS
    assert settings.ad_silence_seconds == AD_SILENCE_NEW_WINDOW_SECONDS
    assert settings.advertise_linger_seconds == CUFF_ADVERTISE_SECONDS
    assert settings.poll_window_gap_seconds == POLL_WINDOW_GAP_SECONDS
    assert settings.dump_timeout_seconds == DUMP_TIMEOUT_SECONDS
    assert settings.dump_idle_seconds == DUMP_IDLE_SECONDS


def test_settings_from_options_none_uses_defaults() -> None:
    assert settings_from_options(None) == VerovalBleSettings()


def test_settings_from_options_partial_merge() -> None:
    settings = settings_from_options({CONF_PHONE_GRACE_SECONDS: 0})
    assert settings.phone_grace_seconds == 0
    assert settings.ad_silence_seconds == AD_SILENCE_NEW_WINDOW_SECONDS


def test_settings_from_options_clamps_out_of_range() -> None:
    settings = settings_from_options(
        {
            CONF_PHONE_GRACE_SECONDS: 999,
            "ad_silence_seconds": 1,
            "dump_idle_seconds": 0,
        }
    )
    assert settings.phone_grace_seconds == 60
    assert settings.ad_silence_seconds == 10
    assert settings.dump_idle_seconds == 1.0


def test_settings_from_options_invalid_falls_back() -> None:
    settings = settings_from_options({CONF_PHONE_GRACE_SECONDS: "nope"})
    assert settings.phone_grace_seconds == PHONE_GRACE_SECONDS


def test_options_schema_defaults_includes_all_keys() -> None:
    defaults = options_schema_defaults({})
    assert set(defaults) == {
        "phone_grace_seconds",
        "ad_silence_seconds",
        "advertise_linger_seconds",
        "poll_window_gap_seconds",
        "dump_timeout_seconds",
        "dump_idle_seconds",
    }
    assert defaults["phone_grace_seconds"] == PHONE_GRACE_SECONDS
