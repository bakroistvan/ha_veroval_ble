"""Config flow for Veroval Blood Pressure BLE."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import CONF_CUFF_USER, DOMAIN, LOCAL_NAME, MANUFACTURER_ID
from .parser import CUFF_USER_1, CUFF_USER_2

_LOGGER = logging.getLogger(__name__)


def _is_bpu26(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this advertisement looks like a Veroval BPU26."""
    name = service_info.name or ""
    if name == LOCAL_NAME or name.startswith(LOCAL_NAME):
        return True
    return MANUFACTURER_ID in service_info.manufacturer_data


class VerovalBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veroval BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._name: str | None = None

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
        """Confirm the discovered cuff, then show pairing instructions."""
        assert self._address is not None
        if user_input is not None:
            return await self.async_step_pairing()

        self._set_confirm_only()
        placeholders = {
            "name": self._name or LOCAL_NAME,
            "address": self._address,
        }
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List discovered BPU26 advertisements for manual setup."""
        if user_input is not None:
            address = str(user_input[CONF_ADDRESS]).lower()
            discovery = self._discovered_devices[address]
            self._discovery_info = discovery
            self._address = address
            self._name = discovery.name or LOCAL_NAME
            await self.async_set_unique_id(address, raise_on_progress=False)
            _LOGGER.debug("User picked %s (%s)", self._name, address)
            return await self.async_step_pairing()

        current_full = {
            entry.unique_id
            for entry in self._async_current_entries()
            if entry.unique_id
        }
        for discovery_info in async_discovered_service_info(self.hass, False):
            if not _is_bpu26(discovery_info):
                continue
            address = discovery_info.address.lower()
            if address in self._discovered_devices:
                continue
            if self._configured_slots(address) >= {CUFF_USER_1, CUFF_USER_2}:
                continue
            self._discovered_devices[address] = discovery_info

        if not self._discovered_devices:
            _LOGGER.debug("User setup: no BPU26 advertisements found")
            return self.async_abort(reason="no_devices_found")

        titles = {
            address: f"{info.name or LOCAL_NAME} ({address})"
            for address, info in self._discovered_devices.items()
            if not (
                f"{address}_{CUFF_USER_1}" in current_full
                and f"{address}_{CUFF_USER_2}" in current_full
            )
        }
        if not titles:
            return self.async_abort(reason="already_configured")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(titles)}
            ),
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show host pairing instructions. No GATT connect on submit."""
        assert self._address is not None
        _LOGGER.debug("Pairing reminder for %s", self._address)
        if user_input is not None:
            return await self.async_step_user_slot()

        return self.async_show_form(
            step_id="pairing",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._name or LOCAL_NAME,
                "address": self._address,
            },
        )

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
