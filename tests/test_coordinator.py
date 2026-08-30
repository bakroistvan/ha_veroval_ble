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
            self._available = False

        @property
        def available(self) -> bool:
            return self._available

        def _async_handle_unavailable(self, service_info: object) -> None:
            return None

        def _async_handle_bluetooth_event(
            self, service_info: object, change: object
        ) -> None:
            return None

        def async_set_updated_data(self, data: object) -> None:
            self.data = data

        def async_update_listeners(self) -> None:
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
AD_SILENCE_NEW_WINDOW_SECONDS = _const.AD_SILENCE_NEW_WINDOW_SECONDS
CUFF_ADVERTISE_SECONDS = _const.CUFF_ADVERTISE_SECONDS
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
            assert data._awaiting_new_window is False

    asyncio.run(run())
    assert data._polled_this_window is True
    assert data._window_polled_at == 10.0
    assert data._awaiting_new_window is False


def test_unavailable_while_idle_clears_flag() -> None:
    data = VerovalBleDeviceData(monotonic=_FakeClock(10.0))
    data._polled_this_window = True
    data._window_polled_at = 10.0
    coordinator = _make_coordinator(data)

    coordinator._async_handle_unavailable(object())

    assert data._polled_this_window is False
    assert data._window_polled_at is None
    assert data._awaiting_new_window is True
    # Fresh GATT after idle unavailable starts phone grace, not an immediate dump.
    assert data.poll_needed(object(), None) is False
    assert data._awaiting_new_window is False
    assert data._grace_started_at == 10.0


def test_poll_needed_true_again_after_window_gap() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    data._polled_this_window = True
    data._window_polled_at = 0.0
    data._last_ad_time = 0.0

    clock.now = POLL_WINDOW_GAP_SECONDS - 1
    # Keep last_ad fresh so silence does not open a window before the gap.
    data._last_ad_time = clock.now
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is True

    clock.now = POLL_WINDOW_GAP_SECONDS
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is False
    assert data._window_polled_at is None
    assert data._grace_started_at == POLL_WINDOW_GAP_SECONDS

    clock.now = POLL_WINDOW_GAP_SECONDS + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None) is True


def test_consumed_slot_polls_again_after_idle_unavailable() -> None:
    """Issue #23: next advertise window after setup dump must not be muted."""
    clock = _FakeClock(50.0)
    data = VerovalBleDeviceData(monotonic=clock)
    result = _dump_result()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())
    assert data.poll_needed(object(), None, 1) is False

    data.mark_window_ended()
    assert data._window_records is not None
    assert data._awaiting_new_window is True
    assert data.poll_needed(object(), None, 1) is False
    assert data._window_records is None
    assert data._consumed_slots == set()
    assert data._awaiting_new_window is False
    assert data._grace_started_at == 50.0
    clock.now = 50.0 + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None, 1) is True


def test_advertisement_silence_starts_new_window() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    result = _dump_result()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())

    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS - 1
    assert data.poll_needed(object(), None, 1) is False
    assert data._window_records is not None

    clock.now += AD_SILENCE_NEW_WINDOW_SECONDS
    assert data.poll_needed(object(), None, 1) is False
    assert data._window_records is None
    assert data._polled_this_window is False
    assert data._grace_started_at == clock.now
    clock.now += PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None, 1) is True


def test_same_window_ads_do_not_start_new_dump() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    result = _dump_result()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())

    for offset in (1.0, 5.0, 10.0, 19.0):
        clock.now = offset
        assert data.poll_needed(object(), None, 1) is False
    assert data._window_records is not None


def test_force_poll_dumps_again_while_window_still_open() -> None:
    data = VerovalBleDeviceData()
    first = _sample_measurement()
    second = BloodPressureMeasurement(
        flags=0x1E,
        systolic=122.0,
        diastolic=81.0,
        mean_arterial=94.0,
        timestamp=datetime(2024, 1, 16, 8, 0, 0),
        pulse=70.0,
        user_id=0,
        status=0,
        raw=b"",
    )
    dumps = [
        _dump_result(records=[first], selected=first),
        _dump_result(records=[second], selected=second),
    ]
    calls = {"n": 0}

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        result = dumps[min(calls["n"], len(dumps) - 1)]
        calls["n"] += 1
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> object:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)
        assert data.poll_needed(object(), None, 1) is False
        return await data.async_force_poll(_FakeBleDevice(), cuff_user=1)

    published = asyncio.run(run())
    assert calls["n"] == 2
    assert published is second
    assert data.last_measurement[1] is second


def test_coordinator_force_poll_requires_connectable_device() -> None:
    data = VerovalBleDeviceData()
    coordinator = _make_coordinator(data)
    coordinator.hass = SimpleNamespace()
    previous = _coordinator.async_ble_device_from_address
    _coordinator.async_ble_device_from_address = lambda *args, **kwargs: None
    try:

        async def run() -> None:
            await coordinator.async_force_poll()

        try:
            asyncio.run(run())
        except _coordinator.CuffNotConnectableError as err:
            assert "AA:BB:CC:DD:EE:FF" in str(err)
        else:
            raise AssertionError("expected CuffNotConnectableError")
    finally:
        _coordinator.async_ble_device_from_address = previous


