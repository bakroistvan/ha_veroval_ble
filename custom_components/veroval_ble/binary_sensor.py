"""Binary sensors for Veroval Blood Pressure BLE."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VerovalBleConfigEntry
from .entity import VerovalBleEntity

IRREGULAR_PULSE_DESCRIPTION = BinarySensorEntityDescription(
    key="irregular_pulse",
    translation_key="irregular_pulse",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:heart-pulse",
)

CONNECTED_DESCRIPTION = BinarySensorEntityDescription(
    key="connected",
    translation_key="connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    icon="mdi:bluetooth-connect",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VerovalBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veroval BLE binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            VerovalBleIrregularPulseSensor(coordinator, IRREGULAR_PULSE_DESCRIPTION),
            VerovalBleConnectedSensor(coordinator, CONNECTED_DESCRIPTION),
        ]
    )


class VerovalBleIrregularPulseSensor(VerovalBleEntity, BinarySensorEntity):
    """Irregular pulse from measurement status bit 2 (not atrial fibrillation)."""

    @property
    def available(self) -> bool:
        """Stay available after a successful poll (sleepy cuff)."""
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        """Return True when the selected record has irregular pulse set."""
        measurement = self.coordinator.data
        if measurement is None:
            return None
        return measurement.irregular_pulse


class VerovalBleConnectedSensor(VerovalBleEntity, BinarySensorEntity):
    """On while the Home Assistant host adapter has a GATT link to the cuff."""

    @property
    def available(self) -> bool:
        """Always shown so Off means the host is not connected."""
        return True

    @property
    def assumed_state(self) -> bool:
        """Off is a real disconnect, not a stale last value."""
        return False

    @property
    def is_on(self) -> bool:
        """Return True while BlueZ Device1 Connected is yes, or a dump is running."""
        return self.coordinator.is_connected
