"""Poll coordinator: drain the 0x2A35 indication dump, then pick latest for one user."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime

from bleak.backends.device import BLEDevice

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant, callback

from .client import dump_latest
from .const import (
    AD_SILENCE_NEW_WINDOW_SECONDS,
    POLL_WINDOW_GAP_SECONDS,
    UPDATE_INTERVAL,
)
from .parser import (
    BloodPressureMeasurement,
    cuff_user_to_ble_id,
    select_latest_for_user,
)

_LOGGER = logging.getLogger(__name__)

_ADDRESS_LOCKS: dict[str, asyncio.Lock] = {}


class CuffNotConnectableError(RuntimeError):
    """Raised when a force dump cannot resolve a connectable BLEDevice."""


def _address_lock(address: str) -> asyncio.Lock:
    """Return a shared lock so two user-slot entries do not connect at once."""
    key = address.lower()
    if key not in _ADDRESS_LOCKS:
        _ADDRESS_LOCKS[key] = asyncio.Lock()
    return _ADDRESS_LOCKS[key]


class VerovalBleDeviceData:
    """Connect, drain 0x2A35 indications, select newest record for one BLE user id."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        """Initialize poll state."""
        self._monotonic = monotonic
        self._poll_lock = asyncio.Lock()
        self.last_measurement: dict[int, BloodPressureMeasurement] = {}
        self._last_published_timestamp: dict[int, datetime] = {}
        self._polled_this_window = False
        self._window_polled_at: float | None = None
        self._last_ad_time: float | None = None
        self._awaiting_new_window = False
        self._window_records: list[BloodPressureMeasurement] | None = None
        self._consumed_slots: set[int] = set()

    def _begin_new_window(self, reason: str) -> None:
        """Clear dump-skip state so the next advertisement can start a GATT dump."""
        _LOGGER.debug("Starting new advertise window (%s)", reason)
        self._polled_this_window = False
        self._window_polled_at = None
        self._window_records = None
        self._consumed_slots.clear()
        self._awaiting_new_window = False

    def _expire_stale_window(self, now: float) -> None:
        """Drop the dump cache after the last-resort gap (HA never went unavailable)."""
        polled_at = self._window_polled_at
        if polled_at is None or now - polled_at < POLL_WINDOW_GAP_SECONDS:
            return
        self._begin_new_window("poll-window gap expired")

    def poll_needed(
        self,
        service_info: BluetoothServiceInfoBleak,  # noqa: ARG002
        last_poll: float | None,
        cuff_user: int | None = None,
    ) -> bool:
        """Return True when an advertisement should trigger a GATT dump.

        Two-arg calls stay GATT-window-only (a dump in this window suppresses
        another connect). Pass *cuff_user* so the other slot can consume the
        shared dump without a second connection.

        A new window starts when advertisements have been gone long enough
        (silence or idle unavailable), not only after ``POLL_WINDOW_GAP_SECONDS``.
        """
        now = self._monotonic()
        self._expire_stale_window(now)

        if self._poll_lock.locked():
            return False

        if (
            self._last_ad_time is not None
            and now - self._last_ad_time >= AD_SILENCE_NEW_WINDOW_SECONDS
        ):
            self._begin_new_window("advertisement silence")
            self._last_ad_time = now
            return True

        if self._awaiting_new_window:
            if (
                cuff_user is not None
                and self._window_records is not None
                and cuff_user not in self._consumed_slots
            ):
                self._last_ad_time = now
                return True
            self._begin_new_window("idle unavailable")
            self._last_ad_time = now
            return True

        self._last_ad_time = now

        if self._window_records is not None:
            if cuff_user is not None and cuff_user not in self._consumed_slots:
                return True
            _LOGGER.debug(
                "Skipping poll cuff_user=%s: dump already consumed this window",
                cuff_user,
            )
            return False

        if self._polled_this_window:
            _LOGGER.debug(
                "Skipping poll cuff_user=%s: already polled this window",
                cuff_user,
            )
            return False
        return not last_poll or last_poll > UPDATE_INTERVAL

    def mark_window_ended(self) -> None:
        """Allow another dump after the cuff stops advertising.

        Connecting stops advertisements, so Home Assistant may mark the cuff
        unavailable while a dump is still running. Ignore that signal.

        Keep ``_window_records`` / ``_consumed_slots`` so the other user slot
        can still consume this window's dump. The next advertisement for a
        slot that already consumed starts a new window.
        """
        if self._poll_lock.locked():
            return
        self._polled_this_window = False
        self._awaiting_new_window = True
        if self._window_records is None:
            self._window_polled_at = None

    def _mark_polled_this_window(self) -> None:
        self._polled_this_window = True
        now = self._monotonic()
        self._window_polled_at = now
        # Dump silence must not look like a new advertise window.
        self._last_ad_time = now
        self._awaiting_new_window = False

    async def async_poll(
        self, ble_device: BLEDevice, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Drain BPM indications and return the newest record for *cuff_user*."""
        async with self._poll_lock:
            async with _address_lock(ble_device.address):
                return await self._async_poll_locked(ble_device, cuff_user)

    async def async_force_poll(
        self, ble_device: BLEDevice, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Clear window skip and dump now (debug / manual sync)."""
        self._begin_new_window("force dump")
        return await self.async_poll(ble_device, cuff_user)

    def consume_shared_dump(
        self, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Publish this slot from the dump already in memory (no connect)."""
        if (
            self._window_records is None
            or cuff_user in self._consumed_slots
        ):
            return self.last_measurement.get(cuff_user)
        return self._publish_from_records(self._window_records, cuff_user)

    async def _async_poll_locked(
        self, ble_device: BLEDevice, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Run one connect → notify → idle-or-timeout → disconnect cycle."""
        if (
            self._window_records is not None
            and cuff_user not in self._consumed_slots
        ):
            return self._publish_from_records(self._window_records, cuff_user)

        self._window_records = None
        self._consumed_slots.clear()

        result = await dump_latest(ble_device, cuff_user)
        if result.auth_error or result.missing_characteristic:
            return self.last_measurement.get(cuff_user)

        if not result.records:
            return self.last_measurement.get(cuff_user)

        self._window_records = result.records
        return self._publish_from_records(result.records, cuff_user)

    def _publish_from_records(
        self,
        records: list[BloodPressureMeasurement],
        cuff_user: int,
    ) -> BloodPressureMeasurement | None:
        """Select this slot from *records*, mark it consumed, and publish."""
        selected = select_latest_for_user(records, cuff_user_to_ble_id(cuff_user))
        self._consumed_slots.add(cuff_user)
        if records:
            self._mark_polled_this_window()
        if selected is None:
            return self.last_measurement.get(cuff_user)

        last_ts = self._last_published_timestamp.get(cuff_user)
        if last_ts is not None and selected.timestamp == last_ts:
            _LOGGER.debug(
                "Selected cuff timestamp %s unchanged; keeping last published reading",
                selected.timestamp.isoformat(),
            )
            return self.last_measurement.get(cuff_user)

        self.last_measurement[cuff_user] = selected
        self._last_published_timestamp[cuff_user] = selected.timestamp
        return selected


class VerovalBleCoordinator(
    ActiveBluetoothDataUpdateCoordinator[BloodPressureMeasurement | None]
):
    """Advertisement-driven coordinator that stores the last selected measurement."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        cuff_user: int,
        device_data: VerovalBleDeviceData,
    ) -> None:
        """Initialize the coordinator for one address + cuff user slot."""
        self.cuff_user = cuff_user
        self.device_data = device_data
        self.rssi: int | None = None
        super().__init__(
            hass,
            _LOGGER,
            address=address,
            mode=BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._async_needs_poll,
            poll_method=self._async_poll_service,
            connectable=False,
        )
        self.address = address

    @property
    def last_measurement(self) -> BloodPressureMeasurement | None:
        """Last published measurement for this coordinator's cuff user."""
        return self.device_data.last_measurement.get(self.cuff_user)

    def _async_needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        last_poll: float | None,
    ) -> bool:
        """Poll when HA is running, a connectable path exists, and the dump is due."""
        if self.hass.state is not CoreState.running:
            return False
        if not self.device_data.poll_needed(
            service_info, last_poll, self.cuff_user
        ):
            return False
        if async_ble_device_from_address(
            self.hass, service_info.device.address, connectable=True
        ):
            return True
        _LOGGER.debug(
            "Skipping poll for %s cuff_user=%s: no connectable BLEDevice",
            service_info.device.address,
            self.cuff_user,
        )
        return False

    async def _async_poll_service(
        self, service_info: BluetoothServiceInfoBleak
    ) -> BloodPressureMeasurement | None:
        """Resolve a connectable BLEDevice and drain the dump for this slot."""
        if service_info.connectable:
            connectable_device = service_info.device
        elif device := async_ble_device_from_address(
            self.hass, service_info.device.address, True
        ):
            connectable_device = device
        else:
            raise RuntimeError(
                f"No connectable device found for {service_info.device.address}"
            )
        _LOGGER.debug(
            "Polling %s cuff_user=%s ble_user_id=%s",
            connectable_device.address,
            self.cuff_user,
            cuff_user_to_ble_id(self.cuff_user),
        )
        return await self.device_data.async_poll(connectable_device, self.cuff_user)

    async def async_force_poll(self) -> BloodPressureMeasurement | None:
        """Connect now and drain the dump, ignoring advertise-window skip."""
        connectable_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if connectable_device is None:
            raise CuffNotConnectableError(
                f"No connectable BPU26 at {self.address}. "
                "Press User 1 or User 2 so Bluetooth flashes."
            )
        _LOGGER.info(
            "Force dump %s cuff_user=%s ble_user_id=%s",
            connectable_device.address,
            self.cuff_user,
            cuff_user_to_ble_id(self.cuff_user),
        )
        measurement = await self.device_data.async_force_poll(
            connectable_device, self.cuff_user
        )
        self.async_set_updated_data(measurement)
        return measurement

    @callback
    def _async_handle_unavailable(
        self, service_info: BluetoothServiceInfoBleak
    ) -> None:
        """Reset dump-skip so the next advertise window can poll again."""
        self.device_data.mark_window_ended()
        super()._async_handle_unavailable(service_info)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Track RSSI from advertisements; never log them at INFO."""
        self.rssi = service_info.rssi
        super()._async_handle_bluetooth_event(service_info, change)


type VerovalBleConfigEntry = ConfigEntry[VerovalBleCoordinator]
