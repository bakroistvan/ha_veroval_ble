"""Unit tests for 0.2.0 → 0.3.0 config-entry migration (no Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_migrate_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"
ADDRESS = "AA:BB:CC:DD:EE:FF"
ADDRESS_LOWER = "aa:bb:cc:dd:ee:ff"


def _stub_homeassistant() -> None:
    if f"{_PKG}.init_ready" in sys.modules:
        return

    ha = sys.modules.get("homeassistant") or ModuleType("homeassistant")
    const = sys.modules.get("homeassistant.const") or ModuleType("homeassistant.const")
    config_entries = sys.modules.get("homeassistant.config_entries") or ModuleType(
        "homeassistant.config_entries"
    )
    core = sys.modules.get("homeassistant.core") or ModuleType("homeassistant.core")
    helpers = sys.modules.get("homeassistant.helpers") or ModuleType(
        "homeassistant.helpers"
    )
    device_registry = sys.modules.get(
        "homeassistant.helpers.device_registry"
    ) or ModuleType("homeassistant.helpers.device_registry")

    const.CONF_ADDRESS = getattr(const, "CONF_ADDRESS", "address")
    const.Platform = getattr(
        const,
        "Platform",
        SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor", BUTTON="button"),
    )
    config_entries.ConfigEntry = getattr(
        config_entries, "ConfigEntry", type("ConfigEntry", (), {})
    )
    core.HomeAssistant = getattr(core, "HomeAssistant", type("HomeAssistant", (), {}))
    device_registry.CONNECTION_BLUETOOTH = getattr(
        device_registry, "CONNECTION_BLUETOOTH", "bluetooth"
    )
    device_registry.DeviceInfo = getattr(
        device_registry, "DeviceInfo", lambda **kwargs: kwargs
    )

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules[f"{_PKG}.init_ready"] = ModuleType(f"{_PKG}.init_ready")


def _load_init() -> ModuleType:
    _stub_homeassistant()
    full = f"{_PKG}.__init__"
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

    bluez = ModuleType(f"{_PKG}.bluez_pair")

    async def async_unpair_address(_address: str) -> None:
        return None

    bluez.async_unpair_address = async_unpair_address
    sys.modules[f"{_PKG}.bluez_pair"] = bluez

    coordinator = ModuleType(f"{_PKG}.coordinator")
    coordinator.VerovalBleCoordinator = type("VerovalBleCoordinator", (), {})
    coordinator.VerovalBleDeviceData = type("VerovalBleDeviceData", (), {})
    coordinator.VerovalBleConfigEntry = object
    sys.modules[f"{_PKG}.coordinator"] = coordinator

    services = ModuleType(f"{_PKG}.services")
    services.async_setup_services = lambda _hass: None
    services.async_unload_services = lambda _hass: None
    sys.modules[f"{_PKG}.services"] = services

    spec = importlib.util.spec_from_file_location(full, _ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None
    init = importlib.util.module_from_spec(spec)
    init.__package__ = _PKG
    sys.modules[full] = init
    spec.loader.exec_module(init)
    return init


_init = _load_init()
async_migrate_entry = _init.async_migrate_entry
DOMAIN = _init.DOMAIN
dr = sys.modules["homeassistant.helpers.device_registry"]


def _entry(
    *,
    entry_id: str,
    unique_id: str,
    version: int,
    cuff_user: int | None = None,
) -> SimpleNamespace:
    data: dict[str, object] = {"address": ADDRESS}
    if cuff_user is not None:
        data["cuff_user"] = cuff_user
    return SimpleNamespace(
        entry_id=entry_id,
        unique_id=unique_id,
        version=version,
        data=data,
        title=f"BPU26 User {cuff_user}" if cuff_user else "BPU26",
    )


class _Registry:
    def __init__(self) -> None:
        self.devices: dict[str, SimpleNamespace] = {}
        self.updates: list[tuple] = []

    def async_get_device(self, identifiers=None, connections=None):  # noqa: ANN001
        wanted = identifiers or set()
        for device in self.devices.values():
            if wanted and wanted <= set(device.identifiers):
                return device
        return None

    def async_update_device(
        self,
        device_id: str,
        merge_identifiers=None,  # noqa: ANN001
        merge_connections=None,  # noqa: ANN001
    ) -> None:
        self.updates.append((device_id, merge_identifiers, merge_connections))
        device = self.devices[device_id]
        if merge_identifiers:
            device.identifiers = set(device.identifiers) | set(merge_identifiers)
        if merge_connections:
            device.connections = set(getattr(device, "connections", set())) | set(
                merge_connections
            )


def _hass(entries: list[SimpleNamespace], registry: _Registry) -> SimpleNamespace:
    tasks: list[object] = []

    def async_entries(_domain: str) -> list[SimpleNamespace]:
        return list(entries)

    def async_update_entry(entry: SimpleNamespace, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(entry, key, value)

    async def async_remove(entry_id: str) -> None:
        entries[:] = [item for item in entries if item.entry_id != entry_id]

    config_entries = SimpleNamespace(
        async_entries=async_entries,
        async_update_entry=async_update_entry,
        async_remove=async_remove,
    )
    return SimpleNamespace(
        config_entries=config_entries,
        async_create_task=lambda coro: tasks.append(coro) or coro,
        _tasks=tasks,
        _registry=registry,
    )


def test_migrate_rewrites_slot_entry_to_mac() -> None:
    user1 = _entry(
        entry_id="e1", unique_id=f"{ADDRESS_LOWER}_1", version=1, cuff_user=1
    )
    registry = _Registry()
    registry.devices["d1"] = SimpleNamespace(
        id="d1",
        identifiers={(DOMAIN, f"{ADDRESS_LOWER}_1")},
        connections=set(),
    )
    hass = _hass([user1], registry)
    original_get = getattr(dr, "async_get", None)
    dr.async_get = lambda _hass: registry
    try:
        assert asyncio.run(async_migrate_entry(hass, user1)) is True
    finally:
        if original_get is None:
            delattr(dr, "async_get")
        else:
            dr.async_get = original_get

    assert user1.version == 2
    assert user1.unique_id == ADDRESS_LOWER
    assert user1.title == "BPU26"
    assert user1.data == {"address": ADDRESS}
    assert registry.updates
    _device_id, merge_idents, merge_conns = registry.updates[0]
    assert merge_idents == {(DOMAIN, ADDRESS_LOWER)}
    assert merge_conns == {("bluetooth", ADDRESS_LOWER)}


def test_migrate_removes_second_slot_entry() -> None:
    user1 = _entry(
        entry_id="e1", unique_id=ADDRESS_LOWER, version=2
    )
    user2 = _entry(
        entry_id="e2", unique_id=f"{ADDRESS_LOWER}_2", version=1, cuff_user=2
    )
    registry = _Registry()
    hass = _hass([user1, user2], registry)
    original_get = getattr(dr, "async_get", None)
    dr.async_get = lambda _hass: registry
    try:
        assert asyncio.run(async_migrate_entry(hass, user2)) is False
    finally:
        if original_get is None:
            delattr(dr, "async_get")
        else:
            dr.async_get = original_get

    assert hass._tasks
    asyncio.run(hass._tasks[0])
    assert [entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)] == [
        "e1"
    ]
