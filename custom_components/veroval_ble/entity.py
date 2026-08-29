"""Shared entity base for Veroval Blood Pressure BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VerovalBleCoordinator


class VerovalBleEntity(CoordinatorEntity[VerovalBleCoordinator]):
    """Device info and sleepy assumed_state for Veroval entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VerovalBleCoordinator,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        address = coordinator.address
        cuff_user = coordinator.cuff_user
        self._attr_unique_id = f"{address}_{cuff_user}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{address}_{cuff_user}")},
            name=f"BPU26 User {cuff_user}",
            manufacturer="PAUL HARTMANN AG",
            model="Veroval compact+ BPU 26",
        )

    @property
    def assumed_state(self) -> bool:
        """True when the cuff is not currently advertising."""
        return not self.coordinator.available
