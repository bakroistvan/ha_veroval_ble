"""Unit tests for the Force data sync button (no Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_button_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


class _FakeHomeAssistantError(Exception):
    pass


class _CuffNotConnectableError(Exception):
    pass


def _stub_homeassistant() -> None:
    if "homeassistant.components.button" in sys.modules:
        return

    ha = sys.modules.get("homeassistant") or ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    button = ModuleType("homeassistant.components.button")
    const = sys.modules.get("homeassistant.const") or ModuleType("homeassistant.const")
    helpers = sys.modules.get("homeassistant.helpers") or ModuleType(
        "homeassistant.helpers"
    )
    device_registry = sys.modules.get(
        "homeassistant.helpers.device_registry"
    ) or ModuleType("homeassistant.helpers.device_registry")
    entity_mod = sys.modules.get("homeassistant.helpers.entity") or ModuleType(
        "homeassistant.helpers.entity"
    )
    update_coordinator = sys.modules.get(
        "homeassistant.helpers.update_coordinator"
    ) or ModuleType("homeassistant.helpers.update_coordinator")
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    core = sys.modules.get("homeassistant.core") or ModuleType("homeassistant.core")
    exceptions = sys.modules.get("homeassistant.exceptions") or ModuleType(
        "homeassistant.exceptions"
    )

    class ButtonEntity:
        pass

    class ButtonEntityDescription:
        def __init__(self, key: str, **kwargs: object) -> None:
            self.key = key
            for name, value in kwargs.items():
                setattr(self, name, value)

    button.ButtonEntity = ButtonEntity
    button.ButtonEntityDescription = ButtonEntityDescription

    device_registry.DeviceInfo = getattr(
        device_registry, "DeviceInfo", lambda **kwargs: kwargs
    )
    device_registry.CONNECTION_BLUETOOTH = getattr(
        device_registry, "CONNECTION_BLUETOOTH", "bluetooth"
    )

    class EntityDescription:
        def __init__(self, key: str, **kwargs: object) -> None:
            self.key = key
            for name, value in kwargs.items():
                setattr(self, name, value)

    entity_mod.EntityDescription = getattr(
        entity_mod, "EntityDescription", EntityDescription
    )

    class CoordinatorEntity:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

        def __init__(self, coordinator: object) -> None:
            self.coordinator = coordinator

    update_coordinator.CoordinatorEntity = getattr(
        update_coordinator, "CoordinatorEntity", CoordinatorEntity
    )
    entity_platform.AddEntitiesCallback = object
    core.HomeAssistant = getattr(core, "HomeAssistant", type("HomeAssistant", (), {}))
    exceptions.HomeAssistantError = getattr(
        exceptions, "HomeAssistantError", _FakeHomeAssistantError
    )

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.button"] = button
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.entity"] = entity_mod
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.exceptions"] = exceptions


def _load_button() -> ModuleType:
    _stub_homeassistant()
    full = f"{_PKG}.button"
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
    coordinator.CuffNotConnectableError = _CuffNotConnectableError
    sys.modules[f"{_PKG}.coordinator"] = coordinator

    entity_spec = importlib.util.spec_from_file_location(
        f"{_PKG}.entity", _ROOT / "entity.py"
    )
    assert entity_spec is not None and entity_spec.loader is not None
    entity = importlib.util.module_from_spec(entity_spec)
    entity.__package__ = _PKG
    sys.modules[f"{_PKG}.entity"] = entity
    entity_spec.loader.exec_module(entity)

    spec = importlib.util.spec_from_file_location(full, _ROOT / "button.py")
    assert spec is not None and spec.loader is not None
    button_mod = importlib.util.module_from_spec(spec)
    button_mod.__package__ = _PKG
    sys.modules[full] = button_mod
    spec.loader.exec_module(button_mod)
    return button_mod


_button = _load_button()
VerovalBleForceDumpButton = _button.VerovalBleForceDumpButton
FORCE_DUMP_DESCRIPTION = _button.FORCE_DUMP_DESCRIPTION
HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError


def test_force_dump_button_unique_id_is_device_level() -> None:
    coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
    button = VerovalBleForceDumpButton(coordinator, FORCE_DUMP_DESCRIPTION)
    assert button._attr_unique_id == "aa:bb:cc:dd:ee:ff_force_dump"
    assert FORCE_DUMP_DESCRIPTION.key == "force_dump"
    assert FORCE_DUMP_DESCRIPTION.translation_key == "force_dump"


def test_press_calls_force_poll() -> None:
    calls = {"n": 0}

    async def async_force_poll() -> dict:
        calls["n"] += 1
        return {1: None, 2: None}

    coordinator = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        async_force_poll=async_force_poll,
    )
    button = VerovalBleForceDumpButton(coordinator, FORCE_DUMP_DESCRIPTION)
    asyncio.run(button.async_press())
    assert calls["n"] == 1


def test_button_available_without_last_update_success() -> None:
    coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", available=False)
    button = VerovalBleForceDumpButton(coordinator, FORCE_DUMP_DESCRIPTION)
    assert button.available is True


def test_press_raises_when_cuff_not_connectable() -> None:
    async def async_force_poll() -> dict:
        raise _CuffNotConnectableError(
            "No connectable BPU26 at AA:BB:CC:DD:EE:FF. "
            "Press User 1 or User 2 so Bluetooth flashes."
        )

    coordinator = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        async_force_poll=async_force_poll,
    )
    button = VerovalBleForceDumpButton(coordinator, FORCE_DUMP_DESCRIPTION)
    try:
        asyncio.run(button.async_press())
    except HomeAssistantError as err:
        assert "AA:BB:CC:DD:EE:FF" in str(err)
    else:
        raise AssertionError("expected HomeAssistantError")
