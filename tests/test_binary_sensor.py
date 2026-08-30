"""Unit tests for the advertising diagnostic binary sensor (no Home Assistant)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_binary_sensor_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


def _callback(func: object) -> object:
    return func


def _stub_homeassistant() -> None:
    if "homeassistant.components.binary_sensor" in sys.modules:
        return

    ha = sys.modules.get("homeassistant") or ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    binary_sensor = ModuleType("homeassistant.components.binary_sensor")
    const = ModuleType("homeassistant.const")
    helpers = ModuleType("homeassistant.helpers")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    entity_mod = ModuleType("homeassistant.helpers.entity")
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    core = ModuleType("homeassistant.core")

    class BinarySensorDeviceClass:
        PROBLEM = "problem"

    class BinarySensorEntity:
        pass

    class BinarySensorEntityDescription:
        def __init__(self, key: str, **kwargs: object) -> None:
            self.key = key
            for name, value in kwargs.items():
                setattr(self, name, value)

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorEntityDescription = BinarySensorEntityDescription

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"

    const.EntityCategory = EntityCategory

    def device_info(**kwargs: object) -> dict:
        return kwargs

    device_registry.DeviceInfo = device_info

    class EntityDescription:
        def __init__(self, key: str, **kwargs: object) -> None:
            self.key = key
            for name, value in kwargs.items():
                setattr(self, name, value)

    entity_mod.EntityDescription = EntityDescription

    class CoordinatorEntity:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

        def __init__(self, coordinator: object) -> None:
            self.coordinator = coordinator

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    entity_platform.AddEntitiesCallback = object
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = _callback

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.binary_sensor"] = binary_sensor
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.entity"] = entity_mod
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.core"] = core


def _load_binary_sensor() -> ModuleType:
    _stub_homeassistant()
    full = f"{_PKG}.binary_sensor"
    if full in sys.modules:
        return sys.modules[full]

    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg

    const_path = _ROOT / "const.py"
    const_spec = importlib.util.spec_from_file_location(f"{_PKG}.const", const_path)
    assert const_spec is not None and const_spec.loader is not None
    const = importlib.util.module_from_spec(const_spec)
    const.__package__ = _PKG
    sys.modules[f"{_PKG}.const"] = const
    const_spec.loader.exec_module(const)

    coordinator = ModuleType(f"{_PKG}.coordinator")
    coordinator.VerovalBleCoordinator = type("VerovalBleCoordinator", (), {})
    coordinator.VerovalBleConfigEntry = object
    sys.modules[f"{_PKG}.coordinator"] = coordinator

    entity_spec = importlib.util.spec_from_file_location(
        f"{_PKG}.entity", _ROOT / "entity.py"
    )
    assert entity_spec is not None and entity_spec.loader is not None
    entity = importlib.util.module_from_spec(entity_spec)
    entity.__package__ = _PKG
    sys.modules[f"{_PKG}.entity"] = entity
    entity_spec.loader.exec_module(entity)

    spec = importlib.util.spec_from_file_location(full, _ROOT / "binary_sensor.py")
    assert spec is not None and spec.loader is not None
    binary = importlib.util.module_from_spec(spec)
    binary.__package__ = _PKG
    sys.modules[full] = binary
    spec.loader.exec_module(binary)
    return binary


_binary = _load_binary_sensor()
VerovalBleAdvertisingSensor = _binary.VerovalBleAdvertisingSensor
ADVERTISING_DESCRIPTION = _binary.ADVERTISING_DESCRIPTION


def _sensor(*, advertising: bool) -> VerovalBleAdvertisingSensor:
    coordinator = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        cuff_user=1,
        is_advertising=advertising,
        available=advertising,
    )
    return VerovalBleAdvertisingSensor(coordinator, ADVERTISING_DESCRIPTION)


def test_advertising_on_when_ads_are_captured() -> None:
    sensor = _sensor(advertising=True)
    assert sensor.available is True
    assert sensor.assumed_state is False
    assert sensor.is_on is True


def test_advertising_off_when_ads_stop() -> None:
    sensor = _sensor(advertising=False)
    assert sensor.available is True
    assert sensor.assumed_state is False
    assert sensor.is_on is False


def test_advertising_is_diagnostic() -> None:
    assert ADVERTISING_DESCRIPTION.key == "advertising"
    assert ADVERTISING_DESCRIPTION.entity_category == "diagnostic"
    sensor = _sensor(advertising=False)
    assert sensor._attr_unique_id == "aa:bb:cc:dd:ee:ff_1_advertising"
