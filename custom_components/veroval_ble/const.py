"""Constants for Veroval Blood Pressure BLE."""

from __future__ import annotations

DOMAIN = "veroval_ble"


CONF_CUFF_USER = "cuff_user"
CONF_PIN = "pin"

SERVICE_FORCE_DUMP = "force_dump"

CHARACTERISTIC_BLOOD_PRESSURE = "00002a35-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 2751  # 0x0ABF PAUL HARTMANN AG
LOCAL_NAME = "BPU26"

DUMP_IDLE_SECONDS = 2.0
DUMP_TIMEOUT_SECONDS = 30.0
# No *new* live advertisements for this long → next live ad is a new GATT window.
# This is not how long the Advertising sensor stays on. HA often delivers only
# one callback (or replays the same scanner stamp) for the whole flash.
AD_SILENCE_NEW_WINDOW_SECONDS = 20
# How long Advertising stays on after the last live advertisement (cuff flash).
CUFF_ADVERTISE_SECONDS = 120
# HA scanner cache can outlive BlueZ Device1; pairing needs a recent host ad.
ADVERTISEMENT_MAX_AGE_SECONDS = 30.0
# Last-resort expiry if HA keeps delivering ads and never marks unavailable.
POLL_WINDOW_GAP_SECONDS = 180
# Wait after the first advertisement of a new GATT window so medi.connect
# can take the transfer. Cache consume and force_dump skip this wait.
PHONE_GRACE_SECONDS = 60
# BlueZ Device1 RSSI is present only while the cuff is transmitting. Poll it
# when HA's scanner delivers no BluetoothServiceInfoBleak (duplicate filter).
BLUEZ_RSSI_POLL_SECONDS = 2.0
SCAN_TIMEOUT_SECONDS = 60
UPDATE_INTERVAL = 10


def normalize_ble_address(address: str) -> str:
    """Return the BLE address in the form HA's callback matcher indexes."""
    return address.upper()
