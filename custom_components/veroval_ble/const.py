"""Constants for Veroval Blood Pressure BLE."""

from __future__ import annotations

DOMAIN = "veroval_ble"


CONF_CUFF_USER = "cuff_user"
CONF_PIN = "pin"

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
# Range: 10–40 s. Must be > BLUEZ_RSSI_POLL_SECONDS and << CUFF_ADVERTISE_SECONDS.
AD_SILENCE_NEW_WINDOW_SECONDS = 20
# Advertising stays on this long after the *last* live sighting (HA ad or BlueZ
# RSSI). Cuff flash is ~2 min. One HA callback at the start → 120 s matches the
# flash. Continuous RSSI refresh → the sensor lingers this long after ads stop.
# Range: 90–150 s for the single-callback case; 5–20 s if sightings keep arriving.
CUFF_ADVERTISE_SECONDS = 120
# Config-flow pairing only: HA scanner cache can outlive BlueZ Device1.
# Range: 10–60 s. Must be < CUFF_ADVERTISE_SECONDS. Not used for dumps.
ADVERTISEMENT_MAX_AGE_SECONDS = 30.0
# Last-resort expiry if HA keeps delivering ads and never marks unavailable.
# Range: 150–240 s. Must be > CUFF_ADVERTISE_SECONDS so a second dump cannot
# start in the same ~2 min flash.
POLL_WINDOW_GAP_SECONDS = 180
# Wait after the first advertisement of a new GATT window so medi.connect
# can take the transfer. Cache consume and force_dump skip this wait.
# Range: 0–60 s (0 = dump immediately). 60 s is the top of the useful range:
# grace + DUMP_TIMEOUT_SECONDS must stay under the ~120 s flash.
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


def normalize_ble_address(address: str) -> str:
    """Return the BLE address in the form HA's callback matcher indexes."""
    return address.upper()
