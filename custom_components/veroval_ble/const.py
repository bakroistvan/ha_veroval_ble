"""Constants for Veroval Blood Pressure BLE."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DOMAIN = "veroval_ble"


CONF_CUFF_USER = "cuff_user"
CONF_PIN = "pin"

# Config entry options (Configure UI).
CONF_PHONE_GRACE_SECONDS = "phone_grace_seconds"
CONF_AD_SILENCE_SECONDS = "ad_silence_seconds"
CONF_ADVERTISE_LINGER_SECONDS = "advertise_linger_seconds"
CONF_POLL_WINDOW_GAP_SECONDS = "poll_window_gap_seconds"
CONF_DUMP_TIMEOUT_SECONDS = "dump_timeout_seconds"
CONF_DUMP_IDLE_SECONDS = "dump_idle_seconds"

SERVICE_FORCE_DUMP = "force_dump"

CHARACTERISTIC_BLOOD_PRESSURE = "00002a35-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 2751  # 0x0ABF PAUL HARTMANN AG
LOCAL_NAME = "BPU26"

# Gap between 0x2A35 indications that means the dump is finished.
# Range: 0.5–5 s (BLE jitter vs a slow burst).
DUMP_IDLE_SECONDS = 2.0
# Hard ceiling for one dump. Must be > DUMP_IDLE_SECONDS.
# Range: 10–30 s. PHONE_GRACE_SECONDS + this must fit in the ~120 s flash.
DUMP_TIMEOUT_SECONDS = 30.0
# No *new* live advertisements for this long → next live ad is a new GATT window.
# Also the coordinator live-ad max age (older cache is ignored). This is not
# how long the Advertising sensor stays on. HA often delivers only one callback
# (or replays the same scanner stamp) for the whole flash.
# Range: 10–40 s. Must be > BLUEZ_RSSI_POLL_SECONDS and well below the ~120 s flash.
AD_SILENCE_NEW_WINDOW_SECONDS = 20
# Advertising stays on this long after the *last* live sighting (HA ad or BlueZ
# RSSI). RSSI/ads refresh while the cuff flashes; when it sleeps, the sensor
# turns off after this linger — not after a full 2 min.
# Range: 6–15 s (a few missed RSSI polls). Longer than BLUEZ_RSSI_POLL_SECONDS.
CUFF_ADVERTISE_SECONDS = 10
# Config-flow pairing only: HA scanner cache can outlive BlueZ Device1.
# Range: 10–60 s. Must be well below the ~120 s cuff flash. Not used for dumps.
ADVERTISEMENT_MAX_AGE_SECONDS = 30.0
# Last-resort expiry if HA keeps delivering ads and never marks unavailable.
# Range: 150–240 s. Must be longer than the ~120 s cuff flash so a second dump
# cannot start in the same window. Independent of CUFF_ADVERTISE_SECONDS.
POLL_WINDOW_GAP_SECONDS = 180
# Wait after the first advertisement of a new GATT window so medi.connect
# can take the transfer. Cache consume and force_dump skip this wait.
# Range: 0–60 s (0 = dump immediately). 20 s leaves ~100 s of the flash for
# connect + dump. grace + DUMP_TIMEOUT_SECONDS must stay under the ~120 s flash.
PHONE_GRACE_SECONDS = 20
# BlueZ Device1 RSSI is present only while the cuff is transmitting. Poll it
# when HA's scanner delivers no BluetoothServiceInfoBleak (duplicate filter).
# Range: 1–5 s. Must stay well below AD_SILENCE_NEW_WINDOW_SECONDS.
BLUEZ_RSSI_POLL_SECONDS = 2.0
# Config-flow scan for BPU26. Press User 1/2 so the cuff flashes during this.
# Range: 30–120 s (up to one full flash).
SCAN_TIMEOUT_SECONDS = 60
# Minimum seconds since last_poll before HA will dump again after phone grace.
# Not the cuff flash length. Range: 5–30 s. Must be << PHONE_GRACE_SECONDS.
UPDATE_INTERVAL = 10

# UI / clamp ranges for Configure options.
PHONE_GRACE_RANGE = (0, 60)
AD_SILENCE_RANGE = (10, 40)
ADVERTISE_LINGER_RANGE = (6, 15)
POLL_WINDOW_GAP_RANGE = (120, 300)
DUMP_TIMEOUT_RANGE = (10, 60)
DUMP_IDLE_RANGE = (1, 5)


def normalize_ble_address(address: str) -> str:
    """Return the BLE address in the form HA's callback matcher indexes."""
    return address.upper()


