"""Diagnostics for RTL-SDR."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from . import RtlSdrConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: RtlSdrConfigEntry,
) -> dict[str, Any]:
    """Return non-sensitive diagnostics."""
    runtime = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "health": runtime.health,
        "coordinator": runtime.coordinator.data,
    }
