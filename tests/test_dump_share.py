"""Unit tests for sharing one GATT dump across User 1 and User 2 (no Home Assistant)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PKG = "veroval_ble_dump_share_test"
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
            raise AssertionError("dump_latest must be patched for dump-share tests")

        client.dump_latest = dump_latest
        sys.modules[client_name] = client

    coordinator = _load_module("coordinator")
    return const, parser, coordinator


_const, _parser, _coordinator = _load_coordinator()
DUMP_IDLE_SECONDS = _const.DUMP_IDLE_SECONDS
DUMP_TIMEOUT_SECONDS = _const.DUMP_TIMEOUT_SECONDS
PHONE_GRACE_SECONDS = _const.PHONE_GRACE_SECONDS
POLL_WINDOW_GAP_SECONDS = _const.POLL_WINDOW_GAP_SECONDS
VerovalBleDeviceData = _coordinator.VerovalBleDeviceData
VerovalBleCoordinator = _coordinator.VerovalBleCoordinator
BloodPressureMeasurement = _parser.BloodPressureMeasurement
BLE_USER_1 = _parser.BLE_USER_1
BLE_USER_2 = _parser.BLE_USER_2
CUFF_USER_1 = _parser.CUFF_USER_1
CUFF_USER_2 = _parser.CUFF_USER_2
select_latest_for_user = _parser.select_latest_for_user
cuff_user_to_ble_id = _parser.cuff_user_to_ble_id


def _measurement(
    *,
    user_id: int,
    timestamp: datetime,
    systolic: float = 120.0,
    diastolic: float = 80.0,
    pulse: float = 72.0,
) -> BloodPressureMeasurement:
    return BloodPressureMeasurement(
        flags=0x1E,
        systolic=systolic,
        diastolic=diastolic,
        mean_arterial=93.0,
        timestamp=timestamp,
        pulse=pulse,
        user_id=user_id,
        status=0,
        raw=b"",
    )


def _dump_result(
    records: list[BloodPressureMeasurement],
    cuff_user: int,
) -> SimpleNamespace:
    selected = select_latest_for_user(records, cuff_user_to_ble_id(cuff_user))
    return SimpleNamespace(
        auth_error=False,
        missing_characteristic=False,
        records=records,
        selected=selected,
    )


def _patch_dump(records: list[BloodPressureMeasurement]) -> dict[str, int]:
    calls = {"n": 0}

    async def fake_dump(_ble_device: object, cuff_user: int) -> object:
        calls["n"] += 1
        return _dump_result(records, cuff_user)

    _coordinator.dump_latest = fake_dump
    return calls


def _make_coordinator(device_data: object, cuff_user: int) -> object:
    return VerovalBleCoordinator(
        hass=SimpleNamespace(),
        address="AA:BB:CC:DD:EE:FF",
        cuff_user=cuff_user,
        device_data=device_data,
    )


def test_dump_timeout_covers_full_two_user_stream() -> None:
    """~200 records ~24s plus idle stop; 15s would cut off User 2."""
    assert DUMP_TIMEOUT_SECONDS == 30.0
    assert DUMP_IDLE_SECONDS == 2.0


def test_two_coordinators_share_one_dump() -> None:
    """User 1 dump with mixed records; User 2 selects locally without a second GATT."""
    data = VerovalBleDeviceData()
    user1_latest = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        systolic=120.0,
    )
    user1_older = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 11, 0, 0),
        systolic=118.0,
    )
    user2_latest = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
        diastolic=85.0,
        pulse=75.0,
    )
    user2_older = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 10, 20, 10, 0),
        systolic=125.0,
    )
    records = [user1_latest, user1_older, user2_latest, user2_older]
    calls = _patch_dump(records)

    coord1 = _make_coordinator(data, CUFF_USER_1)
    coord2 = _make_coordinator(data, CUFF_USER_2)
    assert coord1.device_data is coord2.device_data is data

    async def run() -> tuple[object, object]:
        first = await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        second = await data.async_poll(_FakeBleDevice(), CUFF_USER_2)
        return first, second

    first, second = asyncio.run(run())

    assert calls["n"] == 1
    assert first is user1_latest
    assert second is user2_latest
    assert coord1.last_measurement is user1_latest
    assert coord2.last_measurement is user2_latest


def test_truncated_dump_user_2_does_not_reconnect() -> None:
    """Timeout can leave only User 1 records; User 2 must not start a second connect."""
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        systolic=120.0,
    )
    calls = _patch_dump([user1])

    async def run() -> tuple[object, object]:
        first = await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        second = await data.async_poll(_FakeBleDevice(), CUFF_USER_2)
        return first, second

    first, second = asyncio.run(run())

    assert calls["n"] == 1
    assert first is user1
    assert second is None
    assert data.last_measurement.get(CUFF_USER_2) is None


def test_user_1_last_measurement_unchanged_after_user_2_poll() -> None:
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        systolic=120.0,
    )
    user2 = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
    )
    _patch_dump([user1, user2])

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        published = data.last_measurement[CUFF_USER_1]
        await data.async_poll(_FakeBleDevice(), CUFF_USER_2)
        assert data.last_measurement[CUFF_USER_1] is published is user1
        assert data.last_measurement[CUFF_USER_2] is user2

    asyncio.run(run())


def test_poll_needed_two_arg_false_after_dump() -> None:
    """PR #10 contract: GATT-window-only poll_needed stays False after a dump."""
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    _patch_dump([user1])

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)

    asyncio.run(run())
    assert data._polled_this_window is True
    assert data.poll_needed(object(), None) is False


