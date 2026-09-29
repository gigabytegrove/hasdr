"""System health for RTL-SDR."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register RTL-SDR system health."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return aggregate HASDR backend health."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    online = 0
    radios = 0
    versions: set[str] = set()
    modes: set[str] = set()

    for entry in entries:
        runtime = entry.runtime_data
        modes.add(runtime.mode)
        try:
            health = await runtime.client.health()
        except Exception:
            continue
        online += 1
        radios += int(health.get("radios", 0))
        versions.add(str(health.get("version", "unknown")))

    return {
        "configured_backends": len(entries),
        "online_backends": online,
        "detected_radios": radios,
        "engine_versions": ", ".join(sorted(versions)) if versions else "unknown",
        "backend_modes": ", ".join(sorted(modes)) if modes else "none",
    }
