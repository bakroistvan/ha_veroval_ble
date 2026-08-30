"""Debug actions for Veroval Blood Pressure BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SERVICE_FORCE_DUMP
from .coordinator import CuffNotConnectableError, VerovalBleCoordinator
from .parser import BloodPressureMeasurement

_SERVICES_FLAG = f"{DOMAIN}_services_registered"


def _loaded_coordinators(hass: HomeAssistant) -> list[VerovalBleCoordinator]:
    """Return coordinators for loaded Veroval config entries."""
    coordinators: list[VerovalBleCoordinator] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if isinstance(runtime, VerovalBleCoordinator):
            coordinators.append(runtime)
    return coordinators


def _ident_for(coordinator: VerovalBleCoordinator) -> str:
    return f"{coordinator.address}_{coordinator.cuff_user}".lower()


def coordinators_for_service_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[VerovalBleCoordinator]:
    """Resolve target devices, or every loaded Veroval device if none given."""
    coordinators = _loaded_coordinators(hass)
    if not coordinators:
        raise HomeAssistantError("No Veroval Blood Pressure BLE device is set up.")

    raw_ids = call.data.get(ATTR_DEVICE_ID)
    if not raw_ids:
        return coordinators
    if isinstance(raw_ids, str):
        device_ids = [raw_ids]
    else:
        device_ids = [str(item) for item in raw_ids]

    registry = dr.async_get(hass)
    by_ident = {_ident_for(coord): coord for coord in coordinators}
    selected: list[VerovalBleCoordinator] = []
    seen: set[str] = set()
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Unknown device_id {device_id}")
        matched = False
        for ident_domain, ident in device.identifiers:
            if ident_domain != DOMAIN:
                continue
            coord = by_ident.get(str(ident).lower())
            if coord is None:
                continue
            key = _ident_for(coord)
            if key not in seen:
                seen.add(key)
                selected.append(coord)
            matched = True
        if not matched:
            raise HomeAssistantError(
                f"Device {device_id} is not a Veroval Blood Pressure BLE cuff."
            )
    return selected


def measurement_payload(
    coordinator: VerovalBleCoordinator,
    measurement: BloodPressureMeasurement | None,
) -> dict[str, Any]:
    """JSON-friendly dump result for Developer Tools."""
    payload: dict[str, Any] = {
        "address": coordinator.address,
        "cuff_user": coordinator.cuff_user,
        "synced": measurement is not None,
    }
    if measurement is None:
        return payload
    payload.update(
        {
            "systolic": measurement.systolic,
            "diastolic": measurement.diastolic,
            "pulse": measurement.pulse,
            "timestamp": measurement.timestamp.isoformat(),
            "irregular_pulse": measurement.irregular_pulse,
        }
    )
    return payload


async def async_force_dump_coordinators(
    coordinators: list[VerovalBleCoordinator],
) -> list[dict[str, Any]]:
    """Force one GATT dump per cuff address; other slots consume that dump."""
    dumped_addresses: set[str] = set()
    results: list[dict[str, Any]] = []
    for coordinator in coordinators:
        address = coordinator.address.lower()
        try:
            if address in dumped_addresses:
                measurement = coordinator.device_data.consume_shared_dump(
                    coordinator.cuff_user
                )
                coordinator.async_publish_measurement(measurement)
            else:
                measurement = await coordinator.async_force_poll()
                dumped_addresses.add(address)
        except CuffNotConnectableError as err:
            raise HomeAssistantError(str(err)) from err
        results.append(measurement_payload(coordinator, measurement))
    return results


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register debug actions once."""
    if hass.data.get(_SERVICES_FLAG):
        return

    async def _handle_force_dump(call: ServiceCall) -> dict[str, Any]:
        coordinators = coordinators_for_service_call(hass, call)
        dumps = await async_force_dump_coordinators(coordinators)
        return {"dumps": dumps}

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_DUMP,
        _handle_force_dump,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.data[_SERVICES_FLAG] = True


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove debug actions when the last config entry unloads."""
    if not hass.data.pop(_SERVICES_FLAG, None):
        return
    hass.services.async_remove(DOMAIN, SERVICE_FORCE_DUMP)
