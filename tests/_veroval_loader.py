"""Load HA-free veroval_ble modules (const, parser, client) for tests and CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PKG = "veroval_ble_hil"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "veroval_ble"
_MODULES = ("const", "parser", "client")


def _ensure_package() -> ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(_PKG, loader=None)
    )
    pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = pkg
    return pkg


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


def load_veroval_ble() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Return (const, parser, client) without importing Home Assistant."""
    _ensure_package()
    const = _load_module("const")
    parser = _load_module("parser")
    client = _load_module("client")
    return const, parser, client


def load_client_module() -> ModuleType:
    """Return the client module only."""
    return load_veroval_ble()[2]
