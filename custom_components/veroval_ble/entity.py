"""Shared entity base for Veroval Blood Pressure BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
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
        cuff_user: int | None = None,
    ) -> None:
        """Initialize the entity.

        Pass *cuff_user* for per-slot measurement entities so unique ids stay
        ``{address}_{cuff_user}_{key}``. Device-level diagnostics omit it.
        """
        super().__init__(coordinator)
        self.entity_description = description
        self.cuff_user = cuff_user
        address = coordinator.address.lower()
        if cuff_user is not None:
            self._attr_unique_id = f"{address}_{cuff_user}_{description.key}"
            self._attr_translation_placeholders = {"user": str(cuff_user)}
        else:
            self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name="BPU26",
            manufacturer="PAUL HARTMANN AG",
            model="Veroval compact+ BPU 26",
        )

    @property
    def available(self) -> bool:
        """Do not use CoordinatorEntity.available (needs last_update_success).

        ``ActiveBluetoothDataUpdateCoordinator`` is not a
        ``DataUpdateCoordinator``. Measurement entities override this for
        sleepy-cuff semantics; the force-sync button stays available.
        """
        return True

    @property
    def assumed_state(self) -> bool:
        """True when the cuff is not currently advertising."""
        return not self.coordinator.available