def _clamp_number(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
    *,
    as_int: bool = False,
) -> float | int:
    """Coerce *value* to a number in [minimum, maximum], else *default*."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:  # NaN
        number = float(default)
    number = max(minimum, min(maximum, number))
    if as_int:
        return int(round(number))
    return number


@dataclass(frozen=True, slots=True)
class VerovalBleSettings:
    """Per-cuff dump / advertise timing from entry options (or defaults)."""

    phone_grace_seconds: int = PHONE_GRACE_SECONDS
    ad_silence_seconds: int = AD_SILENCE_NEW_WINDOW_SECONDS
    advertise_linger_seconds: int = CUFF_ADVERTISE_SECONDS
    poll_window_gap_seconds: int = POLL_WINDOW_GAP_SECONDS
    dump_timeout_seconds: float = DUMP_TIMEOUT_SECONDS
    dump_idle_seconds: float = DUMP_IDLE_SECONDS


def settings_from_options(options: Mapping[str, Any] | None) -> VerovalBleSettings:
    """Build settings from config-entry options; missing keys use defaults."""
    opts = options or {}
    return VerovalBleSettings(
        phone_grace_seconds=int(
            _clamp_number(
                opts.get(CONF_PHONE_GRACE_SECONDS, PHONE_GRACE_SECONDS),
                PHONE_GRACE_SECONDS,
                *PHONE_GRACE_RANGE,
                as_int=True,
            )
        ),
        ad_silence_seconds=int(
            _clamp_number(
                opts.get(CONF_AD_SILENCE_SECONDS, AD_SILENCE_NEW_WINDOW_SECONDS),
                AD_SILENCE_NEW_WINDOW_SECONDS,
                *AD_SILENCE_RANGE,
                as_int=True,
            )
        ),
        advertise_linger_seconds=int(
            _clamp_number(
                opts.get(CONF_ADVERTISE_LINGER_SECONDS, CUFF_ADVERTISE_SECONDS),
                CUFF_ADVERTISE_SECONDS,
                *ADVERTISE_LINGER_RANGE,
                as_int=True,
            )
        ),
        poll_window_gap_seconds=int(
            _clamp_number(
                opts.get(CONF_POLL_WINDOW_GAP_SECONDS, POLL_WINDOW_GAP_SECONDS),
                POLL_WINDOW_GAP_SECONDS,
                *POLL_WINDOW_GAP_RANGE,
                as_int=True,
            )
        ),
        dump_timeout_seconds=float(
            _clamp_number(
                opts.get(CONF_DUMP_TIMEOUT_SECONDS, DUMP_TIMEOUT_SECONDS),
                DUMP_TIMEOUT_SECONDS,
                *DUMP_TIMEOUT_RANGE,
            )
        ),
        dump_idle_seconds=float(
            _clamp_number(
                opts.get(CONF_DUMP_IDLE_SECONDS, DUMP_IDLE_SECONDS),
                DUMP_IDLE_SECONDS,
                *DUMP_IDLE_RANGE,
            )
        ),
    )


def options_schema_defaults(options: Mapping[str, Any] | None = None) -> dict[str, int | float]:
    """Return option values for the Configure form (merged with defaults)."""
    settings = settings_from_options(options)
    return {
        CONF_PHONE_GRACE_SECONDS: settings.phone_grace_seconds,
        CONF_AD_SILENCE_SECONDS: settings.ad_silence_seconds,
        CONF_ADVERTISE_LINGER_SECONDS: settings.advertise_linger_seconds,
        CONF_POLL_WINDOW_GAP_SECONDS: settings.poll_window_gap_seconds,
        CONF_DUMP_TIMEOUT_SECONDS: int(settings.dump_timeout_seconds),
        CONF_DUMP_IDLE_SECONDS: int(settings.dump_idle_seconds),
    }
