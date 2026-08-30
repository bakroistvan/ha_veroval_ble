"""Unit tests for VerovalBleDeviceData poll-window skip (no Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_coordinator_test"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _FakeBleDevice:
    def __init__(self, address: str = "AA:BB:CC:DD:EE:FF") -> None:
        self.address = address


def _stub_bleak() -> None:
    if "bleak.backends.device" in sys.modules:
        return
    bleak = ModuleType("bleak")
    backends = ModuleType("bleak.backends")
    device = ModuleType("bleak.backends.device")
    device.BLEDevice = _FakeBleDevice
    sys.modules["bleak"] = bleak
    sys.modules["bleak.backends"] = backends
    sys.modules["bleak.backends.device"] = device


def _callback(func: object) -> object:
    return func


def _stub_homeassistant() -> None:
    if "homeassistant.components.bluetooth.active_update_coordinator" in sys.modules:
        return

    ha = sys.modules.get("homeassistant") or ModuleType("homeassistant")
    components = ModuleType("homeassistant.components")
    bluetooth = ModuleType("homeassistant.components.bluetooth")
    active = ModuleType("homeassistant.components.bluetooth.active_update_coordinator")
    config_entries = ModuleType("homeassistant.config_entries")
    core = ModuleType("homeassistant.core")

    bluetooth.BluetoothChange = type("BluetoothChange", (), {})
    bluetooth.BluetoothScanningMode = SimpleNamespace(PASSIVE="passive")
    bluetooth.BluetoothServiceInfoBleak = type("BluetoothServiceInfoBleak", (), {})
    bluetooth.async_ble_device_from_address = lambda *args, **kwargs: None

    class ActiveBluetoothDataUpdateCoordinator:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

        def __init__(self, hass: object, logger: object, **kwargs: object) -> None:
            self.hass = hass
            self.logger = logger

        def _async_handle_unavailable(self, service_info: object) -> None:
            return None

    active.ActiveBluetoothDataUpdateCoordinator = ActiveBluetoothDataUpdateCoordinator

    class ConfigEntry:
        def __class_getitem__(cls, _item: object) -> type:
            return cls

    config_entries.ConfigEntry = ConfigEntry
    core.CoreState = SimpleNamespace(running="running")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = _callback

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.bluetooth"] = bluetooth
    sys.modules["homeassistant.components.bluetooth.active_update_coordinator"] = active
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core


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


def _load_coordinator() -> tuple[ModuleType, ModuleType, ModuleType]:
    _stub_bleak()
    _stub_homeassistant()

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
            raise AssertionError("dump_latest must be patched for poll tests")

        client.dump_latest = dump_latest
        sys.modules[client_name] = client

    coordinator = _load_module("coordinator")
    return const, parser, coordinator


_const, _parser, _coordinator = _load_coordinator()
PHONE_GRACE_SECONDS = _const.PHONE_GRACE_SECONDS
POLL_WINDOW_GAP_SECONDS = _const.POLL_WINDOW_GAP_SECONDS
VerovalBleDeviceData = _coordinator.VerovalBleDeviceData
VerovalBleCoordinator = _coordinator.VerovalBleCoordinator
BloodPressureMeasurement = _parser.BloodPressureMeasurement


def _sample_measurement() -> BloodPressureMeasurement:
    return BloodPressureMeasurement(
        flags=0x1E,
        systolic=120.0,
        diastolic=80.0,
        mean_arterial=93.0,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        pulse=72.0,
        user_id=0,
        status=0,
        raw=b"",
    )


def _dump_result(
    *,
    records: list[BloodPressureMeasurement] | None = None,
    selected: BloodPressureMeasurement | None = None,
) -> SimpleNamespace:
    if records is None:
        measurement = _sample_measurement()
        records = [measurement]
        selected = measurement
    return SimpleNamespace(
        auth_error=False,
        missing_characteristic=False,
        records=records,
        selected=selected,
    )


def _make_coordinator(device_data: object) -> object:
    return VerovalBleCoordinator(
        hass=SimpleNamespace(),
        address="AA:BB:CC:DD:EE:FF",
        cuff_user=1,
        device_data=device_data,
    )


def test_dump_sets_window_flag_so_immediate_poll_needed_is_false() -> None:
    clock = _FakeClock(50.0)
    data = VerovalBleDeviceData(monotonic=clock)
    result = _dump_result()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())

    assert data._polled_this_window is True
    assert data._window_polled_at == 50.0
    assert data.poll_needed(object(), None) is False


def test_unavailable_while_poll_lock_held_does_not_clear_flag() -> None:
    clock = _FakeClock(10.0)
    data = VerovalBleDeviceData(monotonic=clock)
    data._polled_this_window = True
    data._window_polled_at = 10.0
    coordinator = _make_coordinator(data)

    async def run() -> None:
        async with data._poll_lock:
            coordinator._async_handle_unavailable(object())
            assert data._polled_this_window is True
            assert data._window_polled_at == 10.0

    asyncio.run(run())
    assert data._polled_this_window is True
    assert data._window_polled_at == 10.0


def test_unavailable_while_idle_clears_flag() -> None:
    data = VerovalBleDeviceData(monotonic=_FakeClock(10.0))
    data._polled_this_window = True
    data._window_polled_at = 10.0
    coordinator = _make_coordinator(data)

    coordinator._async_handle_unavailable(object())

    assert data._polled_this_window is False
    assert data._window_polled_at is None
    assert data.poll_needed(object(), None) is False
    assert data._grace_started_at == 10.0


def test_poll_needed_true_again_after_window_gap() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    data._polled_this_window = True
    data._window_polled_at = 0.0

    clock.now = POLL_WINDOW_GAP_SECONDS - 1
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is True

    clock.now = POLL_WINDOW_GAP_SECONDS
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is False
    assert data._window_polled_at is None
    assert data._grace_started_at == POLL_WINDOW_GAP_SECONDS

    clock.now = POLL_WINDOW_GAP_SECONDS + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None) is True


def test_first_poll_needed_starts_phone_grace() -> None:
    clock = _FakeClock(100.0)
    data = VerovalBleDeviceData(monotonic=clock)
    dumps = {"n": 0}

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        dumps["n"] += 1
        return _dump_result()

    _coordinator.dump_latest = fake_dump

    assert data.poll_needed(object(), None) is False
    assert data._grace_started_at == 100.0
    clock.now = 100.0 + PHONE_GRACE_SECONDS - 1
    assert data.poll_needed(object(), None) is False
    clock.now = 100.0 + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None) is True
    assert dumps["n"] == 0


def test_unavailable_during_grace_skips_until_window_gap() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    coordinator = _make_coordinator(data)

    assert data.poll_needed(object(), None) is False
    clock.now = 30.0
    coordinator._async_handle_unavailable(object())

    assert data._window_skipped is True
    assert data._grace_started_at is None
    assert data._window_polled_at == 30.0
    clock.now = 50.0
    assert data.poll_needed(object(), None) is False

    clock.now = 30.0 + POLL_WINDOW_GAP_SECONDS
    assert data.poll_needed(object(), None) is False
    assert data._window_skipped is False
    assert data._grace_started_at == 30.0 + POLL_WINDOW_GAP_SECONDS

    clock.now = 30.0 + POLL_WINDOW_GAP_SECONDS + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None) is True


def test_second_unavailable_after_skip_keeps_gap() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(object(), None) is False
    clock.now = 10.0
    data.mark_window_ended("AA:BB:CC:DD:EE:FF")
    data.mark_window_ended("AA:BB:CC:DD:EE:FF")
    assert data._window_skipped is True
    assert data._window_polled_at == 10.0


def test_unavailable_after_grace_elapsed_skips_before_dump() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(object(), None) is False
    clock.now = PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None) is True
    data.mark_window_ended()
    assert data._window_skipped is True
    assert data.poll_needed(object(), None) is False


def test_after_dump_no_new_grace_until_window_gap() -> None:
    clock = _FakeClock(50.0)
    data = VerovalBleDeviceData(monotonic=clock)

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return _dump_result()

    _coordinator.dump_latest = fake_dump
    asyncio.run(data.async_poll(_FakeBleDevice(), cuff_user=1))

    clock.now = 80.0
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is True
    clock.now = 50.0 + POLL_WINDOW_GAP_SECONDS
    assert data.poll_needed(object(), None) is False
    assert data._grace_started_at == 50.0 + POLL_WINDOW_GAP_SECONDS
