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
from .const import PHONE_GRACE_SECONDS, POLL_WINDOW_GAP_SECONDS, UPDATE_INTERVAL
from .parser import (
    BloodPressureMeasurement,
    cuff_user_to_ble_id,
    select_latest_for_user,
)

_LOGGER = logging.getLogger(__name__)

_ADDRESS_LOCKS: dict[str, asyncio.Lock] = {}


def _advertisement_address(service_info: object) -> str:
    """Best-effort BLE address from an advertisement callback argument."""
    device = getattr(service_info, "device", None)
    address = getattr(device, "address", None)
    if address:
        return str(address)
    address = getattr(service_info, "address", None)
    if address:
        return str(address)
    return "unknown"


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
        self._window_records: list[BloodPressureMeasurement] | None = None
        self._consumed_slots: set[int] = set()
        self._grace_started_at: float | None = None
        self._grace_address: str | None = None
        self._grace_elapsed_logged = False
        self._window_skipped = False

    def _expire_window_if_due(self) -> None:
        """Clear dump and grace state after POLL_WINDOW_GAP_SECONDS."""
        polled_at = self._window_polled_at
        if (
            polled_at is None
            or self._monotonic() - polled_at < POLL_WINDOW_GAP_SECONDS
        ):
            return
        self._polled_this_window = False
        self._window_polled_at = None
        self._window_records = None
        self._consumed_slots.clear()
        self._grace_started_at = None
        self._grace_address = None
        self._grace_elapsed_logged = False
        self._window_skipped = False

    def _grace_in_progress(self) -> bool:
        """True while waiting for the phone, including after the 60s elapsed."""
        return (
            self._grace_started_at is not None
            and not self._window_skipped
            and self._window_records is None
            and not self._polled_this_window
        )

    def poll_needed(
        self,
        service_info: BluetoothServiceInfoBleak,
        last_poll: float | None,
        cuff_user: int | None = None,
    ) -> bool:
        """Return True when an advertisement should trigger a GATT dump.

        Two-arg calls stay GATT-window-only (a dump in this window suppresses
        another connect). Pass *cuff_user* so the other slot can consume the
        shared dump without a second connection.

        The first advertisement of a window starts a phone-first grace
        (``PHONE_GRACE_SECONDS``). Later advertisements of the same window
        share that timer.
        """
        self._expire_window_if_due()

        if self._poll_lock.locked():
            return False

        if self._window_records is not None:
            if cuff_user is not None and cuff_user not in self._consumed_slots:
                return True
            # Dump already happened this window. Consumed slots and two-arg
            # callers must not start a new GATT dump while the cache lives.
            # The next window starts when expiry clears the cache.
            return False

        if self._polled_this_window:
            return False

        if self._window_skipped:
            return False

        address = _advertisement_address(service_info)
        now = self._monotonic()
        if self._grace_started_at is None:
            self._grace_started_at = now
            self._grace_address = address if address != "unknown" else None
            _LOGGER.debug(
                "Waiting %ss for phone app before polling %s",
                PHONE_GRACE_SECONDS,
                address,
            )
        if now - self._grace_started_at < PHONE_GRACE_SECONDS:
            return False
        if not self._grace_elapsed_logged:
            _LOGGER.debug("Phone grace elapsed; polling %s", address)
            self._grace_elapsed_logged = True
        return not last_poll or last_poll > UPDATE_INTERVAL

    def mark_window_ended(self, address: str | None = None) -> None:
        """Handle the cuff stopping advertisements.

        Connecting stops advertisements, so Home Assistant may mark the cuff
        unavailable while a dump is still running. Ignore that signal.

        If the phone-first grace is still open, treat disappearance as the
        phone grabbing the transfer and skip this window.

        Keep ``_window_records`` / ``_consumed_slots`` so the other user slot
        can still consume this window's dump. The cache is cleared when a new
        GATT dump starts or when the poll-window gap expires.
        """
        if self._poll_lock.locked():
            return
        if self._window_skipped:
            return
        if self._grace_in_progress():
            label = address or self._grace_address or "unknown"
            self._window_skipped = True
            self._window_polled_at = self._monotonic()
            self._grace_started_at = None
            _LOGGER.debug(
                "Cuff disappeared during phone grace; skipping dump for %s",
                label,
            )
            return
        self._polled_this_window = False
        if self._window_records is None:
            self._window_polled_at = None

    def _mark_polled_this_window(self) -> None:
        self._polled_this_window = True
        self._window_polled_at = self._monotonic()

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
        self.address = address
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
        return (
            self.hass.state is CoreState.running
            and self.device_data.poll_needed(
                service_info, last_poll, self.cuff_user
            )
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
        """Skip an in-progress phone grace, or reset after a finished dump."""
        self.device_data.mark_window_ended(self.address)
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
