"""BlueZ D-Bus pairing helper for Veroval BPU26 passkey entry (HAOS host adapter)."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
DEVICE_INTERFACE = "org.bluez.Device1"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
AGENT_MANAGER_PATH = "/org/bluez"
AGENT_PATH = "/org/homeassistant/veroval_ble/agent"
AGENT_CAPABILITY = "KeyboardDisplay"

PAIR_TIMEOUT_SECONDS = 120.0


class PairingError(Exception):
    """Pairing failure with a config-flow abort/error reason."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class AgentUnavailableError(PairingError):
    """BlueZ agent could not be registered (another agent may own the default)."""

    def __init__(self, message: str = "") -> None:
        super().__init__("agent_unavailable", message)


class PairingNotSupportedError(PairingError):
    """Host cannot register a BlueZ agent (not Linux / no dbus-fast)."""

    def __init__(self, message: str = "") -> None:
        super().__init__("pairing_not_supported", message)


class ProxyNotSupportedError(PairingError):
    """Device is only reachable via a Bluetooth proxy, not the host adapter."""

    def __init__(self, message: str = "") -> None:
        super().__init__("proxy_not_supported", message)


class DeviceNotFoundError(PairingError):
    """BlueZ does not know this address yet."""

    def __init__(self, message: str = "") -> None:
        super().__init__("device_not_found", message)


class PairingFailedError(PairingError):
    """Pair() failed after the agent was registered."""

    def __init__(self, message: str = "") -> None:
        super().__init__("pairing_failed", message)


_SNAPSHOT_KEYS = (
    "Address",
    "AddressType",
    "Name",
    "Alias",
    "Adapter",
    "Paired",
    "Bonded",
    "Connected",
    "Trusted",
    "Blocked",
    "ServicesResolved",
    "LegacyPairing",
    "RSSI",
    "TxPower",
)

_BLUEZ_ERROR_HINTS: dict[str, str] = {
    "org.bluez.Error.AuthenticationFailed": (
        "wrong PIN, cuff not in pair mode, or another device still bonded"
    ),
    "org.bluez.Error.AuthenticationCanceled": (
        "pairing cancelled (often by the cuff or the agent)"
    ),
    "org.bluez.Error.AuthenticationRejected": "pairing rejected",
    "org.bluez.Error.AuthenticationTimeout": "PIN entry timed out",
    "org.bluez.Error.ConnectionAttemptFailed": (
        "could not connect; press User 1 or User 2 so Bluetooth flashes"
    ),
    "org.bluez.Error.Failed": (
        "BlueZ Failed; often a stale bond or the cuff is paired to another device"
    ),
    "org.bluez.Error.InProgress": "another pairing is already in progress",
    "org.bluez.Error.NotReady": "Bluetooth adapter is not ready",
    "org.bluez.Error.AlreadyExists": "already paired on this adapter",
    "org.bluez.Error.NotAvailable": (
        "device not available; wake the cuff and scan again"
    ),
}


def _dbus_plain(value: Any) -> Any:
    """Unwrap a dbus-fast Variant to a plain Python value."""
    return getattr(value, "value", value)


def format_device_snapshot(props: dict[str, Any]) -> str:
    """Return a compact Device1 property summary for logs."""
    parts: list[str] = []
    for key in _SNAPSHOT_KEYS:
        if key not in props:
            continue
        parts.append(f"{key}={_dbus_plain(props[key])!r}")
    return " ".join(parts)


