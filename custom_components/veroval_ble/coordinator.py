"""Poll coordinator: drain the 0x2A35 indication dump, then pick latest for one user."""

from __future__ import annotations

import asyncio
import logging
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
from .const import UPDATE_INTERVAL
from .parser import BloodPressureMeasurement, cuff_user_to_ble_id

_LOGGER = logging.getLogger(__name__)

_ADDRESS_LOCKS: dict[str, asyncio.Lock] = {}


def _address_lock(address: str) -> asyncio.Lock:
    """Return a shared lock so two user-slot entries do not connect at once."""
    key = address.lower()
    if key not in _ADDRESS_LOCKS:
        _ADDRESS_LOCKS[key] = asyncio.Lock()
    return _ADDRESS_LOCKS[key]


class VerovalBleDeviceData:
    """Connect, drain 0x2A35 indications, select newest record for one BLE user id."""

    def __init__(self) -> None:
        """Initialize poll state."""
        self._poll_lock = asyncio.Lock()
        self.last_measurement: BloodPressureMeasurement | None = None
        self._last_published_timestamp: datetime | None = None
        self._polled_this_window = False

    def poll_needed(
        self,
        service_info: BluetoothServiceInfoBleak,  # noqa: ARG002
        last_poll: float | None,
    ) -> bool:
        """Return True when an advertisement should trigger a GATT dump."""
        if self._poll_lock.locked() or self._polled_this_window:
            return False
        return not last_poll or last_poll > UPDATE_INTERVAL

    def mark_window_ended(self) -> None:
        """Allow another dump after the cuff stops advertising."""
        self._polled_this_window = False

    async def async_poll(
        self, ble_device: BLEDevice, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Drain BPM indications and return the newest record for *cuff_user*."""
        async with self._poll_lock:
            async with _address_lock(ble_device.address):
                return await self._async_poll_locked(ble_device, cuff_user)

    async def _async_poll_locked(
        self, ble_device: BLEDevice, cuff_user: int
    ) -> BloodPressureMeasurement | None:
        """Run one connect → notify → idle-or-timeout → disconnect cycle."""
        result = await dump_latest(ble_device, cuff_user)
        if result.auth_error or result.missing_characteristic:
            return self.last_measurement

        if not result.records:
            return self.last_measurement

        selected = result.selected
        if selected is None:
            if result.records:
                self._polled_this_window = True
            return self.last_measurement

        self._polled_this_window = True

        if (
            self._last_published_timestamp is not None
            and selected.timestamp == self._last_published_timestamp
        ):
            _LOGGER.debug(
                "Selected cuff timestamp %s unchanged; keeping last published reading",
                selected.timestamp.isoformat(),
            )
            return self.last_measurement

        self.last_measurement = selected
        self._last_published_timestamp = selected.timestamp
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

    def _async_needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        last_poll: float | None,
    ) -> bool:
        """Poll when HA is running, a connectable path exists, and the dump is due."""
        return (
            self.hass.state is CoreState.running
            and self.device_data.poll_needed(service_info, last_poll)
            and bool(
                async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

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
