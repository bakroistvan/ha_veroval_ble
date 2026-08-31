"""Unit tests for the force_dump debug action (no Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_services_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


class _FakeBleDevice:
    def __init__(self, address: str = "AA:BB:CC:DD:EE:FF") -> None:
        self.address = address


class _FakeHomeAssistantError(Exception):
    pass


def _callback(func: object) -> object:
    return func


def _ensure_device_registry() -> ModuleType:
    device_registry = sys.modules.get("homeassistant.helpers.device_registry")
    if device_registry is None:
        device_registry = ModuleType("homeassistant.helpers.device_registry")
        sys.modules["homeassistant.helpers.device_registry"] = device_registry
    if getattr(device_registry, "DeviceInfo", None) is None:
        device_registry.DeviceInfo = lambda **kwargs: kwargs
    if getattr(device_registry, "CONNECTION_BLUETOOTH", None) is None:
        device_registry.CONNECTION_BLUETOOTH = "bluetooth"
    if getattr(device_registry, "_registry", None) is None:
        _registry = SimpleNamespace(_devices={})

        def _registry_get(device_id: str) -> object | None:
            return _registry._devices.get(device_id)

        _registry.async_get = _registry_get
        device_registry._registry = _registry
    registry = device_registry._registry
    if getattr(registry, "_devices", None) is None:
        registry._devices = {}
    if getattr(registry, "async_get", None) is None:
        registry.async_get = lambda device_id: registry._devices.get(device_id)
    device_registry.async_get = lambda _hass: device_registry._registry
    return device_registry


def _stub_modules() -> None:
    ha = sys.modules.get("homeassistant") or ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    const = sys.modules.get("homeassistant.const") or ModuleType("homeassistant.const")
    const.ATTR_DEVICE_ID = getattr(const, "ATTR_DEVICE_ID", "device_id")
    const.CONF_ADDRESS = getattr(const, "CONF_ADDRESS", "address")
    if getattr(const, "Platform", None) is None:
        const.Platform = SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor")
    sys.modules["homeassistant.const"] = const

    exceptions = sys.modules.get("homeassistant.exceptions") or ModuleType(
        "homeassistant.exceptions"
    )
    exceptions.HomeAssistantError = getattr(
        exceptions, "HomeAssistantError", _FakeHomeAssistantError
    )
    sys.modules["homeassistant.exceptions"] = exceptions

    core = sys.modules.get("homeassistant.core") or ModuleType("homeassistant.core")
    core.HomeAssistant = getattr(core, "HomeAssistant", type("HomeAssistant", (), {}))
    core.ServiceCall = getattr(core, "ServiceCall", type("ServiceCall", (), {}))
    core.SupportsResponse = getattr(
        core, "SupportsResponse", SimpleNamespace(OPTIONAL="optional")
    )
    core.CoreState = getattr(core, "CoreState", SimpleNamespace(running="running"))
    core.callback = getattr(core, "callback", _callback)
    sys.modules["homeassistant.core"] = core

    helpers = sys.modules.get("homeassistant.helpers") or ModuleType(
        "homeassistant.helpers"
    )
    sys.modules["homeassistant.helpers"] = helpers
    _ensure_device_registry()

    if "homeassistant.components.bluetooth.active_update_coordinator" in sys.modules:
        return

    components = ModuleType("homeassistant.components")
    bluetooth = ModuleType("homeassistant.components.bluetooth")
    active = ModuleType("homeassistant.components.bluetooth.active_update_coordinator")
    config_entries = ModuleType("homeassistant.config_entries")

    bluetooth.BluetoothChange = type("BluetoothChange", (), {})
    bluetooth.BluetoothScanningMode = SimpleNamespace(PASSIVE="passive")
    bluetooth.BluetoothServiceInfoBleak = type("BluetoothServiceInfoBleak", (), {})
    bluetooth.async_ble_device_from_address = lambda *args, **kwargs: _FakeBleDevice()

    class ActiveBluetoothDataUpdateCoordinator:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

        def __init__(self, hass: object, logger: object, **kwargs: object) -> None:
            self.hass = hass
            self.logger = logger
            self.data = None

        def async_set_updated_data(self, data: object) -> None:
            self.data = data

        def async_update_listeners(self) -> None:
            return None

        def _async_handle_unavailable(self, service_info: object) -> None:
            return None

        def _async_handle_bluetooth_event(
            self, service_info: object, change: object
        ) -> None:
            return None

    active.ActiveBluetoothDataUpdateCoordinator = ActiveBluetoothDataUpdateCoordinator

    class ConfigEntry:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

    config_entries.ConfigEntry = ConfigEntry

    if "bleak.backends.device" not in sys.modules:
        bleak = ModuleType("bleak")
        backends = ModuleType("bleak.backends")
        device = ModuleType("bleak.backends.device")
        device.BLEDevice = _FakeBleDevice
        sys.modules["bleak"] = bleak
        sys.modules["bleak.backends"] = backends
        sys.modules["bleak.backends.device"] = device

    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.bluetooth"] = bluetooth
    sys.modules["homeassistant.components.bluetooth.active_update_coordinator"] = active
    sys.modules["homeassistant.config_entries"] = config_entries


def _load_module(name: str) -> ModuleType:
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = _ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_services() -> tuple[ModuleType, ModuleType, ModuleType]:
    _stub_modules()
    pkg = sys.modules.get(_PKG)
    if pkg is None:
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg

    const = _load_module("const")
    parser = _load_module("parser")
    client_name = f"{_PKG}.client"
    if client_name not in sys.modules:
        client = ModuleType(client_name)

        async def dump_latest(_ble_device: object, _cuff_user: int) -> object:
            raise AssertionError("dump_latest must be patched")

        client.dump_latest = dump_latest
        sys.modules[client_name] = client

    coordinator = _load_module("coordinator")
    coordinator.async_ble_device_from_address = (
        lambda *args, **kwargs: _FakeBleDevice()
    )
    services = _load_module("services")
    return const, parser, coordinator, services


_const, _parser, _coordinator, _services = _load_services()
SERVICE_FORCE_DUMP = _const.SERVICE_FORCE_DUMP
VerovalBleDeviceData = _coordinator.VerovalBleDeviceData
VerovalBleCoordinator = _coordinator.VerovalBleCoordinator
BloodPressureMeasurement = _parser.BloodPressureMeasurement
CUFF_USER_1 = _parser.CUFF_USER_1
CUFF_USER_2 = _parser.CUFF_USER_2
BLE_USER_1 = _parser.BLE_USER_1
BLE_USER_2 = _parser.BLE_USER_2


def _measurement(*, user_id: int, systolic: float, timestamp: datetime) -> BloodPressureMeasurement:
    return BloodPressureMeasurement(
        flags=0x1E,
        systolic=systolic,
        diastolic=80.0,
        mean_arterial=93.0,
        timestamp=timestamp,
        pulse=72.0,
        user_id=user_id,
        status=0,
        raw=b"",
    )


def _dump_result(records: list[BloodPressureMeasurement]) -> SimpleNamespace:
    return SimpleNamespace(
        auth_error=False,
        missing_characteristic=False,
        records=records,
        selected=records[0],
    )


def _make_coordinator(cuff_user: int, device_data: object | None = None) -> object:
    data = device_data or VerovalBleDeviceData()
    coord = VerovalBleCoordinator(
        hass=SimpleNamespace(),
        address="AA:BB:CC:DD:EE:FF",
        cuff_user=cuff_user,
        device_data=data,
    )
    return coord


def test_service_name_is_force_dump() -> None:
    assert SERVICE_FORCE_DUMP == "force_dump"


def test_no_entries_raises() -> None:
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: [])
    )
    try:
        _services.coordinators_for_service_call(hass, SimpleNamespace(data={}))
    except _FakeHomeAssistantError as err:
        assert "set up" in str(err)
    else:
        raise AssertionError("expected HomeAssistantError")


def test_no_device_id_returns_all_coordinators() -> None:
    coord1 = _make_coordinator(CUFF_USER_1)
    coord2 = _make_coordinator(CUFF_USER_2, coord1.device_data)
    entries = [
        SimpleNamespace(runtime_data=coord1),
        SimpleNamespace(runtime_data=coord2),
    ]
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: entries)
    )
    selected = _services.coordinators_for_service_call(hass, SimpleNamespace(data={}))
    assert selected == [coord1, coord2]


def test_device_id_selects_one_slot() -> None:
    coord1 = _make_coordinator(CUFF_USER_1)
    coord2 = _make_coordinator(CUFF_USER_2, coord1.device_data)
    entries = [
        SimpleNamespace(runtime_data=coord1),
        SimpleNamespace(runtime_data=coord2),
    ]
    ident = ("veroval_ble", "AA:BB:CC:DD:EE:FF_1")
    device = SimpleNamespace(identifiers={ident})
    registry = _services.dr.async_get(SimpleNamespace())
    registry._devices["dev-user-1"] = device
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: entries)
    )
    selected = _services.coordinators_for_service_call(
        hass, SimpleNamespace(data={"device_id": "dev-user-1"})
    )
    assert selected == [coord1]


def test_measurement_payload_includes_reading() -> None:
    coord = _make_coordinator(CUFF_USER_1)
    measurement = _measurement(
        user_id=BLE_USER_1,
        systolic=120.0,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    payload = _services.measurement_payload(coord, measurement)
    assert payload["synced"] is True
    assert payload["cuff_user"] == 1
    assert payload["systolic"] == 120.0
    assert payload["timestamp"] == "2024-01-15T12:00:00"
    assert payload["irregular_pulse"] is False


def test_force_dump_one_gatt_for_two_slots() -> None:
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        systolic=120.0,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    user2 = _measurement(
        user_id=BLE_USER_2,
        systolic=130.0,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
    )
    calls = {"n": 0}

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        calls["n"] += 1
        return _dump_result([user1, user2])

    _coordinator.dump_latest = fake_dump
    coord1 = _make_coordinator(CUFF_USER_1, data)
    coord2 = _make_coordinator(CUFF_USER_2, data)

    async def run() -> list:
        return await _services.async_force_dump_coordinators([coord1, coord2])

    results = asyncio.run(run())
    assert calls["n"] == 1
    assert results[0]["cuff_user"] == 1
    assert results[0]["systolic"] == 120.0
    assert results[1]["cuff_user"] == 2
    assert results[1]["systolic"] == 130.0
    assert coord1.data is user1
    assert coord2.data is user2
