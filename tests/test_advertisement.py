"""Unit tests for advertisement freshness helpers (no Home Assistant)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"


def _load() -> ModuleType:
    name = "veroval_ble_advertisement_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _ROOT / "advertisement.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_adv = _load()


def test_missing_timestamp_depends_on_require_flag() -> None:
    info = SimpleNamespace()
    assert _adv.advertisement_is_live(info, 10.0, max_age=20) is True
    assert (
        _adv.advertisement_is_live(info, 10.0, max_age=20, require_timestamp=True)
        is False
    )


def test_age_and_replayed_stamp() -> None:
    live = SimpleNamespace(time=100.0)
    assert _adv.advertisement_is_live(live, 110.0, max_age=20) is True
    stale = SimpleNamespace(time=70.0)
    assert _adv.advertisement_is_live(stale, 110.0, max_age=20) is False
    replay = SimpleNamespace(time=100.0)
    assert (
        _adv.advertisement_is_live(
            replay, 110.0, max_age=20, last_seen_stamp=100.0
        )
        is False
    )
    newer = SimpleNamespace(time=108.0)
    assert (
        _adv.advertisement_is_live(
            newer, 110.0, max_age=20, last_seen_stamp=100.0
        )
        is True
    )
