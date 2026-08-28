"""The Veroval Blood Pressure BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_CUFF_USER
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


async def async_setup_entry(hass: HomeAssistant, entry: VerovalBleConfigEntry) -> bool:
    """Set up Veroval BLE from a config entry."""
    address = _entry_address(entry)
    cuff_user = int(entry.data[CONF_CUFF_USER])
    _LOGGER.info("Setting up BPU26 %s User %s", address, cuff_user)

    device_data = VerovalBleDeviceData()
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
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