def _iter_exception_chain(err: BaseException) -> list[BaseException]:
    """Walk ``__cause__`` / ``__context__`` without repeating."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def format_pairing_error(err: BaseException) -> str:
    """Return a compact BlueZ/D-Bus error string for logs and the UI."""
    dbus_type: str | None = None
    dbus_text: str | None = None
    for exc in _iter_exception_chain(err):
        name = getattr(exc, "type", None)
        if isinstance(name, str) and name:
            dbus_type = name
            text = getattr(exc, "text", None)
            if isinstance(text, str) and text:
                dbus_text = text
            elif str(exc).strip():
                dbus_text = str(exc).strip()
            break
    if dbus_type:
        core = f"{dbus_type}: {dbus_text}" if dbus_text else dbus_type
        hint = _BLUEZ_ERROR_HINTS.get(dbus_type)
        if hint:
            return f"{core} ({hint})"
        return core
    message = str(err).strip()
    name = type(err).__name__
    if message and message != name:
        return f"{name}: {message}"
    return message or name


def log_pairing_failure(
    address: str,
    stage: str,
    err: BaseException,
    *,
    device_path: str | None = None,
    snapshot: str = "",
) -> None:
    """Log a pairing failure at WARNING (details) and DEBUG (traceback)."""
    where = f"{stage} {device_path}" if device_path else stage
    extra = f"; {snapshot}" if snapshot else ""
    _LOGGER.warning(
        "Pairing failed for %s at %s: %s%s",
        address,
        where,
        format_pairing_error(err),
        extra,
    )
    _LOGGER.debug(
        "Pairing failure traceback for %s at %s",
        address,
        stage,
        exc_info=err,
    )


def is_bluez_pairing_supported() -> bool:
    """Return True when this host can attempt in-UI BlueZ passkey pairing."""
    if sys.platform != "linux":
        return False
    try:
        import dbus_fast  # noqa: F401
    except ImportError:
        return False
    return True


def is_local_bluez_device(device: Any) -> bool:
    """Return True if a Bleak BLEDevice looks like a local BlueZ object."""
    details = getattr(device, "details", None) or {}
    if not isinstance(details, dict):
        return False
    path = details.get("path")
    return isinstance(path, str) and path.startswith("/org/bluez/")


def bluez_path_from_device(device: Any) -> str | None:
    """Return the BlueZ object path from a Bleak BLEDevice, if present."""
    if not is_local_bluez_device(device):
        return None
    details = device.details
    assert isinstance(details, dict)
    path = details.get("path")
    assert isinstance(path, str)
    return path


def _adapter_path_for_device(device_path: str) -> str:
    """Return `/org/bluez/hciX` for a device path `/org/bluez/hciX/dev_…`."""
    return device_path.rsplit("/", 1)[0]


def _dbus_bool(value: Any, default: bool = False) -> bool:
    """Unwrap a dbus-fast Variant or Python bool."""
    if value is None:
        return default
    inner = getattr(value, "value", value)
    return bool(inner)


async def _get_managed_objects(bus: Any) -> dict[str, Any]:
    """Return BlueZ GetManagedObjects() (path → interface → properties)."""
    introspection = await bus.introspect(BLUEZ_SERVICE, "/")
    manager = bus.get_proxy_object(
        BLUEZ_SERVICE, "/", introspection
    ).get_interface(OBJECT_MANAGER_INTERFACE)
    return await manager.call_get_managed_objects()


async def _find_device_path_on_bus(bus: Any, address: str) -> str | None:
    """Find `/org/bluez/hciX/dev_…` for *address* via ObjectManager."""
    objects = await _get_managed_objects(bus)
    target = address.lower()
    for path, interfaces in objects.items():
        device = interfaces.get(DEVICE_INTERFACE)
        if not device:
            continue
        address_variant = device.get("Address")
        if address_variant is None:
            continue
        addr = getattr(address_variant, "value", address_variant)
        if str(addr).lower() == target:
            return str(path)
    return None


async def async_unpair_address(address: str) -> None:
    """Remove the BlueZ bond for *address* (Adapter1.RemoveDevice).

    Never raises: config-entry delete must succeed even if BlueZ remove fails.
    """
    if not is_bluez_pairing_supported():
        _LOGGER.debug(
            "Skipping BlueZ unpair for %s (not Linux / no dbus-fast)", address
        )
        return

    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import BusType

    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        device_path = await _find_device_path_on_bus(bus, address)
        if device_path is None:
            _LOGGER.debug("BlueZ has no device for %s; already unpaired", address)
            return
        adapter_path = _adapter_path_for_device(device_path)
        introspection = await bus.introspect(BLUEZ_SERVICE, adapter_path)
        adapter = bus.get_proxy_object(
            BLUEZ_SERVICE, adapter_path, introspection
        ).get_interface(ADAPTER_INTERFACE)
        await adapter.call_remove_device(device_path)
        _LOGGER.info("Removed BlueZ bond for %s (%s)", address, device_path)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to unpair %s from BlueZ: %s", address, err)
    finally:
        if bus is not None:
            try:
                bus.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("D-Bus disconnect after unpair failed: %s", err)


def _build_passkey_agent(session: BlueZPairSession) -> Any:
    """Build an org.bluez.Agent1 ServiceInterface bound to *session*.

    Annotations are D-Bus signatures, not Python types. Void methods omit the
    return annotation; ``-> None`` is eval'd to Python None and crashes dbus-fast.
    """
    from dbus_fast import DBusError
    from dbus_fast.service import ServiceInterface, dbus_method

    class VerovalPasskeyAgent(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(AGENT_INTERFACE)
            self._session = session

        @dbus_method()
        def Release(self):
            _LOGGER.debug("BlueZ agent Release")

        @dbus_method()
        async def RequestPinCode(self, device: "o") -> "s":
            pin = await self._session._await_passkey(device)
            return f"{pin:06d}"

        @dbus_method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            _LOGGER.debug("DisplayPinCode %s %s", device, pincode)

        @dbus_method()
        async def RequestPasskey(self, device: "o") -> "u":
            return await self._session._await_passkey(device)

        @dbus_method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            _LOGGER.debug(
                "DisplayPasskey %s %06u entered=%s", device, passkey, entered
            )

        @dbus_method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            _LOGGER.warning(
                "Unexpected RequestConfirmation for %s (%06u); rejecting",
                device,
                passkey,
            )
            raise DBusError(
                "org.bluez.Error.Rejected",
                "Numeric comparison is not supported for this cuff",
            )

        @dbus_method()
        def RequestAuthorization(self, device: "o"):
            _LOGGER.debug("RequestAuthorization %s", device)

        @dbus_method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            _LOGGER.debug("AuthorizeService %s %s", device, uuid)

        @dbus_method()
        def Cancel(self):
            _LOGGER.debug("BlueZ agent Cancel")
            self._session._cancel_passkey()

    return VerovalPasskeyAgent()


class BlueZPairSession:
    """Register a BlueZ agent, Pair a cuff, collect the PIN from the UI."""

    def __init__(self, address: str, device_path: str | None = None) -> None:
        """Initialize an idle pairing session for *address*."""
        self.address = address.lower()
        self.device_path = device_path
        self.passkey_requested = asyncio.Event()
        self._passkey_future: asyncio.Future[int] | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._bus: Any = None
        self._agent_registered = False
        self._closed = False
        self._pair_error: BaseException | None = None
        self.already_paired = False

    async def _await_passkey(self, device: str) -> int:
        """Block the BlueZ agent until the config flow supplies a passkey."""
        from dbus_fast import DBusError

        _LOGGER.debug("RequestPasskey for %s", device)
        loop = asyncio.get_running_loop()
        if self._passkey_future is not None and not self._passkey_future.done():
            self._passkey_future.cancel()
        self._passkey_future = loop.create_future()
        self.passkey_requested.set()
        try:
            return await self._passkey_future
        except asyncio.CancelledError as err:
            raise DBusError(
                "org.bluez.Error.Canceled", "Passkey entry cancelled"
            ) from err

    def _cancel_passkey(self) -> None:
        """Cancel a pending passkey future (BlueZ Cancel)."""
        if self._passkey_future is not None and not self._passkey_future.done():
            self._passkey_future.cancel()

    def provide_passkey(self, pin: str) -> None:
        """Complete RequestPasskey with the 6-digit PIN from the UI."""
        digits = pin.strip()
        if not digits.isdigit() or not 1 <= len(digits) <= 6:
            raise ValueError("invalid_pin")
        value = int(digits)
        if value > 999999:
            raise ValueError("invalid_pin")
        future = self._passkey_future
        if future is None or future.cancelled():
            raise PairingFailedError("No passkey request is pending")
        if future.done():
            return
        _LOGGER.debug(
            "Passkey provided for %s (%s digits); PIN value is not logged",
            self.address,
            len(digits),
        )
        future.set_result(value)

    async def open(self) -> None:
        """Connect to the system bus, resolve the device, register the agent."""
        if not is_bluez_pairing_supported():
            raise PairingNotSupportedError(
                "In-UI pairing requires Linux BlueZ (Home Assistant OS host adapter)"
            )

        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType

        _LOGGER.debug("Connecting to BlueZ system bus to pair %s", self.address)
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await self._async_resolve_device_path()

        props = await self._device1_props()
        snapshot = format_device_snapshot(props) or "no Device1 properties"
        _LOGGER.debug(
            "BlueZ Device1 %s path=%s %s",
            self.address,
            self.device_path,
            snapshot,
        )
        if _dbus_bool(props.get("Paired")):
            _LOGGER.debug("%s is already paired", self.address)
            self.already_paired = True
            await self._set_trusted()
            return

        agent = _build_passkey_agent(self)
        self._bus.export(AGENT_PATH, agent)
        introspection = await self._bus.introspect(BLUEZ_SERVICE, AGENT_MANAGER_PATH)
        manager = self._bus.get_proxy_object(
            BLUEZ_SERVICE, AGENT_MANAGER_PATH, introspection
        ).get_interface(AGENT_MANAGER_INTERFACE)
        try:
            await manager.call_register_agent(AGENT_PATH, AGENT_CAPABILITY)
            await manager.call_request_default_agent(AGENT_PATH)
        except Exception as err:
            try:
                self._bus.unexport(AGENT_PATH)
            except Exception:  # noqa: BLE001
                pass
            log_pairing_failure(
                self.address,
                "register_agent",
                err,
                device_path=self.device_path,
                snapshot=snapshot,
            )
            raise AgentUnavailableError(
                "Could not register the Bluetooth pairing agent. "
                "Close bluetoothctl if it is open, then try again."
            ) from err
        self._agent_registered = True
        _LOGGER.debug("Registered BlueZ agent at %s", AGENT_PATH)

    async def start_pair(self) -> None:
        """Begin Device1.Pair in the background (agent must already be open)."""
        if self.already_paired:
            return
        assert self.device_path is not None
        assert self._bus is not None
        self.passkey_requested.clear()
        _LOGGER.info(
            "Calling BlueZ Pair() for %s path=%s",
            self.address,
            self.device_path,
        )
        self._pair_task = asyncio.create_task(
            self._pair_and_trust(), name=f"veroval_ble_pair_{self.address}"
        )

    async def wait_for_passkey_or_done(self) -> str:
        """Wait until BlueZ asks for a PIN, or Pair finishes without one.

        Returns ``need_pin``, ``already_paired``, or ``done``.
        """
        if self.already_paired:
            return "already_paired"
        assert self._pair_task is not None

        _LOGGER.debug(
            "Waiting for cuff PIN or Pair() completion for %s (timeout=%ss)",
            self.address,
            PAIR_TIMEOUT_SECONDS,
        )
        passkey_waiter = asyncio.create_task(
            self.passkey_requested.wait(), name="veroval_ble_passkey_wait"
        )
        done, pending = await asyncio.wait(
            {self._pair_task, passkey_waiter},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=PAIR_TIMEOUT_SECONDS,
        )

        if not done:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending)
            self._cancel_passkey()
            self._pair_task.cancel()
            await asyncio.wait({self._pair_task})
            err = PairingFailedError("Pairing timed out waiting for the cuff PIN")
            log_pairing_failure(
                self.address,
                "wait_for_pin",
                err,
                device_path=self.device_path,
                snapshot=await self._device_snapshot_text(),
            )
            raise err

        if passkey_waiter in done and self.passkey_requested.is_set():
            _LOGGER.debug("BlueZ requested a passkey for %s", self.address)
            if self._pair_task.done():
                exc = self._pair_task.exception()
                if exc is not None:
                    raise PairingFailedError(str(exc)) from exc
            return "need_pin"

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)
        try:
            await self._pair_task
        except Exception as err:
            if self._pair_error is None:
                log_pairing_failure(
                    self.address,
                    "pair_without_pin",
                    err,
                    device_path=self.device_path,
                    snapshot=await self._device_snapshot_text(),
                )
            raise PairingFailedError(str(err)) from err
        if self._pair_error is not None:
            raise PairingFailedError(str(self._pair_error)) from self._pair_error
        _LOGGER.debug("Pair() finished for %s without requesting a PIN", self.address)
        return "done"

    async def wait_finished(self) -> None:
        """Wait for Pair() to finish after the passkey was provided."""
        if self.already_paired:
            return
        assert self._pair_task is not None
        try:
            await asyncio.wait_for(self._pair_task, timeout=PAIR_TIMEOUT_SECONDS)
        except TimeoutError as err:
            self._cancel_passkey()
            self._pair_task.cancel()
            wrapped = PairingFailedError("Pairing timed out after PIN entry")
            log_pairing_failure(
                self.address,
                "wait_after_pin",
                wrapped,
                device_path=self.device_path,
                snapshot=await self._device_snapshot_text(),
            )
            raise wrapped from err
        except Exception as err:
            if self._pair_error is None:
                log_pairing_failure(
                    self.address,
                    "wait_after_pin",
                    err,
                    device_path=self.device_path,
                    snapshot=await self._device_snapshot_text(),
                )
            raise PairingFailedError(str(err)) from err
        if self._pair_error is not None:
            raise PairingFailedError(str(self._pair_error)) from self._pair_error

    async def close(self) -> None:
        """Unregister the agent and disconnect from D-Bus."""
        if self._closed:
            return
        self._closed = True
        self._cancel_passkey()
        if self._pair_task is not None and not self._pair_task.done():
            self._pair_task.cancel()
            try:
                await self._pair_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        if self._bus is not None and self._agent_registered:
            try:
                introspection = await self._bus.introspect(
                    BLUEZ_SERVICE, AGENT_MANAGER_PATH
                )
                manager = self._bus.get_proxy_object(
                    BLUEZ_SERVICE, AGENT_MANAGER_PATH, introspection
                ).get_interface(AGENT_MANAGER_INTERFACE)
                await manager.call_unregister_agent(AGENT_PATH)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("UnregisterAgent failed: %s", err)
            try:
                self._bus.unexport(AGENT_PATH)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("unexport agent failed: %s", err)
            self._agent_registered = False
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("D-Bus disconnect failed: %s", err)
            self._bus = None

    async def _pair_and_trust(self) -> None:
        """Call Device1.Pair, then set Trusted and Disconnect."""
        assert self._bus is not None
        assert self.device_path is not None
        try:
            device = await self._device1_proxy()
            _LOGGER.debug("Calling Pair on %s", self.device_path)
            await device.call_pair()
            await self._set_trusted()
            try:
                await device.call_disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Disconnect after pair: %s", err)
            _LOGGER.info("Paired and trusted %s", self.address)
        except Exception as err:
            self._pair_error = err
            log_pairing_failure(
                self.address,
                "Device1.Pair",
                err,
                device_path=self.device_path,
                snapshot=await self._device_snapshot_text(),
            )
            raise

    async def _set_trusted(self) -> None:
        """Set Device1.Trusted = true."""
        from dbus_fast import Message, MessageType, Variant
        from dbus_fast.errors import DBusError

        device = await self._device1_proxy()
        setter = getattr(device, "set_trusted", None)
        if setter is not None:
            await setter(True)
        else:
            # Device1 XML omitted Trusted; Properties.Set still works at runtime.
            assert self._bus is not None
            assert self.device_path is not None
            reply = await self._bus.call(
                Message(
                    destination=BLUEZ_SERVICE,
                    path=self.device_path,
                    interface=PROPERTIES_INTERFACE,
                    member="Set",
                    signature="ssv",
                    body=[DEVICE_INTERFACE, "Trusted", Variant("b", True)],
                )
            )
            if reply.message_type == MessageType.ERROR:
                raise DBusError._from_message(reply)
        _LOGGER.debug("Trusted %s", self.device_path)

    async def _device_snapshot_text(self) -> str:
        """Return Device1 properties for logs, or an error note."""
        try:
            props = await self._device1_props()
        except Exception as err:  # noqa: BLE001
            return f"Device1 props unavailable ({format_pairing_error(err)})"
        return format_device_snapshot(props) or "no Device1 properties"

    async def _device1_proxy(self) -> Any:
        """Return an org.bluez.Device1 proxy for the resolved path."""
        from dbus_fast.errors import InterfaceNotFoundError

        assert self._bus is not None
        assert self.device_path is not None
        introspection = await self._bus.introspect(BLUEZ_SERVICE, self.device_path)
        try:
            return self._bus.get_proxy_object(
                BLUEZ_SERVICE, self.device_path, introspection
            ).get_interface(DEVICE_INTERFACE)
        except InterfaceNotFoundError as err:
            raise DeviceNotFoundError(
                f"BlueZ has no Device1 interface at {self.device_path}"
            ) from err

    async def _device1_props(self) -> dict[str, Any]:
        """Return Device1 properties via ObjectManager.

        Avoids ``org.freedesktop.DBus.Properties`` on the device proxy; BlueZ
        Introspect XML often omits that interface even though Device1 exists.
        """
        assert self._bus is not None
        assert self.device_path is not None
        objects = await _get_managed_objects(self._bus)
        ifaces = objects.get(self.device_path)
        if not ifaces or DEVICE_INTERFACE not in ifaces:
            resolved = await _find_device_path_on_bus(self._bus, self.address)
            if resolved is None:
                raise DeviceNotFoundError(
                    f"BlueZ has no device object for {self.address}. "
                    "Press User 1 or User 2 so the cuff advertises, then scan again."
                )
            self.device_path = resolved
            objects = await _get_managed_objects(self._bus)
            ifaces = objects.get(resolved) or {}
        return ifaces.get(DEVICE_INTERFACE) or {}

    async def _async_resolve_device_path(self) -> None:
        """Resolve a live BlueZ Device1 path, retrying briefly for export races."""
        last_error: DeviceNotFoundError | None = None
        for attempt in range(8):
            try:
                if self.device_path is None:
                    self.device_path = await self._find_device_path()
                _LOGGER.debug(
                    "Resolve Device1 %s attempt=%s path=%s",
                    self.address,
                    attempt + 1,
                    self.device_path,
                )
                if self.device_path is None:
                    raise DeviceNotFoundError(
                        f"BlueZ has no device object for {self.address}. "
                        "Press User 1 or User 2 so the cuff advertises, then scan again."
                    )
                await self._device1_props()
                return
            except DeviceNotFoundError as err:
                last_error = err
                self.device_path = None
                if attempt == 7:
                    break
                await asyncio.sleep(0.25)
        assert last_error is not None
        _LOGGER.warning(
            "BlueZ has no Device1 object for %s after retries: %s",
            self.address,
            last_error,
        )
        raise last_error

    async def _find_device_path(self) -> str | None:
        """Find `/org/bluez/hciX/dev_…` for this address via ObjectManager."""
        assert self._bus is not None
        return await _find_device_path_on_bus(self._bus, self.address)
