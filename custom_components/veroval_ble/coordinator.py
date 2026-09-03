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
    BLUEZ_RSSI_POLL_SECONDS,
    UPDATE_INTERVAL,
    VerovalBleSettings,
    normalize_ble_address,
)
from .parser import (
    CUFF_USER_1,
    CUFF_USER_2,
    BloodPressureMeasurement,
    cuff_user_to_ble_id,
    select_latest_for_user,
)

_CUFF_USERS = (CUFF_USER_1, CUFF_USER_2)

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
        settings: VerovalBleSettings | None = None,
    ) -> None:
        """Initialize poll state."""
        self._monotonic = monotonic
        self._utcnow = utcnow
        self.settings = settings or VerovalBleSettings()
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
        self.bluez_connected = False
        # Rising-edge only: cached Device1 RSSI is not a new advertisement.
        self._bluez_rssi_present = False
        self._connected_listeners: list[Callable[[], None]] = []

    def async_add_connected_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Notify *listener* when host Connected or an in-progress dump changes."""
        self._connected_listeners.append(listener)

        def _remove() -> None:
            try:
                self._connected_listeners.remove(listener)
            except ValueError:
                pass

        return _remove

    def _notify_connected(self) -> None:
        for listener in list(self._connected_listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Connected listener failed")

    def set_bluez_connected(self, connected: bool) -> None:
        """Record Device1 Connected from the BlueZ radio watch."""
        if self.bluez_connected == connected:
            return
        self.bluez_connected = connected
        self._notify_connected()

    @property
    def is_connected(self) -> bool:
        """True while the host adapter has a GATT link, or a dump holds the lock."""
        return self.bluez_connected or self._poll_lock.locked()

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
        """True while waiting for the phone, including after grace elapsed."""
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
        """Start or continue the phone-first wait; True when a dump may start."""
        address = _advertisement_address(service_info)
        if self._grace_started_at is None:
            self._grace_started_at = now
            self._grace_address = address if address != "unknown" else None
            _LOGGER.debug(
                "Waiting %ss for phone app before polling %s",
                self.settings.phone_grace_seconds,
                address,
            )
        if now - self._grace_started_at < self.settings.phone_grace_seconds:
            return False
        if not self._grace_elapsed_logged:
            _LOGGER.debug("Phone grace elapsed; polling %s", address)
            self._grace_elapsed_logged = True
        return not last_poll or last_poll > UPDATE_INTERVAL

    def _expire_stale_window(self, now: float) -> None:
        """Drop the dump cache after the last-resort gap (HA never went unavailable)."""
        polled_at = self._window_polled_at
        if (
            polled_at is None
            or now - polled_at < self.settings.poll_window_gap_seconds
        ):
            return
        self._begin_new_window("poll-window gap expired")

    def is_advertising(self, now: float | None = None) -> bool:
        """True while a live sighting is still recent (not the full 2 min flash)."""
        if self._last_live_ad_time is None:
            return False
        if now is None:
            now = self._monotonic()
        return now - self._last_live_ad_time < self.settings.advertise_linger_seconds

    def grace_dump_due(self, now: float | None = None) -> bool:
        """True when phone grace elapsed and no dump has run this window."""
        if not self._grace_in_progress():
            return False
        if self._grace_started_at is None:
            return False
        if now is None:
            now = self._monotonic()
        return now - self._grace_started_at >= self.settings.phone_grace_seconds

    def _advertisement_is_live(self, service_info: object, now: float) -> bool:
        """Return True if this callback is a real, new advertisement."""
        return advertisement_is_live(
            service_info,
            now,
            max_age=self.settings.ad_silence_seconds,
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

        A dump in this window suppresses another connect. *cuff_user* is
        accepted for older call sites; one poll now publishes both slots.

        A new window starts when *live* advertisements have been gone long
        enough (silence or idle unavailable), not only after
        ``poll_window_gap_seconds``. Cached scanner callbacks do not refresh
        the last-ad clock. A fresh GATT connect still waits
        ``phone_grace_seconds`` so medi.connect can take the transfer.
        ``force_dump`` does not.
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

        # Scanner gaps during phone grace must not look like a new window.
        if self._grace_in_progress():
            self._last_ad_time = now
            return self._phone_grace_allows_poll(service_info, last_poll, now)

        if (
            self._last_ad_time is not None
            and now - self._last_ad_time >= self.settings.ad_silence_seconds
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

        Keep ``_window_records`` so a late consumer can still read this
        window's dump. The next advertisement after a finished dump starts a
        new window (then grace).
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
        self, ble_device: BLEDevice, cuff_user: int | None = None
    ) -> BloodPressureMeasurement | dict[int, BloodPressureMeasurement | None] | None:
        """Drain BPM indications and publish newest records for both users.

        If *cuff_user* is set, return that slot's measurement (tests and older
        call sites). Otherwise return a map of both slots.
        """
        async with self._poll_lock:
            async with _address_lock(ble_device.address):
                published = await self._async_poll_locked(ble_device)
        if cuff_user is not None:
            return published.get(cuff_user)
        return published

    async def async_force_poll(
        self, ble_device: BLEDevice, cuff_user: int | None = None
    ) -> BloodPressureMeasurement | dict[int, BloodPressureMeasurement | None] | None:
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
        self, ble_device: BLEDevice
    ) -> dict[int, BloodPressureMeasurement | None]:
        """Run one connect → notify → idle-or-timeout → disconnect cycle."""
        if self._window_records is not None:
            return self._publish_all_slots(self._window_records)

        self._window_records = None
        self._consumed_slots.clear()

        self._notify_connected()
        try:
            result = await dump_latest(
                ble_device,
                CUFF_USER_1,
                dump_idle=self.settings.dump_idle_seconds,
                dump_timeout=self.settings.dump_timeout_seconds,
            )
        finally:
            self._notify_connected()
        if result.auth_error or result.missing_characteristic or not result.records:
            return {user: self.last_measurement.get(user) for user in _CUFF_USERS}

        self._window_records = result.records
        return self._publish_all_slots(result.records)

    def _publish_all_slots(
        self, records: list[BloodPressureMeasurement]
    ) -> dict[int, BloodPressureMeasurement | None]:
        """Select and stamp both cuff users from one dump."""
        return {
            cuff_user: self._publish_from_records(records, cuff_user)
            for cuff_user in _CUFF_USERS
        }

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
        _LOGGER.info(
            "Latest reading User %s: systolic=%.0f mmHg diastolic=%.0f mmHg pulse=%.0f bpm",
            cuff_user,
            selected.systolic,
            selected.diastolic,
            selected.pulse,
        )
        return selected


class VerovalBleCoordinator(
    ActiveBluetoothDataUpdateCoordinator[dict[int, BloodPressureMeasurement | None]]
):
    """Advertisement-driven coordinator that stores the last selected measurements."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        device_data: VerovalBleDeviceData,
    ) -> None:
        """Initialize the coordinator for one cuff MAC."""
        self.device_data = device_data
        self.rssi: int | None = None
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
        # CoordinatorEntity.available reads this; bluetooth coordinators omit it.
        self.last_update_success = True

    @property
    def settings(self) -> VerovalBleSettings:
        """Dump / advertise timing from the config entry options."""
        return self.device_data.settings

    def measurement_for(
        self, cuff_user: int | None
    ) -> BloodPressureMeasurement | None:
        """Last published measurement for *cuff_user*, or None."""
        if cuff_user is None:
            return None
        return self.device_data.last_measurement.get(cuff_user)

    def last_synchronized_for(self, cuff_user: int | None) -> datetime | None:
        """Home Assistant time of the last successful dump for *cuff_user*."""
        if cuff_user is None:
            return None
        return self.device_data.last_synchronized.get(cuff_user)

    @property
    def is_advertising(self) -> bool:
        """True while the last live sighting (HA ad or BlueZ RSSI) is still recent."""
        return self.device_data.is_advertising()

    @property
    def is_connected(self) -> bool:
        """True while the host adapter is in a GATT session with the cuff."""
        return self.device_data.is_connected

    def _ensure_grace_timer(self) -> None:
        """Dump after phone grace even if Home Assistant sends no further advertisements.

        One-shot: the timer scheduled when grace starts owns the dump. After it
        fires, do not replace it with a 0-delay callback on every RSSI poll.
        """
        data = self.device_data
        if not data._grace_in_progress() or data._grace_started_at is None:
            return
        if data._grace_timer is not None:
            return
        remaining = self.settings.phone_grace_seconds - (
            data._monotonic() - data._grace_started_at
        )
        if remaining <= 0:
            return
        loop = getattr(self.hass, "loop", None)
        call_later = getattr(loop, "call_later", None)
        if call_later is None:
            return
        data._grace_timer = call_later(remaining, self._async_grace_timer_fired)

    def _spawn_poll_task(self, coro: object) -> None:
        """Start a dump task unless one already holds the poll lock."""
        if self.device_data._poll_lock.locked():
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return
        create_task = getattr(self.hass, "async_create_task", None)
        if create_task is None:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return
        create_task(coro)

    @callback
    def _async_grace_timer_fired(self) -> None:
        """Phone grace elapsed; start the dump without waiting for another ad."""
        self.device_data._grace_timer = None
        self._spawn_poll_task(self._async_grace_elapsed_poll())

    async def _async_grace_elapsed_poll(self) -> None:
        """Connect after phone grace when advertisement callbacks have stopped."""
        if not self.device_data.grace_dump_due():
            return
        if self.hass.state is not CoreState.running:
            return
        if self.device_data._poll_lock.locked():
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
            "Phone grace elapsed; polling %s (no further ads)",
            self.address,
        )
        measurements = await self.device_data.async_poll(connectable_device)
        if isinstance(measurements, dict):
            self.async_publish_measurements(measurements)
        else:
            self.async_publish_measurements(
                {user: self.measurement_for(user) for user in _CUFF_USERS}
            )

    def async_start_bluez_rssi_watch(self) -> Callable[[], None]:
        """Watch BlueZ Device1 RSSI and Connected on the host adapter."""
        unsub = self.device_data.async_add_connected_listener(
            self.async_update_listeners
        )
        if not is_bluez_pairing_supported():
            return unsub
        spawn = getattr(self.hass, "async_create_background_task", None)
        if spawn is None:
            spawn = getattr(self.hass, "async_create_task", None)
        if spawn is None:
            return unsub
        task = spawn(
            async_watch_device_rssi(
                self.address,
                self.async_handle_bluez_rssi,
                BLUEZ_RSSI_POLL_SECONDS,
                on_connected=self.async_handle_bluez_connected,
            ),
            name=f"veroval_ble_rssi_{self.address}",
        )

        def _stop() -> None:
            task.cancel()
            unsub()

        return _stop

    @callback
    def async_handle_bluez_connected(self, connected: bool) -> None:
        """Mirror Device1 Connected from bluetoothctl onto the diagnostic."""
        if self.hass.state is not CoreState.running:
            return
        if connected != self.device_data.bluez_connected:
            _LOGGER.debug(
                "BlueZ Device1 Connected=%s for %s",
                connected,
                self.address,
            )
        self.device_data.set_bluez_connected(connected)

    @callback
    def async_handle_bluez_rssi(self, rssi: int | None) -> None:
        """Treat Device1 RSSI absent→present as one live advertisement.

        Cached RSSI still present on GetManagedObjects is not a new packet:
        update the diagnostic value only. Falling edge clears the rising-edge
        latch so the next flash can start a dump window.
        """
        if self.hass.state is not CoreState.running:
            return
        data = self.device_data
        if rssi is None:
            data._bluez_rssi_present = False
            self.async_update_listeners()
            return

        rising = not data._bluez_rssi_present
        data._bluez_rssi_present = True
        self.rssi = rssi
        if not rising:
            self.async_update_listeners()
            return

        now = data._monotonic()
        if not data.is_advertising(now):
            _LOGGER.debug(
                "BlueZ Device1 RSSI %s for %s (HA scanner had no live ad)",
                rssi,
                self.address,
            )
        sighting = _live_sighting(self.address, now)
        last_poll = getattr(self, "_last_poll", None)
        needed = data.poll_needed(sighting, last_poll)
        self._ensure_grace_timer()
        if needed:
            self._spawn_poll_task(self._async_poll_connectable())
        self.async_update_listeners()

    async def _async_poll_connectable(self) -> None:
        """Drain the dump when a live sighting says a GATT poll is due."""
        if self.hass.state is not CoreState.running:
            return
        if self.device_data._poll_lock.locked():
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
        measurements = await self.device_data.async_poll(connectable_device)
        if isinstance(measurements, dict):
            self.async_publish_measurements(measurements)
        else:
            self.async_publish_measurements(
                {user: self.measurement_for(user) for user in _CUFF_USERS}
            )

    def _async_needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        last_poll: float | None,
    ) -> bool:
        """Poll when HA is running, a connectable path exists, and the dump is due."""
        if self.hass.state is not CoreState.running:
            return False
        needed = self.device_data.poll_needed(service_info, last_poll)
        self._ensure_grace_timer()
        if not needed:
            return False
        if async_ble_device_from_address(
            self.hass, service_info.device.address, connectable=True
        ):
            return True
        _LOGGER.debug(
            "Skipping poll for %s: no connectable BLEDevice",
            service_info.device.address,
        )
        return False

    async def _async_poll_service(
        self, service_info: BluetoothServiceInfoBleak
    ) -> dict[int, BloodPressureMeasurement | None]:
        """Resolve a connectable BLEDevice and drain the dump for both slots."""
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
        _LOGGER.debug("Polling %s (both user slots)", connectable_device.address)
        measurements = await self.device_data.async_poll(connectable_device)
        if isinstance(measurements, dict):
            return measurements
        return {user: self.measurement_for(user) for user in _CUFF_USERS}

    async def async_force_poll(
        self,
    ) -> dict[int, BloodPressureMeasurement | None]:
        """Connect now and drain the dump, ignoring advertise-window skip."""
        connectable_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if connectable_device is None:
            raise CuffNotConnectableError(
                f"No connectable BPU26 at {self.address}. "
                "Press User 1 or User 2 so Bluetooth flashes."
            )
        _LOGGER.info("Force dump %s (both user slots)", connectable_device.address)
        measurements = await self.device_data.async_force_poll(connectable_device)
        if not isinstance(measurements, dict):
            measurements = {user: self.measurement_for(user) for user in _CUFF_USERS}
        self.async_publish_measurements(measurements)
        return measurements

    @callback
    def async_publish_measurements(
        self, measurements: dict[int, BloodPressureMeasurement | None]
    ) -> None:
        """Push both slots' dump results to entities.

        ``ActiveBluetoothDataUpdateCoordinator`` is not a
        ``DataUpdateCoordinator`` and has no ``async_set_updated_data``.
        """
        self.data = measurements
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
        self.async_update_listeners()


type VerovalBleConfigEntry = ConfigEntry[VerovalBleCoordinator]
