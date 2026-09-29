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
    CONF_ADDON_SLUG,
    CONF_MODE,
    CONF_PORT,
    CONF_TOKEN,
    CONF_USE_SSL,
    DEFAULT_PORT,
    DOMAIN,
    EVENT_DECODED_PACKET,
    EVENT_JOB_ERROR,
    EVENT_SCAN_COMPLETE,
    EVENT_SIGNAL_DETECTED,
    MODE_LOCAL,
    MODE_REMOTE,
    MODE_SUPERVISOR,
    PLATFORMS,
    SERVICE_GET_LAST_SCAN,
    SERVICE_REFRESH_RADIOS,
    SERVICE_SCAN,
    SERVICE_START_DECODER,
    SERVICE_STOP,
)
from .coordinator import RtlSdrCoordinator
from .local import LocalRtlSdrClient, LocalRuntimeUnavailable
from .runtime_bootstrap import (
    RuntimeBootstrapError,
    async_prepare_local_runtime,
    local_usb_access_available,
)
from .supervisor import HasdrSupervisorManager, SupervisorEngineError


@dataclass(slots=True)
class RtlSdrRuntimeData:
    """Runtime state owned by one config entry."""

    client: Any
    coordinator: RtlSdrCoordinator
    health: dict[str, Any]
    mode: str
    stop_event: asyncio.Event
    event_task: asyncio.Task[Any]


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


def _service_error(err: Exception) -> ServiceValidationError:
    return ServiceValidationError(
        str(err),
        translation_domain=DOMAIN,
        translation_key="backend_request_failed",
        translation_placeholders={"error": str(err)},
    )


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
        except (RtlSdrApiError, RuntimeError, ValueError) as err:
            raise _service_error(err) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_start_decoder(call: ServiceCall) -> None:
        data = dict(call.data)
        runtime = _entry_runtime(hass, data.pop("config_entry_id"))
        radio_id = data.pop("radio_id")
        try:
            await runtime.client.start_decoder(radio_id, data)
        except (RtlSdrApiError, RuntimeError, ValueError) as err:
            raise _service_error(err) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_stop(call: ServiceCall) -> None:
        data = dict(call.data)
        runtime = _entry_runtime(hass, data["config_entry_id"])
        try:
            await runtime.client.stop(data["radio_id"])
        except (RtlSdrApiError, RuntimeError, ValueError) as err:
            raise _service_error(err) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_refresh(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data["config_entry_id"])
        try:
            await runtime.client.refresh_radios()
        except (RtlSdrApiError, RuntimeError, ValueError) as err:
            raise _service_error(err) from err
        await runtime.coordinator.async_request_refresh()

    async def handle_get_last_scan(call: ServiceCall) -> ServiceResponse:
        runtime = _entry_runtime(hass, call.data["config_entry_id"])
        try:
            latest = await runtime.client.latest_scan(call.data["radio_id"])
        except (RtlSdrApiError, RuntimeError, ValueError) as err:
            raise _service_error(err) from err
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


async def _async_create_backend(
    hass: HomeAssistant,
    entry: RtlSdrConfigEntry,
) -> tuple[Any, dict[str, Any], str]:
    session = async_get_clientsession(hass)
    mode = entry.data.get(CONF_MODE)
    if mode is None:
        mode = MODE_REMOTE if CONF_HOST in entry.data else MODE_LOCAL

    if mode == MODE_LOCAL:
        if not local_usb_access_available():
            raise ConfigEntryNotReady(
                "Home Assistant Container cannot access /dev/bus/usb; map the USB bus into the container"
            )
        try:
            runtime_info = await async_prepare_local_runtime(hass.config.config_dir)
            client = LocalRtlSdrClient(runtime_info)
            health = await client.health()
        except (RuntimeBootstrapError, LocalRuntimeUnavailable) as err:
            raise ConfigEntryNotReady(
                f"Local HASDR runtime is unavailable: {err}"
            ) from err
        return client, health, mode

    if mode == MODE_SUPERVISOR:
        supervisor = HasdrSupervisorManager(session)
        if not supervisor.available:
            raise ConfigEntryNotReady("Home Assistant Supervisor is unavailable")

        token = entry.data[CONF_TOKEN]
        try:
            addon_slug, hostname = await supervisor.ensure_engine(token)
            if entry.data.get(CONF_ADDON_SLUG) != addon_slug:
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_ADDON_SLUG: addon_slug},
                )
            await supervisor.mark_system_managed(addon_slug, entry.entry_id)
            client = RtlSdrApiClient(session, hostname, DEFAULT_PORT, token, False)
            health = await client.health()
        except SupervisorEngineError as err:
            raise ConfigEntryNotReady(
                f"Unable to provision the managed HASDR SDR Engine: {err}"
            ) from err
        except RtlSdrApiAuthError as err:
            raise ConfigEntryAuthFailed("Managed HASDR SDR Engine rejected its internal token") from err
        except RtlSdrApiError as err:
            raise ConfigEntryNotReady(f"Managed HASDR SDR Engine is unavailable: {err}") from err
        return client, health, mode

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
        raise ConfigEntryAuthFailed("Remote HASDR Engine rejected the API token") from err
    except RtlSdrApiConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to reach remote HASDR Engine: {err}") from err
    except RtlSdrApiError as err:
        raise ConfigEntryNotReady(f"Remote HASDR Engine setup failed: {err}") from err
    return client, health, MODE_REMOTE


async def async_setup_entry(hass: HomeAssistant, entry: RtlSdrConfigEntry) -> bool:
    """Set up a HASDR config entry."""
    client, health, mode = await _async_create_backend(hass, entry)

    coordinator = RtlSdrCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Gigabyte Grove",
        model={
            MODE_LOCAL: "Embedded HASDR Runtime",
            MODE_SUPERVISOR: "Managed HASDR SDR Engine",
            MODE_REMOTE: "Remote HASDR SDR Engine",
        }.get(mode, "HASDR SDR Engine"),
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

    event_task = entry.async_create_background_task(
        hass,
        client.event_loop(on_event, stop_event),
        f"{DOMAIN}_events_{entry.entry_id}",
    )
    entry.runtime_data = RtlSdrRuntimeData(
        client=client,
        coordinator=coordinator,
        health=health,
        mode=mode,
        stop_event=stop_event,
        event_task=event_task,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RtlSdrConfigEntry) -> bool:
    """Unload an RTL-SDR backend."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime = entry.runtime_data
    runtime.stop_event.set()
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)
    await runtime.coordinator.async_shutdown()

    close = getattr(runtime.client, "async_close", None)
    if close is not None:
        await close()

    return True
