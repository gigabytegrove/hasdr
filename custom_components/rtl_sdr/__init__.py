"""RTL-SDR integration for Home Assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RtlSdrApiAuthError, RtlSdrApiClient, RtlSdrApiConnectionError, RtlSdrApiError
from .const import (
    CONF_PORT,
    CONF_TOKEN,
    CONF_USE_SSL,
    DOMAIN,
    EVENT_DECODED_PACKET,
    EVENT_JOB_ERROR,
    EVENT_SCAN_COMPLETE,
    EVENT_SIGNAL_DETECTED,
    PLATFORMS,
    SERVICE_GET_LAST_SCAN,
    SERVICE_REFRESH_RADIOS,
    SERVICE_SCAN,
    SERVICE_START_DECODER,
    SERVICE_STOP,
)
from .coordinator import RtlSdrCoordinator


@dataclass(slots=True)
class RtlSdrRuntimeData:
    """Runtime state owned by one config entry."""

    client: RtlSdrApiClient
    coordinator: RtlSdrCoordinator
    health: dict[str, Any]
    stop_event: asyncio.Event
    websocket_task: asyncio.Task[Any]


type RtlSdrConfigEntry = ConfigEntry[RtlSdrRuntimeData]


SCAN_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("radio_id"): cv.string,
        vol.Required("start_frequency_hz"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Required("end_frequency_hz"): vol.All(vol.Coerce(int), vol.Range(min=2)),
        vol.Optional("bin_width_hz", default=25_000): vol.All(vol.Coerce(int), vol.Range(min=1, max=2_800_000)),
        vol.Optional("integration_seconds", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        vol.Optional("ppm", default=0): vol.All(vol.Coerce(int), vol.Range(min=-1000, max=1000)),
        vol.Optional("gain_db"): vol.Coerce(float),
        vol.Optional("bias_tee", default=False): cv.boolean,
        vol.Optional("threshold_db_above_noise", default=10.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    }
)

DECODE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("radio_id"): cv.string,
        vol.Required("frequencies_hz"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1))]),
        vol.Optional("sample_rate_hz", default=250_000): vol.All(vol.Coerce(int), vol.Range(min=10_000, max=3_200_000)),
        vol.Optional("hop_seconds", default=15): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        vol.Optional("ppm", default=0): vol.All(vol.Coerce(int), vol.Range(min=-1000, max=1000)),
        vol.Optional("gain_db"): vol.Coerce(float),
        vol.Optional("bias_tee", default=False): cv.boolean,
        vol.Optional("protocols"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1))]),
    }
)

STOP_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("radio_id"): cv.string,
    }
)

REFRESH_SCHEMA = vol.Schema({vol.Required("config_entry_id"): cv.string})
GET_LAST_SCAN_SCHEMA = STOP_SCHEMA


def _entry_runtime(hass: HomeAssistant, entry_id: str) -> RtlSdrRuntimeData:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            "RTL-SDR config entry not found",
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            "RTL-SDR config entry is not loaded",
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )
    typed_entry = cast(RtlSdrConfigEntry, entry)
    return typed_entry.runtime_data


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-wide actions."""
    if hass.services.has_service(DOMAIN, SERVICE_SCAN):
        return True

    async def handle_scan(call: ServiceCall) -> None:
        data = dict(call.data)
        runtime = _entry_runtime(hass, data.pop("config_entry_id"))
        radio_id = data.pop("radio_id")
        if data["end_frequency_hz"] <= data["start_frequency_hz"]:
            raise ServiceValidationError(
                "End frequency must be greater than start frequency",
                translation_domain=DOMAIN,
                translation_key="invalid_frequency_range",
            )
        try:
            await runtime.client.start_scan(radio_id, data)
        except RtlSdrApiError as err:
            raise ServiceValidationError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="bridge_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_start_decoder(call: ServiceCall) -> None:
        data = dict(call.data)
        runtime = _entry_runtime(hass, data.pop("config_entry_id"))
        radio_id = data.pop("radio_id")
        try:
            await runtime.client.start_decoder(radio_id, data)
        except RtlSdrApiError as err:
            raise ServiceValidationError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="bridge_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_stop(call: ServiceCall) -> None:
        data = dict(call.data)
        runtime = _entry_runtime(hass, data["config_entry_id"])
        try:
            await runtime.client.stop(data["radio_id"])
        except RtlSdrApiError as err:
            raise ServiceValidationError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="bridge_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_refresh(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data["config_entry_id"])
        try:
            await runtime.client.refresh_radios()
        except RtlSdrApiError as err:
            raise ServiceValidationError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="bridge_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_get_last_scan(call: ServiceCall) -> ServiceResponse:
        runtime = _entry_runtime(hass, call.data["config_entry_id"])
        try:
            latest = await runtime.client.latest_scan(call.data["radio_id"])
        except RtlSdrApiError as err:
            raise ServiceValidationError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="bridge_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return {"scan": latest.get("scan")}

    hass.services.async_register(DOMAIN, SERVICE_SCAN, handle_scan, schema=SCAN_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_START_DECODER, handle_start_decoder, schema=DECODE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, handle_stop, schema=STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_RADIOS, handle_refresh, schema=REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_LAST_SCAN,
        handle_get_last_scan,
        schema=GET_LAST_SCAN_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RtlSdrConfigEntry) -> bool:
    """Set up a bridge config entry."""
    session = async_get_clientsession(hass)
    client = RtlSdrApiClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
        entry.data.get(CONF_USE_SSL, False),
    )

    try:
        health = await client.health()
    except RtlSdrApiAuthError as err:
        raise ConfigEntryAuthFailed("RTL-SDR bridge rejected the API token") from err
    except RtlSdrApiConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to reach RTL-SDR bridge: {err}") from err
    except RtlSdrApiError as err:
        raise ConfigEntryNotReady(f"RTL-SDR bridge setup failed: {err}") from err

    coordinator = RtlSdrCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Gigabyte Grove",
        model="RTL-SDR Bridge",
        sw_version=str(health.get("version", "unknown")),
    )

    stop_event = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        await coordinator.async_process_event(event)
        event_type = event.get("type")
        if event_type == "decoded_packet":
            hass.bus.async_fire(EVENT_DECODED_PACKET, event)
            hass.bus.async_fire(EVENT_SIGNAL_DETECTED, event)
        elif event_type == "scan_complete":
            result = dict(event.get("result", {}))
            result.pop("bins", None)
            result.pop("detections", None)
            hass.bus.async_fire(EVENT_SCAN_COMPLETE, {**event, "result": result})
        elif event_type == "job_error":
            hass.bus.async_fire(EVENT_JOB_ERROR, event)

    websocket_task = entry.async_create_background_task(
        hass,
        client.websocket_loop(on_event, stop_event),
        f"{DOMAIN}_websocket_{entry.entry_id}",
    )
    entry.runtime_data = RtlSdrRuntimeData(
        client=client,
        coordinator=coordinator,
        health=health,
        stop_event=stop_event,
        websocket_task=websocket_task,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RtlSdrConfigEntry) -> bool:
    """Unload an RTL-SDR bridge."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry.runtime_data.stop_event.set()
    entry.runtime_data.websocket_task.cancel()
    await asyncio.gather(entry.runtime_data.websocket_task, return_exceptions=True)
    await entry.runtime_data.coordinator.async_shutdown()
    return True
