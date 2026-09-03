"""Unit tests for VerovalBleEntity DeviceInfo (no Home Assistant)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_entity_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"
_CONNECTION_BLUETOOTH = "bluetooth"


def _stub_homeassistant() -> None:
    if "homeassistant.helpers.device_registry" in sys.modules:
        return

    ha = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    entity_mod = ModuleType("homeassistant.helpers.entity")
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")

    def device_info(**kwargs):
        return kwargs

    device_registry.DeviceInfo = device_info
    device_registry.CONNECTION_BLUETOOTH = _CONNECTION_BLUETOOTH

    class EntityDescription:
        def __init__(self, key: str) -> None:
            self.key = key

    entity_mod.EntityDescription = EntityDescription

    class CoordinatorEntity:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

        def __init__(self, coordinator: object) -> None:
            self.coordinator = coordinator

    update_coordinator.CoordinatorEntity = CoordinatorEntity

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.entity"] = entity_mod
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator


def _load_entity() -> ModuleType:
    _stub_homeassistant()
    full = f"{_PKG}.entity"
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
    sys.modules[f"{_PKG}.coordinator"] = coordinator

    spec = importlib.util.spec_from_file_location(full, _ROOT / "entity.py")
    assert spec is not None and spec.loader is not None
    entity = importlib.util.module_from_spec(spec)
    entity.__package__ = _PKG
    sys.modules[full] = entity
    spec.loader.exec_module(entity)
    return entity


_entity = _load_entity()
VerovalBleEntity = _entity.VerovalBleEntity
EntityDescription = sys.modules["homeassistant.helpers.entity"].EntityDescription
DOMAIN = sys.modules[f"{_PKG}.const"].DOMAIN


def _entity_for(address: str, cuff_user: int | None, key: str = "systolic"):
    coordinator = SimpleNamespace(address=address)
    return VerovalBleEntity(coordinator, EntityDescription(key), cuff_user)


def _device_info(address: str, cuff_user: int | None = 1) -> dict:
    return _entity_for(address, cuff_user)._attr_device_info


def _connection_values(device_info: dict) -> set[str]:
    connections = device_info.get("connections") or set()
    return {value for _kind, value in connections}


def test_device_info_is_one_cuff_device() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    info = _device_info(address, cuff_user=1)
    assert info["identifiers"] == {(DOMAIN, address.lower())}
    assert info["name"] == "BPU26"
    assert (_CONNECTION_BLUETOOTH, address.lower()) in info["connections"]


def test_device_info_connections_include_bluetooth_mac() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    info = _device_info(address, cuff_user=2)
    assert address.lower() in _connection_values(info)


def test_user_slots_share_device_identifiers() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    user1 = _device_info(address, cuff_user=1)
    user2 = _device_info(address, cuff_user=2)
    assert user1["identifiers"] == user2["identifiers"] == {(DOMAIN, address.lower())}
    shared_connections = _connection_values(user1) & _connection_values(user2)
    assert address.lower() in shared_connections


def test_measurement_unique_ids_keep_cuff_user() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    user1 = _entity_for(address, 1, "systolic")
    user2 = _entity_for(address, 2, "systolic")
    assert user1._attr_unique_id == "aa:bb:cc:dd:ee:ff_1_systolic"
    assert user2._attr_unique_id == "aa:bb:cc:dd:ee:ff_2_systolic"
    assert user1._attr_translation_placeholders == {"user": "1"}
    assert user2._attr_translation_placeholders == {"user": "2"}


def test_device_level_unique_id_omits_cuff_user() -> None:
    entity = _entity_for("AA:BB:CC:DD:EE:FF", None, "connected")
    assert entity._attr_unique_id == "aa:bb:cc:dd:ee:ff_connected"
    assert not hasattr(entity, "_attr_translation_placeholders") or not getattr(
        entity, "_attr_translation_placeholders", None
    )


def test_available_does_not_require_last_update_success() -> None:
    """ActiveBluetooth coordinators have no last_update_success (HA 0.3.0 crash)."""
    coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", available=False)
    entity = VerovalBleEntity(coordinator, EntityDescription("force_dump"))
    assert entity.available is True
    assert not hasattr(coordinator, "last_update_success")