def test_coordinator_force_poll_updates_data() -> None:
    data = VerovalBleDeviceData()
    result = _dump_result()
    coordinator = _make_coordinator(data)
    coordinator.hass = SimpleNamespace()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump
    _coordinator.async_ble_device_from_address = (
        lambda *args, **kwargs: _FakeBleDevice()
    )

    async def run() -> object:
        return await coordinator.async_force_poll()

    published = asyncio.run(run())
    assert published is result.selected
    assert coordinator.data is result.selected


def test_is_advertising_follows_last_live_ad_not_available() -> None:
    """HA available stays true on cached ads; the sensor must not."""
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    coordinator = _make_coordinator(data)
    coordinator._available = True
    assert coordinator.is_advertising is False

    live = SimpleNamespace(time=0.0)
    assert data.poll_needed(live, None) is False
    assert coordinator.is_advertising is True

    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS
    coordinator._available = True
    assert coordinator.is_advertising is True
    clock.now = CUFF_ADVERTISE_SECONDS
    coordinator._available = True
    assert coordinator.is_advertising is False


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
    assert data._awaiting_new_window is False
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
    data._last_ad_time = clock.now
    assert data.poll_needed(object(), None) is False
    assert data._polled_this_window is True
    clock.now = 50.0 + POLL_WINDOW_GAP_SECONDS
    data._last_ad_time = clock.now
    assert data.poll_needed(object(), None) is False
    assert data._grace_started_at == 50.0 + POLL_WINDOW_GAP_SECONDS


def test_ad_silence_after_dump_starts_phone_grace() -> None:
    """New window via silence must not dump immediately (medi.connect first)."""
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return _dump_result()

    _coordinator.dump_latest = fake_dump
    asyncio.run(data.async_poll(_FakeBleDevice(), cuff_user=1))

    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS
    assert data.poll_needed(object(), None, 1) is False
    assert data._grace_started_at == AD_SILENCE_NEW_WINDOW_SECONDS
    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS + PHONE_GRACE_SECONDS - 1
    assert data.poll_needed(object(), None, 1) is False
    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS + PHONE_GRACE_SECONDS
    assert data.poll_needed(object(), None, 1) is True


def test_grace_unavailable_does_not_set_awaiting_new_window() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(object(), None) is False
    clock.now = 20.0
    data.mark_window_ended()
    assert data._window_skipped is True
    assert data._awaiting_new_window is False
    assert data.poll_needed(object(), None) is False


def test_silence_during_grace_does_not_open_new_window() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(object(), None) is False
    started = data._grace_started_at
    clock.now = AD_SILENCE_NEW_WINDOW_SECONDS + 5
    assert data.poll_needed(object(), None) is False
    assert data._grace_started_at == started
    assert data._window_skipped is False


def test_force_dump_clears_grace_and_skipped_window() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(object(), None) is False
    data.mark_window_ended()
    assert data._window_skipped is True

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return _dump_result()

    _coordinator.dump_latest = fake_dump

    async def run() -> object:
        return await data.async_force_poll(_FakeBleDevice(), cuff_user=1)

    published = asyncio.run(run())
    assert published is not None
    assert data._window_skipped is False
    assert data._grace_started_at is None


class _FakeUtc:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_successful_dump_sets_last_synchronized() -> None:
    stamp = datetime(2024, 6, 1, 10, 0, 0)
    utc = _FakeUtc(stamp)
    data = VerovalBleDeviceData(utcnow=utc)
    result = _dump_result()

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())

    assert data.last_synchronized[1] == stamp
    assert _make_coordinator(data).last_synchronized == stamp


def test_unchanged_cuff_timestamp_still_updates_last_synchronized() -> None:
    first_stamp = datetime(2024, 6, 1, 10, 0, 0)
    second_stamp = datetime(2024, 6, 1, 11, 0, 0)
    utc = _FakeUtc(first_stamp)
    data = VerovalBleDeviceData(utcnow=utc)
    measurement = _sample_measurement()
    result = _dump_result(records=[measurement], selected=measurement)

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return result

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)
        assert data.last_synchronized[1] == first_stamp
        published = data.last_measurement[1]
        utc.now = second_stamp
        await data.async_force_poll(_FakeBleDevice(), cuff_user=1)
        assert data.last_measurement[1] is published
        assert data.last_synchronized[1] == second_stamp

    asyncio.run(run())


def test_auth_error_does_not_set_last_synchronized() -> None:
    data = VerovalBleDeviceData(utcnow=_FakeUtc(datetime(2024, 6, 1, 10, 0, 0)))

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return SimpleNamespace(
            auth_error=True,
            missing_characteristic=False,
            records=[],
            selected=None,
        )

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())
    assert data.last_synchronized == {}


