"""Constants for Veroval Blood Pressure BLE."""

from __future__ import annotations

DOMAIN = "veroval_ble"

CONF_CUFF_USER = "cuff_user"
CONF_PIN = "pin"

CHARACTERISTIC_BLOOD_PRESSURE = "00002a35-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 2751  # 0x0ABF PAUL HARTMANN AG
LOCAL_NAME = "BPU26"

DUMP_IDLE_SECONDS = 2.0
DUMP_TIMEOUT_SECONDS = 30.0
# Gap after a dump before advertisements may start another window.
# Longer than the cuff advertise period; independent of DUMP_TIMEOUT_SECONDS.
POLL_WINDOW_GAP_SECONDS = 180
SCAN_TIMEOUT_SECONDS = 60
UPDATE_INTERVAL = 10
