"""State coordinator for RTL-SDR."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RtlSdrApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class RtlSdrCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Combine backend snapshots with push-derived radio state."""

    def __init__(self, hass: HomeAssistant, client: Any) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=15),
        )
        self.client = client
        self.radio_state: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            radios_payload = await self.client.radios()
            jobs_payload = await self.client.jobs()
        except (RtlSdrApiError, RuntimeError) as err:
            raise UpdateFailed(str(err)) from err

        radios = radios_payload.get("radios", [])
        jobs = jobs_payload.get("jobs", [])
        jobs_by_radio = {job["radio_id"]: job for job in jobs if "radio_id" in job}

        for radio in radios:
            radio_id = radio["id"]
            state = self.radio_state.setdefault(radio_id, {})
            state.update(radio)
            state["job"] = jobs_by_radio.get(radio_id)

        visible = {radio["id"] for radio in radios}
        for radio_id in list(self.radio_state):
            if radio_id not in visible:
                self.radio_state[radio_id]["present"] = False

        return {"radios": self.radio_state, "jobs": jobs}

    async def async_process_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        radio_id = event.get("radio_id")
        if not isinstance(radio_id, str):
            return

        state = self.radio_state.setdefault(radio_id, {"id": radio_id})

        if event_type == "scan_complete":
            result = event.get("result", {})
            state["last_scan"] = result
            state["job"] = None
        elif event_type == "decoded_packet":
            packet = event.get("packet", {})
            state["last_packet"] = packet
            state["decoded_packets"] = int(state.get("decoded_packets", 0)) + 1
        elif event_type == "job_started":
            state["job"] = event.get("job")
        elif event_type in ("job_stopped", "job_error", "job_finished"):
            state["job"] = None
            if event_type == "job_error":
                state["last_error"] = event.get("error")
        elif event_type == "radios_changed":
            await self.async_request_refresh()
            return

        if self.data is None:
            self.async_set_updated_data({"radios": self.radio_state, "jobs": []})
        else:
            updated = dict(self.data)
            updated["radios"] = self.radio_state
            self.async_set_updated_data(updated)
