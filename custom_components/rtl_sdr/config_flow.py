"""Config flow for RTL-SDR."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
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
    MODE_LOCAL,
    MODE_REMOTE,
    MODE_SUPERVISOR,
)
from .local import LocalRtlSdrClient, LocalRuntimeUnavailable
from .supervisor import HasdrSupervisorManager, SupervisorEngineError

_LOGGER = logging.getLogger(__name__)


class RtlSdrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure RTL-SDR."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Offer local setup first and remote hosting only as an advanced option."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["local", "remote"],
        )

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure hardware attached to this Home Assistant system."""
        session = async_get_clientsession(self.hass)
        supervisor = HasdrSupervisorManager(session)

        if supervisor.available:
            if user_input is None:
                return self.async_show_form(
                    step_id="local",
                    data_schema=vol.Schema({}),
                    description_placeholders={"backend": "Home Assistant managed HASDR SDR Engine"},
                )

            api_token = supervisor.generate_api_token()
            try:
                addon_slug, hostname = await supervisor.ensure_engine(api_token)
                client = RtlSdrApiClient(session, hostname, DEFAULT_PORT, api_token, False)
                health: dict[str, Any] | None = None
                last_error: Exception | None = None
                for _ in range(20):
                    try:
                        health = await client.health()
                        break
                    except RtlSdrApiError as err:
                        last_error = err
                        await asyncio.sleep(0.5)
                if health is None:
                    raise SupervisorEngineError(
                        f"HASDR SDR Engine did not become ready: {last_error}"
                    )
            except (SupervisorEngineError, RtlSdrApiError) as err:
                _LOGGER.error("Unable to provision managed HASDR SDR Engine: %s", err)
                return self.async_show_form(
                    step_id="local",
                    data_schema=vol.Schema({}),
                    errors={"base": "engine_install_failed"},
                    description_placeholders={"backend": "Home Assistant managed HASDR SDR Engine"},
                )

            await self.async_set_unique_id("supervisor-managed")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="RTL-SDR",
                data={
                    CONF_MODE: MODE_SUPERVISOR,
                    CONF_ADDON_SLUG: addon_slug,
                    CONF_TOKEN: api_token,
                },
            )

        try:
            client = LocalRtlSdrClient()
            health = await client.health()
            radios = await client.radios()
        except LocalRuntimeUnavailable as err:
            return self.async_show_form(
                step_id="local",
                data_schema=vol.Schema({}),
                errors={"base": "local_runtime_unavailable"},
                description_placeholders={
                    "backend": "embedded local HASDR runtime",
                    "reason": str(err),
                },
            )
        except Exception as err:
            _LOGGER.exception("Unable to initialize local HASDR runtime")
            return self.async_show_form(
                step_id="local",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_start_local"},
                description_placeholders={
                    "backend": "embedded local HASDR runtime",
                    "reason": str(err),
                },
            )
        finally:
            if "client" in locals():
                await client.async_close()

        if user_input is None:
            return self.async_show_form(
                step_id="local",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "backend": "embedded local HASDR runtime",
                    "radio_count": str(len(radios.get("radios", []))),
                    "version": str(health.get("version", "unknown")),
                },
            )

        await self.async_set_unique_id("local")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="RTL-SDR",
            data={CONF_MODE: MODE_LOCAL},
        )

    async def async_step_remote(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure an advanced remote HASDR engine."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            token = user_input[CONF_TOKEN].strip()
            use_ssl = user_input[CONF_USE_SSL]
            client = RtlSdrApiClient(
                async_get_clientsession(self.hass),
                host,
                port,
                token,
                use_ssl,
            )
            try:
                health = await client.health()
            except RtlSdrApiAuthError:
                errors["base"] = "invalid_auth"
            except RtlSdrApiConnectionError:
                errors["base"] = "cannot_connect"
            except RtlSdrApiError:
                errors["base"] = "unknown"
            else:
                unique_id = str(health.get("instance_id") or f"{host}:{port}")
                await self.async_set_unique_id(f"remote:{unique_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(health.get("name") or f"HASDR ({host})"),
                    data={
                        CONF_MODE: MODE_REMOTE,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_USE_SSL: use_ssl,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=1, max=65535),
                ),
                vol.Required(CONF_TOKEN): str,
                vol.Required(CONF_USE_SSL, default=False): bool,
            }
        )
        return self.async_show_form(step_id="remote", data_schema=schema, errors=errors)
