"""Sensors for Veroval Blood Pressure BLE."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPressure,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import VerovalBleConfigEntry
from .entity import VerovalBleEntity
from .parser import BloodPressureMeasurement

type _ValueT = float | int | str | datetime | None


def _aware_timestamp(naive: datetime) -> datetime:
    """Treat the cuff clock as the Home Assistant local timezone."""
    return naive.replace(tzinfo=dt_util.get_default_time_zone())


def _systolic(measurement: BloodPressureMeasurement) -> float:
    return measurement.systolic


def _diastolic(measurement: BloodPressureMeasurement) -> float:
    return measurement.diastolic


def _pulse(measurement: BloodPressureMeasurement) -> float:
    return measurement.pulse


def _timestamp(measurement: BloodPressureMeasurement) -> datetime:
    return _aware_timestamp(measurement.timestamp)


SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="systolic",
        translation_key="systolic",
        native_unit_of_measurement=UnitOfPressure.MMHG,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-minus",
    ),
    SensorEntityDescription(
        key="diastolic",
        translation_key="diastolic",
        native_unit_of_measurement=UnitOfPressure.MMHG,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-plus",
    ),
    SensorEntityDescription(
        key="pulse",
        translation_key="pulse",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-flash",
    ),
    SensorEntityDescription(
        key="timestamp",
        translation_key="timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-time-four-outline",
        entity_registry_enabled_default=True,
    ),
)

USER_SLOT_DESCRIPTION = SensorEntityDescription(
    key="user_slot",
    translation_key="user_slot",
    icon="mdi:account",
    entity_category=EntityCategory.DIAGNOSTIC,
)

_VALUE_GETTERS: dict[str, Callable[[BloodPressureMeasurement], _ValueT]] = {
    "systolic": _systolic,
    "diastolic": _diastolic,
    "pulse": _pulse,
    "timestamp": _timestamp,
}

RSSI_DESCRIPTION = SensorEntityDescription(
    key="rssi",
    translation_key="rssi",
    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VerovalBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veroval BLE sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                VerovalBleSensor(coordinator, description)
                for description in SENSOR_DESCRIPTIONS
            ),
            VerovalBleUserSlotSensor(coordinator, USER_SLOT_DESCRIPTION),
            VerovalBleRssiSensor(coordinator, RSSI_DESCRIPTION),
        ]
    )


class VerovalBleSensor(VerovalBleEntity, SensorEntity):
    """A measurement sensor backed by the last selected dump record."""

    @property
    def available(self) -> bool:
        """Stay available after a successful poll (sleepy cuff)."""
        return self.coordinator.data is not None

    @property
    def native_value(self) -> _ValueT:
        """Return the decoded field for this sensor."""
        measurement = self.coordinator.data
        if measurement is None:
            return None
        getter = _VALUE_GETTERS[self.entity_description.key]
        return getter(measurement)


class VerovalBleUserSlotSensor(VerovalBleEntity, SensorEntity):
    """Configured cuff user slot (User 1 / User 2)."""

    @property
    def available(self) -> bool:
        """Slot comes from the config entry, not from a poll."""
        return True

    @property
    def native_value(self) -> str:
        """Return the cuff button label for this config entry."""
        return f"User {self.coordinator.cuff_user}"


class VerovalBleRssiSensor(VerovalBleEntity, SensorEntity):
    """RSSI from the last advertisement; disabled by default."""

    @property
    def available(self) -> bool:
        """RSSI is available whenever we have seen an advertisement."""
        return self.coordinator.rssi is not None

    @property
    def native_value(self) -> int | None:
        """Return last advertisement RSSI."""
        return self.coordinator.rssi
