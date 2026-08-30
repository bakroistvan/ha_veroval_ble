"""Poll coordinator: drain the 0x2A35 indication dump, then pick latest for one user."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace

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

from .advertisement import advertisement_is_live, advertisement_monotonic_time
from .bluez_pair import async_watch_device_rssi, is_bluez_pairing_supported
from .client import dump_latest
from .const import (
    AD_SILENCE_NEW_WINDOW_SECONDS,
    BLUEZ_RSSI_POLL_SECONDS,
    CUFF_ADVERTISE_SECONDS,
    DOMAIN,
    PHONE_GRACE_SECONDS,
    POLL_WINDOW_GAP_SECONDS,
    UPDATE_INTERVAL,
    normalize_ble_address,
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


def _live_sighting(address: str, now: float) -> object:
    """Build a poll_needed argument whose scanner stamp is *now* (age 0)."""
    return SimpleNamespace(
        time=now,
        address=address,
        device=SimpleNamespace(address=address),
    )


def _address_lock(address: str) -> asyncio.Lock:
    """Return a shared lock so two user-slot entries do not connect at once."""
    key = address.lower()
    if key not in _ADDRESS_LOCKS:
        _ADDRESS_LOCKS[key] = asyncio.Lock()
    return _ADDRESS_LOCKS[key]


def _default_utcnow() -> datetime:
    """Return aware UTC now (injectable in tests via VerovalBleDeviceData)."""
    return datetime.now(timezone.utc)


class VerovalBleDeviceData:
    """Connect, drain 0x2A35 indications, select newest record for one BLE user id."""

    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = _default_utcnow,
    ) -> None:
        """Initialize poll state."""
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._poll_lock = asyncio.Lock()
        self.last_measurement: dict[int, BloodPressureMeasurement] = {}
        self.last_synchronized: dict[int, datetime] = {}
        self._last_published_timestamp: dict[int, datetime] = {}
        self._polled_this_window = False
        self._window_polled_at: float | None = None
        self._last_ad_time: float | None = None
        self._last_live_ad_time: float | None = None
        self._last_ad_stamp: float | None = None
        self._stale_ad_logged = False
        self._awaiting_new_window = False
        self._window_records: list[BloodPressureMeasurement] | None = None
        self._consumed_slots: set[int] = set()
        self._grace_started_at: float | None = None
        self._grace_address: str | None = None
        self._grace_elapsed_logged = False
        self._grace_timer: object | None = None
        self._window_skipped = False

    def cancel_grace_timer(self) -> None:
        """Cancel the delayed dump that runs when HA sends no further ads."""
        handle = self._grace_timer
        if handle is None:
            return
        cancel = getattr(handle, "cancel", None)
        if cancel is not None:
            cancel()
        self._grace_timer = None

    def _begin_new_window(self, reason: str) -> None:
        """Clear dump-skip and grace state so the next ad can start a GATT dump."""
        _LOGGER.debug("Starting new advertise window (%s)", reason)
        self.cancel_grace_timer()
        self._polled_this_window = False
        self._window_polled_at = None
        self._window_records = None
        self._consumed_slots.clear()
        self._awaiting_new_window = False
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

    def _phone_grace_allows_poll(
        self,
        service_info: object,
        last_poll: float | None,
        now: float,
    ) -> bool:
        """Start or continue the 60s phone-first wait; True when a dump may start."""
        address = _advertisement_address(service_info)
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

    def _expire_stale_window(self, now: float) -> None:
        """Drop the dump cache after the last-resort gap (HA never went unavailable)."""
        polled_at = self._window_polled_at
        if polled_at is None or now - polled_at < POLL_WINDOW_GAP_SECONDS:
            return
        self._begin_new_window("poll-window gap expired")

    def is_advertising(self, now: float | None = None) -> bool:
        """True during the cuff's ~2 minute flash after the last live ad."""
        if self._last_live_ad_time is None:
            return False
        if now is None:
            now = self._monotonic()
        return now - self._last_live_ad_time < CUFF_ADVERTISE_SECONDS

    def grace_dump_due(self, now: float | None = None) -> bool:
        """True when phone grace elapsed and no dump has run this window."""
        if not self._grace_in_progress():
            return False
        if self._grace_started_at is None:
            return False
        if now is None:
            now = self._monotonic()
        return now - self._grace_started_at >= PHONE_GRACE_SECONDS

    def _advertisement_is_live(self, service_info: object, now: float) -> bool:
        """Return True if this callback is a real, new advertisement."""
        return advertisement_is_live(
            service_info,
            now,
            max_age=AD_SILENCE_NEW_WINDOW_SECONDS,
            require_timestamp=False,
            last_seen_stamp=self._last_ad_stamp,
        )

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

        A new window starts when *live* advertisements have been gone long
        enough (silence or idle unavailable), not only after
        ``POLL_WINDOW_GAP_SECONDS``. Cached scanner callbacks do not refresh
        the last-ad clock. A fresh GATT connect still waits
        ``PHONE_GRACE_SECONDS`` so medi.connect can take the transfer.
        Shared-cache consume and ``force_dump`` do not.
        """
        now = self._monotonic()
        if not self._advertisement_is_live(service_info, now):
            if not self._stale_ad_logged:
                _LOGGER.debug(
                    "Ignoring stale or cached advertisement for %s",
                    _advertisement_address(service_info),
                )
                self._stale_ad_logged = True
            return False
        self._stale_ad_logged = False
        stamp = advertisement_monotonic_time(service_info)
        if stamp is not None:
            self._last_ad_stamp = stamp
        self._last_live_ad_time = now

        self._expire_stale_window(now)

        if self._poll_lock.locked():
            return False

        if self._window_skipped:
            return False

        # Scanner gaps during the 60s wait must not look like a new window.
        if self._grace_in_progress():
            self._last_ad_time = now
            return self._phone_grace_allows_poll(service_info, last_poll, now)

        if (
            self._last_ad_time is not None
            and now - self._last_ad_time >= AD_SILENCE_NEW_WINDOW_SECONDS
        ):
            self._begin_new_window("advertisement silence")
            self._last_ad_time = now
            return self._phone_grace_allows_poll(service_info, last_poll, now)

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
            return self._phone_grace_allows_poll(service_info, last_poll, now)

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
        return self._phone_grace_allows_poll(service_info, last_poll, now)

    def mark_window_ended(self, address: str | None = None) -> None:
        """Handle the cuff stopping advertisements.

        Connecting stops advertisements, so Home Assistant may mark the cuff
        unavailable while a dump is still running. Ignore that signal.

        If the phone-first grace is still open, treat disappearance as the
        phone grabbing the transfer and skip this window.

        Keep ``_window_records`` / ``_consumed_slots`` so the other user slot
        can still consume this window's dump. The next advertisement for a
        slot that already consumed starts a new window (then grace).
        """
        if self._poll_lock.locked():
            return
        if self._window_skipped:
            return
        if self._grace_in_progress():
            label = address or self._grace_address or "unknown"
            self.cancel_grace_timer()
            self._window_skipped = True
            self._window_polled_at = self._monotonic()
            self._grace_started_at = None
            _LOGGER.debug(
                "Cuff disappeared during phone grace; skipping dump for %s",
                label,
            )
            return
        self._polled_this_window = False
        self._awaiting_new_window = True
        if self._window_records is None:
            self._window_polled_at = None

    def _mark_polled_this_window(self) -> None:
        self.cancel_grace_timer()
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
        # Dump consumed for this slot (shared window or fresh GATT).
        self.last_synchronized[cuff_user] = self._utcnow()
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
        self._advertising_timer: object | None = None
        address = normalize_ble_address(address)
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

    @property
    def last_synchronized(self) -> datetime | None:
        """Home Assistant time of the last successful dump for this cuff user."""
        return self.device_data.last_synchronized.get(self.cuff_user)

    @property
    def is_advertising(self) -> bool:
        """True during the cuff's ~2 minute flash after the last live ad.

        Home Assistant often delivers only one callback (or replays the same
        scanner stamp). The sensor stays on for the flash period, not 20s.
        """
        return self.device_data.is_advertising()

    def _cancel_advertising_timer(self) -> None:
        """Cancel the delayed listener refresh that turns advertising off."""
        handle = self._advertising_timer
        if handle is None:
            return
        cancel = getattr(handle, "cancel", None)
        if cancel is not None:
            cancel()
        self._advertising_timer = None

    def _schedule_advertising_timer(self) -> None:
        """Refresh entities when the ~2 minute flash window should end."""
        self._cancel_advertising_timer()
        last_live = self.device_data._last_live_ad_time
        if last_live is None:
            return
        delay = (
            CUFF_ADVERTISE_SECONDS
            - (self.device_data._monotonic() - last_live)
            + 0.5
        )
        if delay <= 0:
            self.async_update_listeners()
            return
        loop = getattr(self.hass, "loop", None)
        call_later = getattr(loop, "call_later", None)
        if call_later is None:
            return
        self._advertising_timer = call_later(
            delay, self._async_advertising_timer_fired
        )

    def _ensure_grace_timer(self) -> None:
        """Dump after 60s even if Home Assistant sends no further advertisements."""
        data = self.device_data
        if not data._grace_in_progress() or data._grace_started_at is None:
            return
        if data._grace_timer is not None:
            return
        delay = max(
            0.0,
            PHONE_GRACE_SECONDS - (data._monotonic() - data._grace_started_at),
        )
        loop = getattr(self.hass, "loop", None)
        call_later = getattr(loop, "call_later", None)
        if call_later is None:
            return
        data._grace_timer = call_later(delay, self._async_grace_timer_fired)

    @callback
    def _async_grace_timer_fired(self) -> None:
        """Phone grace elapsed; start the dump without waiting for another ad."""
        self.device_data._grace_timer = None
        create_task = getattr(self.hass, "async_create_task", None)
        if create_task is None:
            return
        create_task(self._async_grace_elapsed_poll())

    async def _async_grace_elapsed_poll(self) -> None:
        """Connect after phone grace when advertisement callbacks have stopped."""
        if not self.device_data.grace_dump_due():
            return
        if self.hass.state is not CoreState.running:
            return
        connectable_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if connectable_device is None:
            _LOGGER.debug(
                "Phone grace elapsed; no connectable BLEDevice for %s",
                self.address,
            )
            return
        _LOGGER.debug(
            "Phone grace elapsed; polling %s cuff_user=%s (no further ads)",
            self.address,
            self.cuff_user,
        )
        measurement = await self.device_data.async_poll(
            connectable_device, self.cuff_user
        )
        self._publish_shared_slots(measurement)

    def _publish_shared_slots(
        self, measurement: BloodPressureMeasurement | None
    ) -> None:
        """Publish this slot and let the other user consume the same dump."""
        self.async_publish_measurement(measurement)
        config_entries = getattr(self.hass, "config_entries", None)
        async_entries = getattr(config_entries, "async_entries", None)
        if async_entries is None:
            return
        for entry in async_entries(DOMAIN):
            other = getattr(entry, "runtime_data", None)
            if not isinstance(other, VerovalBleCoordinator) or other is self:
                continue
            if other.address.lower() != self.address.lower():
                continue
            other.async_publish_measurement(
                other.device_data.consume_shared_dump(other.cuff_user)
            )

    @callback
    def _async_advertising_timer_fired(self) -> None:
        """Push a listener update after advertisements have gone stale."""
        self._advertising_timer = None
        self.async_update_listeners()

    def async_start_bluez_rssi_watch(self) -> Callable[[], None]:
        """Watch BlueZ Device1 RSSI so a User-button flash is live without HA ads."""
        if not is_bluez_pairing_supported():
            return lambda: None
        create_task = getattr(self.hass, "async_create_task", None)
        if create_task is None:
            return lambda: None
        task = create_task(
            async_watch_device_rssi(
                self.address,
                self.async_handle_bluez_rssi,
                BLUEZ_RSSI_POLL_SECONDS,
            )
        )

        def _stop() -> None:
            task.cancel()

        return _stop

    @callback
    def async_handle_bluez_rssi(self, rssi: int) -> None:
        """Treat a Device1 RSSI update as a live advertisement."""
        if self.hass.state is not CoreState.running:
            return
        self.rssi = rssi
        now = self.device_data._monotonic()
        if not self.device_data.is_advertising(now):
            _LOGGER.debug(
                "BlueZ Device1 RSSI %s for %s (HA scanner had no live ad)",
                rssi,
                self.address,
            )
        sighting = _live_sighting(self.address, now)
        last_poll = getattr(self, "_last_poll", None)
        needed = self.device_data.poll_needed(sighting, last_poll, self.cuff_user)
        self._ensure_grace_timer()
        if needed:
            create_task = getattr(self.hass, "async_create_task", None)
            if create_task is not None:
                create_task(self._async_poll_connectable())
        if self.device_data.is_advertising():
            self._schedule_advertising_timer()
        self.async_update_listeners()

    async def _async_poll_connectable(self) -> None:
        """Drain the dump when a live sighting says a GATT poll is due."""
        if self.hass.state is not CoreState.running:
            return
        connectable_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if connectable_device is None:
            _LOGGER.debug(
                "Live sighting; no connectable BLEDevice for %s",
                self.address,
            )
            return
        measurement = await self.device_data.async_poll(
            connectable_device, self.cuff_user
        )
        self._publish_shared_slots(measurement)

    def _async_needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        last_poll: float | None,
    ) -> bool:
        """Poll when HA is running, a connectable path exists, and the dump is due."""
        if self.hass.state is not CoreState.running:
            return False
        needed = self.device_data.poll_needed(
            service_info, last_poll, self.cuff_user
        )
        self._ensure_grace_timer()
        if not needed:
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
        self.async_publish_measurement(measurement)
        return measurement

    @callback
    def async_publish_measurement(
        self, measurement: BloodPressureMeasurement | None
    ) -> None:
        """Push a dump result to entities.

        ``ActiveBluetoothDataUpdateCoordinator`` is not a
        ``DataUpdateCoordinator`` and has no ``async_set_updated_data``.
        """
        self.data = measurement
        if self.device_data.is_advertising():
            self._schedule_advertising_timer()
        self.async_update_listeners()

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
        self._ensure_grace_timer()
        if self.device_data.is_advertising():
            self._schedule_advertising_timer()
        self.async_update_listeners()


type VerovalBleConfigEntry = ConfigEntry[VerovalBleCoordinator]
