"""Unit tests for discovery unique_id handling (no Home Assistant install)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"
_PKG = "veroval_ble_cf"
ADDRESS = "aa:bb:cc:dd:ee:ff"
ADDRESS_UPPER = "AA:BB:CC:DD:EE:FF"


class AbortFlow(Exception):
    """Stand-in for homeassistant.data_entry_flow.AbortFlow."""

    def __init__(
        self, reason: str, description_placeholders: dict | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.description_placeholders = description_placeholders


class _StubConfigFlow:
    """Minimal ConfigFlow: records unique_id and aborts on a matching entry."""

    def __init_subclass__(cls, domain=None, **kwargs):  # type: ignore[no-untyped-def]
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(
        self, unique_id: str | None, raise_on_progress: bool = True
    ) -> None:
        if not hasattr(self, "context") or self.context is None:
            self.context = {}
        self.context["unique_id"] = unique_id

    @property
    def unique_id(self) -> str | None:
        return getattr(self, "context", {}).get("unique_id")

    def _async_current_entries(self, include_ignore: bool | None = None) -> list:
        return list(getattr(self, "_entries", []))

    def _abort_if_unique_id_configured(self) -> None:
        uid = self.unique_id
        if uid is None:
            return
        for entry in self._async_current_entries():
            if entry.unique_id == uid:
                raise AbortFlow("already_configured")

    def async_abort(
        self, reason: str, description_placeholders: dict | None = None
    ) -> dict:
        return {
            "type": "abort",
            "reason": reason,
            "description_placeholders": description_placeholders,
        }

    def async_show_form(
        self,
        step_id: str,
        data_schema=None,  # noqa: ANN001
        errors: dict | None = None,
        description_placeholders: dict | None = None,
    ) -> dict:
        return {"type": "form", "step_id": step_id, "errors": errors or {}}

    def _set_confirm_only(self) -> None:
        return None

    def async_create_entry(self, title: str, data: dict) -> dict:
        return {"type": "create_entry", "title": title, "data": data}


def _install_stubs() -> None:
    if "homeassistant.config_entries" in sys.modules:
        return

    vol = ModuleType("voluptuous")
    vol.Schema = lambda *a, **k: object()  # type: ignore[attr-defined]
    vol.Required = lambda x, *a, **k: x  # type: ignore[attr-defined]
    vol.Optional = lambda x, default=None, *a, **k: x  # type: ignore[attr-defined]
    vol.In = lambda x: x  # type: ignore[attr-defined]
    sys.modules["voluptuous"] = vol

    ha = ModuleType("homeassistant")
    ha_components = ModuleType("homeassistant.components")
    ha_bt = ModuleType("homeassistant.components.bluetooth")
    ha_ce = ModuleType("homeassistant.config_entries")
    ha_const = ModuleType("homeassistant.const")
    ha_core = ModuleType("homeassistant.core")
    ha_helpers = ModuleType("homeassistant.helpers")
    ha_selector = ModuleType("homeassistant.helpers.selector")

    ha_bt.BluetoothChange = type("BluetoothChange", (), {})
    ha_bt.BluetoothScanningMode = SimpleNamespace(ACTIVE="active")
    ha_bt.BluetoothServiceInfoBleak = type("BluetoothServiceInfoBleak", (), {})
    ha_bt.async_ble_device_from_address = lambda *a, **k: None
    ha_bt.async_discovered_service_info = lambda *a, **k: []
    ha_bt.async_register_callback = lambda *a, **k: lambda: None

    class _StubOptionsFlow:
        def async_show_form(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"type": "form", **kwargs}

        def async_create_entry(self, title: str, data: dict) -> dict:
            return {"type": "create_entry", "title": title, "data": data}

    ha_ce.ConfigFlow = _StubConfigFlow
    ha_ce.ConfigFlowResult = dict
    ha_ce.AbortFlow = AbortFlow
    ha_ce.ConfigEntry = type("ConfigEntry", (), {})
    ha_ce.OptionsFlow = _StubOptionsFlow

    ha_const.CONF_ADDRESS = "address"
    ha_core.callback = lambda fn: fn

    class NumberSelectorConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class NumberSelector:
        def __init__(self, config: object) -> None:
            self.config = config

    ha_selector.NumberSelector = NumberSelector
    ha_selector.NumberSelectorConfig = NumberSelectorConfig

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.bluetooth"] = ha_bt
    sys.modules["homeassistant.config_entries"] = ha_ce
    sys.modules["homeassistant.const"] = ha_const
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.selector"] = ha_selector


def _load_config_flow() -> ModuleType:
    _install_stubs()
    if _PKG not in sys.modules:
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.config_flow"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _ROOT / "config_flow.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_cf = _load_config_flow()
VerovalBleConfigFlow = _cf.VerovalBleConfigFlow
VerovalBleOptionsFlow = _cf.VerovalBleOptionsFlow
CONF_CUFF_USER = "cuff_user"
CONF_ADDRESS = "address"


def _discovery(*, fresh: bool = True) -> SimpleNamespace:
    ad_time = time.monotonic() if fresh else time.monotonic() - 120
    return SimpleNamespace(
        name="BPU26",
        address=ADDRESS_UPPER,
        manufacturer_data={},
        device=None,
        time=ad_time,
    )


def _slot_entry(cuff_user: int) -> SimpleNamespace:
    return SimpleNamespace(
        unique_id=f"{ADDRESS}_{cuff_user}",
        data={CONF_ADDRESS: ADDRESS, CONF_CUFF_USER: cuff_user},
        version=1,
    )


def _v2_entry() -> SimpleNamespace:
    return SimpleNamespace(
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        version=2,
    )


def _ignore_mac_entry() -> SimpleNamespace:
    return SimpleNamespace(unique_id=ADDRESS, data={})


def _flow(entries: list[SimpleNamespace]) -> VerovalBleConfigFlow:
    flow = VerovalBleConfigFlow()
    flow.context = {}
    flow.hass = object()  # type: ignore[attr-defined]
    flow._entries = entries  # type: ignore[attr-defined]
    return flow


def _set_discovered(infos: list) -> None:
    _cf.async_discovered_service_info = lambda *a, **k: list(infos)


def _host_device(path: str = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"):
    return SimpleNamespace(details={"path": path, "props": {"Address": ADDRESS_UPPER}})


def _proxy_device():
    return SimpleNamespace(details={"source": "kitchen-ble-proxy"})


def _host_discovery(*, fresh: bool = True) -> SimpleNamespace:
    ad_time = time.monotonic() if fresh else time.monotonic() - 120
    return SimpleNamespace(
        name="BPU26",
        address=ADDRESS_UPPER,
        manufacturer_data={},
        device=_host_device(),
        time=ad_time,
        source="00:1a:7d:da:71:13",
    )


def _proxy_discovery() -> SimpleNamespace:
    return SimpleNamespace(
        name="BPU26",
        address=ADDRESS_UPPER,
        manufacturer_data={},
        device=_proxy_device(),
        time=time.monotonic(),
        source="kitchen-ble-proxy",
    )


def _abort_reason(result_or_exc: object) -> str:
    if isinstance(result_or_exc, AbortFlow):
        return result_or_exc.reason
    assert isinstance(result_or_exc, dict)
    return str(result_or_exc["reason"])


def test_bluetooth_configured_cuff_aborts_with_mac_unique_id() -> None:
    """A v2 cuff entry (or leftover v1 slots) is already configured."""
    flow = _flow([_v2_entry()])

    async def _run() -> object:
        try:
            return await flow.async_step_bluetooth(_discovery())
        except AbortFlow as err:
            return err

    outcome = asyncio.run(_run())
    assert _abort_reason(outcome) == "already_configured"
    assert flow.unique_id == ADDRESS


def test_bluetooth_legacy_slot_is_already_configured() -> None:
    """A leftover 0.2.0 User 1 entry still blocks a second add of the cuff."""
    flow = _flow([_slot_entry(1)])

    async def _run() -> object:
        try:
            return await flow.async_step_bluetooth(_discovery())
        except AbortFlow as err:
            return err

    outcome = asyncio.run(_run())
    assert _abort_reason(outcome) == "already_configured"
    assert flow.unique_id == ADDRESS


def test_bluetooth_zero_slots_honors_ignore_mac() -> None:
    """First-time Ignore keyed by MAC still aborts discovery."""
    flow = _flow([_ignore_mac_entry()])

    async def _run() -> object:
        try:
            return await flow.async_step_bluetooth(_discovery())
        except AbortFlow as err:
            return err

    outcome = asyncio.run(_run())
    assert _abort_reason(outcome) == "already_configured"
    assert flow.unique_id == ADDRESS


def test_bluetooth_zero_slots_uses_mac() -> None:
    flow = _flow([])
    result = asyncio.run(flow.async_step_bluetooth(_discovery()))
    assert flow.unique_id == ADDRESS
    assert result["type"] == "form"
    assert result["step_id"] == "bluetooth_confirm"


def test_choose_address_both_slots_aborts_without_mac_unique_id() -> None:
    flow = _flow([_slot_entry(1), _slot_entry(2)])
    result = asyncio.run(flow._async_choose_address(ADDRESS))
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert flow.unique_id is None


def test_choose_address_one_slot_aborts_already_configured() -> None:
    flow = _flow([_slot_entry(1)])
    result = asyncio.run(flow._async_choose_address(ADDRESS))
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert flow.unique_id is None


def test_finish_setup_creates_mac_entry() -> None:
    flow = _flow([])
    flow._address = ADDRESS
    flow._name = "BPU26"
    result = asyncio.run(flow._async_finish_setup())
    assert result["type"] == "create_entry"
    assert result["title"] == "BPU26"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert flow.unique_id == ADDRESS


def test_bluetooth_confirm_proxy_only_aborts_without_scan() -> None:
    """Add on a proxy-only Discovered card must not start the 60s host scan."""
    flow = _flow([])
    scanned = False

    async def _fake_scan() -> dict:
        nonlocal scanned
        scanned = True
        return {"type": "progress", "step_id": "scan"}

    async def _fake_pairing() -> dict:
        return {"type": "form", "step_id": "pairing"}

    flow.async_step_scan = _fake_scan  # type: ignore[method-assign]
    flow.async_step_pairing = _fake_pairing  # type: ignore[method-assign]

    async def _run() -> dict:
        await flow.async_step_bluetooth(_proxy_discovery())
        return await flow.async_step_bluetooth_confirm({})

    result = asyncio.run(_run())
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_not_supported"
    assert scanned is False


def test_scan_only_proxy_ads_aborts_proxy_not_supported() -> None:
    _set_discovered([_proxy_discovery()])
    try:
        flow = _flow([])
        result = asyncio.run(flow._async_next_after_scan())
        assert result["type"] == "abort"
        assert result["reason"] == "proxy_not_supported"
    finally:
        _set_discovered([])


def test_scan_no_ads_is_not_found() -> None:
    _set_discovered([])
    flow = _flow([])
    result = asyncio.run(flow._async_next_after_scan())
    assert result["type"] == "form"
    assert result["step_id"] == "not_found"


def test_bluetooth_confirm_host_fresh_goes_to_pairing() -> None:
    flow = _flow([])

    async def _fake_pairing() -> dict:
        return {"type": "form", "step_id": "pairing"}

    async def _fake_scan() -> dict:
        raise AssertionError("should not scan for a fresh host advertisement")

    flow.async_step_pairing = _fake_pairing  # type: ignore[method-assign]
    flow.async_step_scan = _fake_scan  # type: ignore[method-assign]

    async def _run() -> dict:
        await flow.async_step_bluetooth(_host_discovery(fresh=True))
        return await flow.async_step_bluetooth_confirm({})

    result = asyncio.run(_run())
    assert result["step_id"] == "pairing"


def test_bluetooth_stale_discovery_aborts_without_unique_id() -> None:
    """Cached ads must not leave a Discovered / Add card while the cuff sleeps."""
    flow = _flow([_slot_entry(1)])
    result = asyncio.run(flow.async_step_bluetooth(_discovery(fresh=False)))
    assert result["type"] == "abort"
    assert result["reason"] == "stale_advertisement"
    assert flow.unique_id is None


def test_bluetooth_confirm_stale_host_scans() -> None:
    flow = _flow([])
    scanned = False

    async def _fake_scan() -> dict:
        nonlocal scanned
        scanned = True
        return {"type": "progress", "step_id": "scan"}

    flow.async_step_scan = _fake_scan  # type: ignore[method-assign]

    async def _run() -> dict:
        await flow.async_step_bluetooth(_host_discovery(fresh=True))
        assert flow._discovery_info is not None
        flow._discovery_info.time = time.monotonic() - 120
        return await flow.async_step_bluetooth_confirm({})

    result = asyncio.run(_run())
    assert scanned is True
    assert result["step_id"] == "scan"


def test_is_fresh_advertisement_missing_time_is_stale() -> None:
    assert _cf._is_fresh_advertisement(SimpleNamespace(time=None)) is False
    assert _cf._is_fresh_advertisement(SimpleNamespace()) is False
    assert _cf._is_fresh_advertisement(SimpleNamespace(time="now")) is False
    stale = SimpleNamespace(time=time.monotonic() - 120)
    assert _cf._is_fresh_advertisement(stale) is False
    fresh = SimpleNamespace(time=time.monotonic())
    assert _cf._is_fresh_advertisement(fresh) is True


def test_is_local_bluez_device_path_vs_empty_or_proxy() -> None:
    assert (
        _cf.is_local_bluez_device(
            SimpleNamespace(details={"path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"})
        )
        is True
    )
    assert _cf.is_local_bluez_device(SimpleNamespace(details={})) is False
    assert _cf.is_local_bluez_device(SimpleNamespace(details=None)) is False
    assert (
        _cf.is_local_bluez_device(
            SimpleNamespace(details={"source": "kitchen-ble-proxy"})
        )
        is False
    )


def test_choose_address_proxy_only_aborts() -> None:
    flow = _flow([])
    flow._proxy_devices[ADDRESS] = _proxy_discovery()
    result = asyncio.run(flow._async_choose_address(ADDRESS))
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_not_supported"


def test_host_adapter_source_fallback_without_path() -> None:
    host = SimpleNamespace(
        device=SimpleNamespace(details={}),
        source="00:1A:7D:DA:71:13",
    )
    assert _cf._is_host_adapter_advertisement(host) is True
    proxy = SimpleNamespace(
        device=SimpleNamespace(details={}),
        source="kitchen-ble-proxy",
    )
    assert _cf._is_host_adapter_advertisement(proxy) is False
    assert _cf._is_proxy_advertisement(proxy) is True


def test_async_get_options_flow_returns_options_handler() -> None:
    entry = SimpleNamespace(options={})
    flow = VerovalBleConfigFlow.async_get_options_flow(entry)
    assert isinstance(flow, VerovalBleOptionsFlow)


def test_options_flow_init_shows_form() -> None:
    flow = VerovalBleOptionsFlow()
    flow.config_entry = SimpleNamespace(options={})  # type: ignore[attr-defined]
    result = asyncio.run(flow.async_step_init())
    assert result["type"] == "form"
    assert result["step_id"] == "init"


def test_options_flow_save_persists_ints() -> None:
    flow = VerovalBleOptionsFlow()
    flow.config_entry = SimpleNamespace(options={})  # type: ignore[attr-defined]
    result = asyncio.run(
        flow.async_step_init(
            {
                "phone_grace_seconds": 0.0,
                "ad_silence_seconds": 15.0,
                "advertise_linger_seconds": 8.0,
                "poll_window_gap_seconds": 200.0,
                "dump_timeout_seconds": 25.0,
                "dump_idle_seconds": 3.0,
            }
        )
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {
        "phone_grace_seconds": 0,
        "ad_silence_seconds": 15,
        "advertise_linger_seconds": 8,
        "poll_window_gap_seconds": 200,
        "dump_timeout_seconds": 25,
        "dump_idle_seconds": 3,
    }
