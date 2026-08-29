"""The Veroval Blood Pressure BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .bluez_pair import async_unpair_address
from .const import CONF_CUFF_USER, DOMAIN
from .coordinator import (
    VerovalBleConfigEntry,
    VerovalBleCoordinator,
    VerovalBleDeviceData,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _entry_address(entry: ConfigEntry) -> str:
    """BLE address from entry data, or unique_id before the cuff-user suffix."""
    address = entry.data.get(CONF_ADDRESS)
    if address:
        return str(address)
    unique_id = entry.unique_id
    assert unique_id is not None
    return unique_id.rsplit("_", 1)[0]


def _other_entries_for_address(
    hass: HomeAssistant, entry: ConfigEntry, address: str
) -> list[ConfigEntry]:
    """Config entries that share *address* besides *entry*."""
    return [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        and _entry_address(other).lower() == address
    ]


def _device_data_for_address(
    hass: HomeAssistant, address: str
) -> VerovalBleDeviceData:
    """Return the shared GATT dump state for one cuff MAC."""
    domain_data: dict[str, VerovalBleDeviceData] = hass.data.setdefault(
        DOMAIN, {}
    )
    key = address.lower()
    device_data = domain_data.get(key)
    if device_data is None:
        device_data = VerovalBleDeviceData()
        domain_data[key] = device_data
    return device_data


async def async_setup_entry(hass: HomeAssistant, entry: VerovalBleConfigEntry) -> bool:
    """Set up Veroval BLE from a config entry."""
    address = _entry_address(entry)
    cuff_user = int(entry.data[CONF_CUFF_USER])
    _LOGGER.info("Setting up BPU26 %s User %s", address, cuff_user)

    device_data = _device_data_for_address(hass, address)
    coordinator = VerovalBleCoordinator(
        hass,
        address=address,
        cuff_user=cuff_user,
        device_data=device_data,
    )
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        # Start after platforms have subscribed to coordinator updates.
        coordinator.async_start()
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VerovalBleConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    address = _entry_address(entry).lower()
    if not _other_entries_for_address(hass, entry, address):
        domain_data = hass.data.get(DOMAIN)
        if isinstance(domain_data, dict):
            domain_data.pop(address, None)
            if not domain_data:
                hass.data.pop(DOMAIN, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the host BlueZ bond when the last entry for this cuff is deleted."""
    address = _entry_address(entry).lower()
    remaining = _other_entries_for_address(hass, entry, address)
    if remaining:
        _LOGGER.debug(
            "Keeping BlueZ bond for %s; %s other Veroval entry(ies) remain",
            address,
            len(remaining),
        )
        return

    _LOGGER.debug("Last Veroval entry for %s deleted; removing BlueZ bond", address)
    await async_unpair_address(address)
