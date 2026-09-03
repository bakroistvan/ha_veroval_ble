"""The Veroval Blood Pressure BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .bluez_pair import async_unpair_address
from .const import DOMAIN, normalize_ble_address
from .coordinator import (
    VerovalBleConfigEntry,
    VerovalBleCoordinator,
    VerovalBleDeviceData,
)
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


def _entry_address(entry: ConfigEntry) -> str:
    """BLE address from entry data, or unique_id before a cuff-user suffix."""
    address = entry.data.get(CONF_ADDRESS)
    if address:
        return str(address)
    unique_id = entry.unique_id
    assert unique_id is not None
    lowered = unique_id.lower()
    if lowered.endswith("_1") or lowered.endswith("_2"):
        return unique_id.rsplit("_", 1)[0]
    return unique_id


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


def _merge_legacy_slot_devices(hass: HomeAssistant, address: str) -> None:
    """Fold 0.2.0 per-slot devices into the single cuff device."""
    registry = dr.async_get(hass)
    addr = address.lower()
    for suffix in ("1", "2"):
        device = registry.async_get_device(identifiers={(DOMAIN, f"{addr}_{suffix}")})
        if device is None:
            continue
        registry.async_update_device(
            device.id,
            merge_identifiers={(DOMAIN, addr)},
            merge_connections={(dr.CONNECTION_BLUETOOTH, addr)},
        )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a 0.2.0 per-slot entry onto one config entry per cuff."""
    if entry.version > 2:
        return False
    if entry.version != 1:
        return True

    address = normalize_ble_address(_entry_address(entry))
    addr_key = address.lower()
    siblings = _other_entries_for_address(hass, entry, addr_key)
    already_migrated = [
        other
        for other in siblings
        if other.version >= 2
        and (other.unique_id or "").lower() == addr_key
    ]
    if already_migrated:
        _LOGGER.info(
            "Removing leftover User-slot entry %s; cuff %s is already migrated",
            entry.unique_id,
            address,
        )
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return False

    _merge_legacy_slot_devices(hass, address)
    hass.config_entries.async_update_entry(
        entry,
        unique_id=addr_key,
        title="BPU26",
        data={CONF_ADDRESS: address},
        version=2,
    )
    _LOGGER.info("Migrated BPU26 %s to a single config entry", address)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VerovalBleConfigEntry) -> bool:
    """Set up Veroval BLE from a config entry."""
    address = normalize_ble_address(_entry_address(entry))
    _LOGGER.info("Setting up BPU26 %s", address)

    device_data = _device_data_for_address(hass, address)
    coordinator = VerovalBleCoordinator(
        hass,
        address=address,
        device_data=device_data,
    )
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        # Start after platforms have subscribed to coordinator updates.
        coordinator.async_start()
    )
    entry.async_on_unload(coordinator.async_start_bluez_rssi_watch())
    async_setup_services(hass)
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
    remaining = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        and getattr(other, "runtime_data", None) is not None
    ]
    if not remaining:
        async_unload_services(hass)
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
