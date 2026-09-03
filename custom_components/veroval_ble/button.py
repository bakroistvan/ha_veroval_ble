"""Buttons for Veroval Blood Pressure BLE."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CuffNotConnectableError, VerovalBleConfigEntry
from .entity import VerovalBleEntity

FORCE_DUMP_DESCRIPTION = ButtonEntityDescription(
    key="force_dump",
    translation_key="force_dump",
    icon="mdi:sync",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VerovalBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veroval BLE buttons."""
    async_add_entities(
        [VerovalBleForceDumpButton(entry.runtime_data, FORCE_DUMP_DESCRIPTION)]
    )


class VerovalBleForceDumpButton(VerovalBleEntity, ButtonEntity):
    """Connect now and drain stored measurements for both user slots."""

    async def async_press(self) -> None:
        """Force one GATT dump, ignoring advertise-window skip and phone grace."""
        try:
            await self.coordinator.async_force_poll()
        except CuffNotConnectableError as err:
            raise HomeAssistantError(str(err)) from err