def test_poll_needed_cuff_user_2_true_after_user_1_consumed_dump() -> None:
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    user2 = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
    )
    _patch_dump([user1, user2])

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)

    asyncio.run(run())
    assert CUFF_USER_1 in data._consumed_slots
    assert CUFF_USER_2 not in data._consumed_slots
    assert data.poll_needed(object(), None, CUFF_USER_2) is True
    assert data.poll_needed(object(), None, cuff_user=CUFF_USER_2) is True
    assert data.poll_needed(object(), None, CUFF_USER_1) is False


def test_mark_window_ended_keeps_shared_dump_cache() -> None:
    data = VerovalBleDeviceData()
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    user2 = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
    )
    calls = _patch_dump([user1, user2])

    async def run() -> object:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        data.mark_window_ended()
        assert data._window_records is not None
        assert CUFF_USER_1 in data._consumed_slots
        return await data.async_poll(_FakeBleDevice(), CUFF_USER_2)

    second = asyncio.run(run())
    assert calls["n"] == 1
    assert second is user2


def test_needs_poll_passes_cuff_user() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    seen: list[int | None] = []
    original = data.poll_needed

    def tracking_poll_needed(
        service_info: object,
        last_poll: float | None,
        cuff_user: int | None = None,
    ) -> bool:
        seen.append(cuff_user)
        return original(service_info, last_poll, cuff_user)

    data.poll_needed = tracking_poll_needed  # type: ignore[method-assign]
    coord = _make_coordinator(data, CUFF_USER_2)
    coord.hass = SimpleNamespace(state=_coordinator.CoreState.running)
    previous = _coordinator.async_ble_device_from_address
    _coordinator.async_ble_device_from_address = lambda *args, **kwargs: object()
    try:
        service_info = SimpleNamespace(device=_FakeBleDevice())
        assert coord._async_needs_poll(service_info, None) is False
        clock.now = PHONE_GRACE_SECONDS
        assert coord._async_needs_poll(service_info, None) is True
        assert seen == [CUFF_USER_2, CUFF_USER_2]
    finally:
        _coordinator.async_ble_device_from_address = previous


def test_window_end_allows_new_dump_and_clears_cache() -> None:
    """Consumed slots must not block the next advertise window forever.

    mark_window_ended keeps the dump cache so User 2 can still consume it.
    A new GATT dump starts after POLL_WINDOW_GAP_SECONDS expires the cache
    and the phone-first grace elapses.
    """
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    first_user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        systolic=120.0,
    )
    first_user2 = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
    )
    second_user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 16, 8, 0, 0),
        systolic=122.0,
    )
    dumps = [
        [first_user1, first_user2],
        [second_user1],
    ]
    calls = {"n": 0}

    async def fake_dump(_ble_device: object, cuff_user: int) -> object:
        records = dumps[min(calls["n"], len(dumps) - 1)]
        calls["n"] += 1
        return _dump_result(records, cuff_user)

    _coordinator.dump_latest = fake_dump

    async def run() -> object:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        await data.async_poll(_FakeBleDevice(), CUFF_USER_2)
        data.mark_window_ended()
        assert data._window_records is not None
        assert data.poll_needed(object(), None) is False
        assert data.poll_needed(object(), None, CUFF_USER_1) is False
        clock.now = POLL_WINDOW_GAP_SECONDS
        assert data.poll_needed(object(), None) is False
        assert data._window_records is None
        assert data._grace_started_at == POLL_WINDOW_GAP_SECONDS
        assert data.poll_needed(object(), None, CUFF_USER_1) is False
        clock.now = POLL_WINDOW_GAP_SECONDS + PHONE_GRACE_SECONDS
        assert data.poll_needed(object(), None, CUFF_USER_1) is True
        return await data.async_poll(_FakeBleDevice(), CUFF_USER_1)

    published = asyncio.run(run())
    assert calls["n"] == 2
    assert published is second_user1
    assert data.last_measurement[CUFF_USER_1] is second_user1
    assert data.last_measurement[CUFF_USER_2] is first_user2
    assert data._window_records == [second_user1]
    assert data._consumed_slots == {CUFF_USER_1}


def test_shared_grace_user_2_does_not_restart_wait() -> None:
    """User 1 and User 2 share one grace; after the dump, User 2 uses the cache."""
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    user1 = _measurement(
        user_id=BLE_USER_1,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    user2 = _measurement(
        user_id=BLE_USER_2,
        timestamp=datetime(2024, 1, 14, 9, 30, 0),
        systolic=130.0,
    )
    calls = _patch_dump([user1, user2])

    assert data.poll_needed(object(), None, CUFF_USER_1) is False
    started = data._grace_started_at
    assert data.poll_needed(object(), None, CUFF_USER_2) is False
    assert data._grace_started_at == started

    clock.now = PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None, CUFF_USER_1) is True

    async def run() -> object:
        await data.async_poll(_FakeBleDevice(), CUFF_USER_1)
        assert data.poll_needed(object(), None, CUFF_USER_2) is True
        return await data.async_poll(_FakeBleDevice(), CUFF_USER_2)

    second = asyncio.run(run())
    assert calls["n"] == 1
    assert second is user2
