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
# No advertisements for this long → next ad is a new GATT window.
# Longer than a brief scanner gap; shorter than a new measurement (~30–60s)
# and the cuff's ~2 minute advertise period.
AD_SILENCE_NEW_WINDOW_SECONDS = 20
# HA scanner cache can outlive BlueZ Device1; pairing needs a recent host ad.
ADVERTISEMENT_MAX_AGE_SECONDS = 30.0
# Last-resort expiry if HA keeps delivering ads and never marks unavailable.
POLL_WINDOW_GAP_SECONDS = 180
# Wait after the first advertisement of a new GATT window so medi.connect
# can take the transfer. Cache consume and force_dump skip this wait.
PHONE_GRACE_SECONDS = 60
SCAN_TIMEOUT_SECONDS = 60
UPDATE_INTERVAL = 10