def test_empty_dump_does_not_set_last_synchronized() -> None:
    data = VerovalBleDeviceData(utcnow=_FakeUtc(datetime(2024, 6, 1, 10, 0, 0)))

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return SimpleNamespace(
            auth_error=False,
            missing_characteristic=False,
            records=[],
            selected=None,
        )

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())
    assert data.last_synchronized == {}


def test_missing_characteristic_does_not_set_last_synchronized() -> None:
    data = VerovalBleDeviceData(utcnow=_FakeUtc(datetime(2024, 6, 1, 10, 0, 0)))

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return SimpleNamespace(
            auth_error=False,
            missing_characteristic=True,
            records=[],
            selected=None,
        )

    _coordinator.dump_latest = fake_dump

    async def run() -> None:
        await data.async_poll(_FakeBleDevice(), cuff_user=1)

    asyncio.run(run())
    assert data.last_synchronized == {}


def test_stale_cached_ad_does_not_refresh_last_ad_time() -> None:
    """HA can keep delivering the last packet after the cuff sleeps."""
    clock = _FakeClock(100.0)
    data = VerovalBleDeviceData(monotonic=clock)
    live = SimpleNamespace(time=100.0)
    assert data.poll_needed(live, None) is False
    assert data._last_ad_time == 100.0
    assert data._last_ad_stamp == 100.0

    clock.now = 110.0
    stale = SimpleNamespace(time=50.0)
    assert data.poll_needed(stale, None) is False
    assert data._last_ad_time == 100.0
    assert data._grace_started_at == 100.0

    clock.now = 100.0 + AD_SILENCE_NEW_WINDOW_SECONDS
    assert data.is_advertising() is True
    clock.now = 100.0 + CUFF_ADVERTISE_SECONDS
    assert data.is_advertising() is False


def test_replayed_same_stamp_is_not_live() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    first = SimpleNamespace(time=0.0)
    assert data.poll_needed(first, None) is False
    clock.now = 5.0
    replay = SimpleNamespace(time=0.0)
    assert data.poll_needed(replay, None) is False
    assert data._last_ad_time == 0.0


def test_cached_ads_after_dump_do_not_block_next_window() -> None:
    """Issue #23: scanner cache must not mute the next real measurement."""
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        return _dump_result()

    _coordinator.dump_latest = fake_dump
    asyncio.run(data.async_poll(_FakeBleDevice(), cuff_user=1))
    data._last_ad_time = 0.0
    data._last_ad_stamp = 0.0
    assert data._polled_this_window is True

    for now in (10.0, 30.0, 90.0, 150.0):
        clock.now = now
        cached = SimpleNamespace(time=0.0)
        assert data.poll_needed(cached, None, 1) is False
        assert data._polled_this_window is True

    clock.now = 200.0
    fresh = SimpleNamespace(time=200.0)
    assert data.poll_needed(fresh, None, 1) is False
    assert data._grace_started_at == 200.0
    clock.now = 200.0 + PHONE_GRACE_SECONDS
    later = SimpleNamespace(time=clock.now)
    assert data.poll_needed(later, None, 1) is True


def test_grace_dump_due_without_second_advertisement() -> None:
    """HA often sends only one live callback for the whole flash."""
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    assert data.poll_needed(SimpleNamespace(time=0.0), None) is False
    assert data.grace_dump_due() is False
    clock.now = PHONE_GRACE_SECONDS - 1
    assert data.grace_dump_due() is False
    clock.now = PHONE_GRACE_SECONDS
    assert data.grace_dump_due() is True
    assert data._polled_this_window is False


def test_grace_timer_polls_without_second_ad() -> None:
    clock = _FakeClock(0.0)
    data = VerovalBleDeviceData(monotonic=clock)
    scheduled: list[tuple[float, object]] = []

    class _Handle:
        def cancel(self) -> None:
            return None

    def call_later(delay: float, callback: object) -> _Handle:
        scheduled.append((delay, callback))
        return _Handle()

    tasks: list[object] = []
    coordinator = _make_coordinator(data)
    coordinator.hass = SimpleNamespace(
        loop=SimpleNamespace(call_later=call_later),
        state=_coordinator.CoreState.running,
        async_create_task=lambda coro: tasks.append(coro),
    )

    assert data.poll_needed(SimpleNamespace(time=0.0), None) is False
    coordinator._ensure_grace_timer()
    assert scheduled == [(PHONE_GRACE_SECONDS, coordinator._async_grace_timer_fired)]

    dumps = {"n": 0}

    async def fake_dump(_ble_device: object, _cuff_user: int) -> object:
        dumps["n"] += 1
        return _dump_result()

    _coordinator.dump_latest = fake_dump
    _coordinator.async_ble_device_from_address = (
        lambda *args, **kwargs: _FakeBleDevice()
    )

    clock.now = PHONE_GRACE_SECONDS
    coordinator._async_grace_timer_fired()
    assert tasks
    asyncio.run(tasks[0])
    assert dumps["n"] == 1
    assert data._polled_this_window is True
