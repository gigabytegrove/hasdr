"""Config flow for RTL-SDR."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RtlSdrApiAuthError, RtlSdrApiClient, RtlSdrApiConnectionError, RtlSdrApiError
from .const import CONF_PORT, CONF_TOKEN, CONF_USE_SSL, DEFAULT_PORT, DOMAIN


class RtlSdrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure an RTL-SDR bridge."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            token = user_input[CONF_TOKEN].strip()
            use_ssl = user_input[CONF_USE_SSL]
            client = RtlSdrApiClient(async_get_clientsession(self.hass), host, port, token, use_ssl)
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
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(health.get("name") or f"RTL-SDR Bridge ({host})"),
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_USE_SSL: use_ssl,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_TOKEN): str,
                vol.Required(CONF_USE_SSL, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
