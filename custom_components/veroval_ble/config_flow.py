"""Config flow for Veroval Blood Pressure BLE."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .bluez_pair import (
    AgentUnavailableError,
    BlueZPairSession,
    DeviceNotFoundError,
    PairingError,
    PairingFailedError,
    PairingNotSupportedError,
    ProxyNotSupportedError,
    bluez_path_from_device,
    format_pairing_error,
    is_bluez_pairing_supported,
    is_local_bluez_device,
)
from .const import (
    CONF_CUFF_USER,
    CONF_PIN,
    DOMAIN,
    LOCAL_NAME,
    MANUFACTURER_ID,
    SCAN_TIMEOUT_SECONDS,
)
from .parser import CUFF_USER_1, CUFF_USER_2

_LOGGER = logging.getLogger(__name__)

# HA's scanner cache can outlive BlueZ Device1 objects for unpaired devices.
ADVERTISEMENT_MAX_AGE_SECONDS = 30.0


def _is_bpu26(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this advertisement looks like a Veroval BPU26."""
    name = service_info.name or ""
    if name == LOCAL_NAME or name.startswith(LOCAL_NAME):
        return True
    return MANUFACTURER_ID in service_info.manufacturer_data


def _is_fresh_advertisement(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this advertisement is recent enough that BlueZ may still know it."""
    ad_time = getattr(service_info, "time", None)
    if not isinstance(ad_time, (int, float)):
        return False
    age = time.monotonic() - ad_time
    return 0 <= age <= ADVERTISEMENT_MAX_AGE_SECONDS


def _is_host_adapter_advertisement(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if the advertisement was seen on the local BlueZ adapter."""
    return is_local_bluez_device(service_info.device)


def _normalize_address(value: str) -> str | None:
    """Return a lowercase colon-separated MAC, or None if invalid."""
    compact = value.strip().replace("-", "").replace(":", "").replace(" ", "").lower()
    if len(compact) != 12 or any(c not in "0123456789abcdef" for c in compact):
        return None
    return ":".join(compact[i : i + 2] for i in range(0, 12, 2))


class VerovalBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veroval BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._name: str | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._pair_session: BlueZPairSession | None = None
        self._pair_wait_task: asyncio.Task[str] | None = None
        self._pair_finish_task: asyncio.Task[None] | None = None
        self._pair_outcome: str | None = None
        self._pair_error_reason: str | None = None
        self._pair_error_detail: str | None = None
        self._not_found_error: str | None = None

    def _configured_slots(self, address: str) -> set[int]:
        """Return cuff-user slots already set up for this BLE address."""
        slots: set[int] = set()
        target = address.lower()
        for entry in self._async_current_entries():
            entry_address = entry.data.get(CONF_ADDRESS)
            if not entry_address and entry.unique_id:
                entry_address = entry.unique_id.rsplit("_", 1)[0]
            if not entry_address or str(entry_address).lower() != target:
                continue
            slots.add(int(entry.data[CONF_CUFF_USER]))
        return slots

    def _available_slots(self, address: str) -> dict[int, str]:
        """Return User 1/2 options that are not already configured."""
        configured = self._configured_slots(address)
        return {
            slot: f"User {slot}"
            for slot in (CUFF_USER_1, CUFF_USER_2)
            if slot not in configured
        }

    def _resolve_local_device_path(self) -> str | None:
        """Return a local BlueZ object path for the selected address, if any."""
        assert self._address is not None
        device = async_ble_device_from_address(self.hass, self._address, True)
        if device is not None:
            if is_local_bluez_device(device):
                return bluez_path_from_device(device)
            raise ProxyNotSupportedError()

        discovery = self._discovery_info or self._discovered_devices.get(self._address)
        if discovery is not None:
            if is_local_bluez_device(discovery.device):
                return bluez_path_from_device(discovery.device)
            raise ProxyNotSupportedError()

        # Typed address with no cached advertisement: ObjectManager may still find it.
        return None

    async def _async_close_pair_session(self) -> None:
        """Tear down any in-flight BlueZ pairing session.

        Do not clear ``_pair_wait_task`` / ``_pair_finish_task`` here. Those
        handles belong to the progress steps: Home Assistant re-enters the
        same step after the background task completes, and must still see
        the original task. Clearing them causes a second finish task to be
        started against an already-closed session.
        """
        session = self._pair_session
        self._pair_session = None
        if session is not None:
            await session.close()

    def _abort_pairing_failed(self, detail: str = "") -> ConfigFlowResult:
        """Abort setup and include the BlueZ/D-Bus error in the UI and logs."""
        error = (detail or self._pair_error_detail or "").strip() or "unknown error"
        _LOGGER.warning("Aborting pairing for %s: %s", self._address, error)
        return self.async_abort(
            reason="pairing_failed",
            description_placeholders={"error": error},
        )

    def _record_pairing_error(
        self, err: BaseException, reason: str = "pairing_failed"
    ) -> None:
        """Remember the abort reason and a human-readable BlueZ detail."""
        self._pair_error_reason = reason
        self._pair_error_detail = format_pairing_error(err)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery: confirm the BPU26."""
        _LOGGER.debug(
            "Bluetooth discovery %s (%s)",
            discovery_info.name,
            discovery_info.address,
        )
        if not _is_bpu26(discovery_info):
            return self.async_abort(reason="not_supported")

        address = discovery_info.address.lower()
        await self.async_set_unique_id(address)
        if self._configured_slots(address) >= {CUFF_USER_1, CUFF_USER_2}:
            return self.async_abort(reason="already_configured")

        self._discovery_info = discovery_info
        self._address = address
        self._name = discovery_info.name or LOCAL_NAME
        self.context["title_placeholders"] = {
            "name": self._name,
            "address": address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered cuff, then start in-UI pairing."""
        assert self._address is not None
        if user_input is not None:
            self._collect_discovered()
            current = self._discovered_devices.get(self._address)
            if current is None:
                current = self._discovery_info
            if (
                current is not None
                and _is_host_adapter_advertisement(current)
                and _is_fresh_advertisement(current)
            ):
                self._discovery_info = current
                return await self.async_step_pairing()
            _LOGGER.debug(
                "Discovery for %s is stale or not from the host adapter; scanning",
                self._address,
            )
            return await self.async_step_scan()

        self._set_confirm_only()
        placeholders = {
            "name": self._name or LOCAL_NAME,
            "address": self._address,
        }
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
        )

    def _collect_discovered(self) -> None:
        """Cache fresh BPU26 advertisements from the host adapter only."""
        for discovery_info in async_discovered_service_info(self.hass, False):
            if not _is_bpu26(discovery_info):
                continue
            if not _is_host_adapter_advertisement(discovery_info):
                continue
            if not _is_fresh_advertisement(discovery_info):
                continue
            address = discovery_info.address.lower()
            if address in self._discovered_devices:
                continue
            if self._configured_slots(address) >= {CUFF_USER_1, CUFF_USER_2}:
                continue
            self._discovered_devices[address] = discovery_info

    def _discovered_titles(self) -> dict[str, str]:
        """Return address → label for cuffs that still have a free user slot."""
        current_full = {
            entry.unique_id
            for entry in self._async_current_entries()
            if entry.unique_id
        }
        return {
            address: f"{info.name or LOCAL_NAME} ({address})"
            for address, info in self._discovered_devices.items()
            if not (
                f"{address}_{CUFF_USER_1}" in current_full
                and f"{address}_{CUFF_USER_2}" in current_full
            )
        }

    async def _async_choose_address(self, address: str) -> ConfigFlowResult:
        """Continue setup for a discovered or typed BLE address."""
        discovery = self._discovered_devices.get(address)
        self._discovery_info = discovery
        self._address = address
        self._name = (discovery.name if discovery else None) or LOCAL_NAME
        await self.async_set_unique_id(address, raise_on_progress=False)
        if self._configured_slots(address) >= {CUFF_USER_1, CUFF_USER_2}:
            return self.async_abort(reason="already_configured")
        _LOGGER.debug("User picked %s (%s)", self._name, address)
        return await self.async_step_pairing()

    async def _async_next_after_scan(self) -> ConfigFlowResult:
        """Picker, pairing, or the Scan again screen after a timed scan."""
        self._collect_discovered()
        titles = self._discovered_titles()
        if not titles:
            if self._discovered_devices:
                return self.async_abort(reason="already_configured")
            _LOGGER.debug("Scan timed out with no BPU26 advertisements")
            return await self.async_step_not_found()
        if len(titles) == 1:
            return await self._async_choose_address(next(iter(titles)))
        return await self.async_step_pick_device()

    async def _async_scan_for_cuffs(self) -> None:
        """Listen for BPU26 advertisements until one arrives or time runs out."""
        found = asyncio.Event()

        @callback
        def _on_advertisement(
            service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
        ) -> None:
            if not _is_bpu26(service_info):
                return
            if not _is_host_adapter_advertisement(service_info):
                _LOGGER.debug(
                    "Ignoring non-host advertisement for %s", service_info.address
                )
                return
            address = service_info.address.lower()
            if self._configured_slots(address) >= {CUFF_USER_1, CUFF_USER_2}:
                return
            self._discovered_devices[address] = service_info
            found.set()

        unsubscribers = [
            async_register_callback(
                self.hass,
                _on_advertisement,
                {"local_name": LOCAL_NAME},
                BluetoothScanningMode.ACTIVE,
            ),
            async_register_callback(
                self.hass,
                _on_advertisement,
                {"manufacturer_id": MANUFACTURER_ID},
                BluetoothScanningMode.ACTIVE,
            ),
        ]
        try:
            self._discovered_devices.clear()
            self._collect_discovered()
            if self._discovered_devices:
                self.async_update_progress(1.0)
                return
            started = time.monotonic()
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= SCAN_TIMEOUT_SECONDS or found.is_set():
                    break
                self.async_update_progress(elapsed / SCAN_TIMEOUT_SECONDS)
                remaining = SCAN_TIMEOUT_SECONDS - elapsed
                try:
                    await asyncio.wait_for(found.wait(), timeout=min(1.0, remaining))
                    break
                except TimeoutError:
                    continue
            self._collect_discovered()
        finally:
            self.async_update_progress(1.0)
            for unsub in unsubscribers:
                unsub()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show wake-cuff instructions, then start a timed scan."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "seconds": str(SCAN_TIMEOUT_SECONDS),
                },
            )
        return await self.async_step_scan()

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a progress bar while listening for BPU26 advertisements."""
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                self._async_scan_for_cuffs(),
                f"{DOMAIN}_scan",
            )

        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scanning",
                description_placeholders={"seconds": str(SCAN_TIMEOUT_SECONDS)},
                progress_task=self._scan_task,
            )

        try:
            await self._scan_task
        finally:
            self._scan_task = None
        return self.async_show_progress_done(next_step_id="scan_done")

    async def async_step_scan_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue after the timed scan finishes or finds a cuff."""
        return await self._async_next_after_scan()

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick among several discovered cuffs."""
        if user_input is not None:
            return await self._async_choose_address(
                str(user_input[CONF_ADDRESS]).lower()
            )
        titles = self._discovered_titles()
        if not titles:
            return await self.async_step_not_found()
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(titles)}),
        )

    async def async_step_not_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """After the scan timeout: Scan again, or paste a bluetoothctl address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            typed = str(user_input.get(CONF_ADDRESS) or "").strip()
            if typed:
                address = typed.lower()
                if address not in self._discovered_devices:
                    normalized = _normalize_address(typed)
                    if normalized is None:
                        errors[CONF_ADDRESS] = "invalid_address"
                    else:
                        address = normalized
                if CONF_ADDRESS not in errors:
                    return await self._async_choose_address(address)
            else:
                return await self.async_step_scan()

        if self._not_found_error:
            errors.setdefault("base", self._not_found_error)
            self._not_found_error = None

        return self.async_show_form(
            step_id="not_found",
            data_schema=vol.Schema({vol.Optional(CONF_ADDRESS): str}),
            errors=errors,
            description_placeholders={"seconds": str(SCAN_TIMEOUT_SECONDS)},
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start host-adapter BlueZ pairing (or skip if already bonded)."""
        assert self._address is not None
        _LOGGER.debug("Starting UI pairing for %s", self._address)
        self._pair_error_reason = None
        self._pair_error_detail = None

        if not is_bluez_pairing_supported():
            return self.async_abort(reason="pairing_not_supported")

        try:
            device_path = self._resolve_local_device_path()
        except ProxyNotSupportedError:
            return self.async_abort(reason="proxy_not_supported")

        await self._async_close_pair_session()
        session = BlueZPairSession(self._address, device_path=device_path)
        self._pair_session = session
        try:
            await session.open()
        except AgentUnavailableError as err:
            self._record_pairing_error(err, "agent_unavailable")
            _LOGGER.warning(
                "BlueZ pairing agent unavailable for %s: %s",
                self._address,
                self._pair_error_detail,
            )
            await self._async_close_pair_session()
            return self.async_abort(reason="agent_unavailable")
        except PairingNotSupportedError:
            await self._async_close_pair_session()
            return self.async_abort(reason="pairing_not_supported")
        except DeviceNotFoundError:
            await self._async_close_pair_session()
            _LOGGER.debug(
                "BlueZ has no device object for %s; returning to scan", self._address
            )
            self._not_found_error = "device_not_found"
            return await self.async_step_not_found()
        except ProxyNotSupportedError:
            await self._async_close_pair_session()
            return self.async_abort(reason="proxy_not_supported")
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Failed to open BlueZ pairing session")
            await self._async_close_pair_session()
            return self._abort_pairing_failed(format_pairing_error(err))

        if session.already_paired:
            await self._async_close_pair_session()
            _LOGGER.debug("Already paired; continuing to user slot")
            return await self.async_step_user_slot()

        return await self.async_step_pair_wait()

    async def _async_run_pair_wait(self) -> str:
        """Background work for the connecting progress step."""
        session = self._pair_session
        if session is None:
            err = PairingFailedError("pairing session was closed")
            self._record_pairing_error(err)
            raise err
        await session.start_pair()
        try:
            return await session.wait_for_passkey_or_done()
        except PairingError as err:
            self._record_pairing_error(err, err.reason)
            raise
        except Exception as err:
            self._record_pairing_error(err)
            raise PairingFailedError(str(err)) from err

    async def async_step_pair_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress: connecting and waiting for the cuff to show a PIN."""
        assert self._address is not None
        first_visit = self._pair_wait_task is None
        if first_visit:
            self._pair_error_reason = None
            self._pair_error_detail = None
            self._pair_wait_task = self.hass.async_create_task(
                self._async_run_pair_wait(),
                f"{DOMAIN}_pair_wait",
                eager_start=False,
            )

        if first_visit or not self._pair_wait_task.done():
            return self.async_show_progress(
                step_id="pair_wait",
                progress_action="connecting",
                description_placeholders={
                    "name": self._name or LOCAL_NAME,
                    "address": self._address,
                },
                progress_task=self._pair_wait_task,
            )

        try:
            self._pair_outcome = await self._pair_wait_task
        except PairingError as err:
            self._pair_outcome = "failed"
            self._record_pairing_error(err, err.reason)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("pair_wait failed")
            self._pair_outcome = "failed"
            self._record_pairing_error(err)
        finally:
            self._pair_wait_task = None
        return self.async_show_progress_done(next_step_id="pair_wait_done")

    async def async_step_pair_wait_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Route after connecting: PIN form, user slot, or error."""
        outcome = self._pair_outcome
        if outcome == "need_pin":
            return await self.async_step_enter_pin()
        if outcome in {"done", "already_paired"}:
            await self._async_close_pair_session()
            return await self.async_step_user_slot()
        reason = self._pair_error_reason or "pairing_failed"
        await self._async_close_pair_session()
        if reason == "pairing_failed":
            return self._abort_pairing_failed()
        _LOGGER.warning(
            "Aborting pairing for %s: %s (%s)",
            self._address,
            reason,
            self._pair_error_detail or "",
        )
        return self.async_abort(reason=reason)

    async def async_step_enter_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the 6-digit PIN shown on the cuff."""
        assert self._address is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            pin = str(user_input.get(CONF_PIN) or "").strip()
            if not pin.isdigit() or not 4 <= len(pin) <= 6:
                errors[CONF_PIN] = "invalid_pin"
            elif self._pair_finish_task is not None or self._pair_outcome == "done":
                return await self.async_step_pair_finish()
            elif self._pair_session is None:
                return self._abort_pairing_failed("pairing session was closed")
            else:
                try:
                    self._pair_session.provide_passkey(pin)
                except ValueError:
                    errors[CONF_PIN] = "invalid_pin"
                except PairingError as err:
                    await self._async_close_pair_session()
                    return self._abort_pairing_failed(format_pairing_error(err))
                else:
                    return await self.async_step_pair_finish()

        return self.async_show_form(
            step_id="enter_pin",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            errors=errors,
            description_placeholders={
                "name": self._name or LOCAL_NAME,
                "address": self._address,
            },
        )

    async def _async_run_pair_finish(self) -> None:
        """Background work after the PIN was submitted."""
        session = self._pair_session
        if session is None:
            raise PairingFailedError("pairing session was closed")
        try:
            await session.wait_finished()
            self._pair_outcome = "done"
        except PairingError as err:
            self._record_pairing_error(err, err.reason)
            raise
        except Exception as err:
            self._record_pairing_error(err)
            raise PairingFailedError(str(err)) from err
        finally:
            await self._async_close_pair_session()

    async def _async_pair_finish_noop(self) -> None:
        """No-op finish task so a second PIN submit still shows progress first."""

    async def async_step_pair_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress: finish bonding after the PIN was entered."""
        assert self._address is not None
        first_visit = self._pair_finish_task is None
        if first_visit:
            if self._pair_outcome == "done":
                self._pair_finish_task = self.hass.async_create_task(
                    self._async_pair_finish_noop(),
                    f"{DOMAIN}_pair_finish",
                    eager_start=False,
                )
            else:
                self._pair_error_reason = None
                self._pair_error_detail = None
                self._pair_finish_task = self.hass.async_create_task(
                    self._async_run_pair_finish(),
                    f"{DOMAIN}_pair_finish",
                    eager_start=False,
                )

        if first_visit or not self._pair_finish_task.done():
            return self.async_show_progress(
                step_id="pair_finish",
                progress_action="finishing",
                description_placeholders={
                    "name": self._name or LOCAL_NAME,
                    "address": self._address,
                },
                progress_task=self._pair_finish_task,
            )

        try:
            await self._pair_finish_task
            self._pair_outcome = "done"
        except PairingError as err:
            self._pair_outcome = "failed"
            self._record_pairing_error(err, err.reason)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("pair_finish failed")
            self._pair_outcome = "failed"
            self._record_pairing_error(err)
        finally:
            self._pair_finish_task = None
        return self.async_show_progress_done(next_step_id="pair_finish_done")

    async def async_step_pair_finish_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Route after bonding finishes."""
        if self._pair_outcome == "done":
            return await self.async_step_user_slot()
        reason = self._pair_error_reason or "pairing_failed"
        if reason == "pairing_failed":
            return self._abort_pairing_failed()
        _LOGGER.warning(
            "Aborting pairing for %s: %s (%s)",
            self._address,
            reason,
            self._pair_error_detail or "",
        )
        return self.async_abort(reason=reason)

    async def async_step_user_slot(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Required User 1 or User 2 slot. unique_id is address_cuff_user."""
        assert self._address is not None
        address = self._address
        options = self._available_slots(address)
        if not options:
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            cuff_user = int(user_input[CONF_CUFF_USER])
            if cuff_user not in options:
                return self.async_abort(reason="already_configured")
            unique_id = f"{address}_{cuff_user}"
            await self.async_set_unique_id(unique_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            _LOGGER.debug(
                "Creating entry unique_id=%s cuff_user=%s", unique_id, cuff_user
            )
            return self.async_create_entry(
                title=f"BPU26 User {cuff_user}",
                data={
                    CONF_ADDRESS: address,
                    CONF_CUFF_USER: cuff_user,
                },
            )

        return self.async_show_form(
            step_id="user_slot",
            data_schema=vol.Schema(
                {vol.Required(CONF_CUFF_USER): vol.In(options)}
            ),
            description_placeholders={
                "name": self._name or LOCAL_NAME,
                "address": address,
            },
        )
