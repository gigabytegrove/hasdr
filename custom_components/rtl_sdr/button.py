"""One-click receiver controls for RTL-SDR."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RtlSdrConfigEntry
from .api import RtlSdrApiError
from .const import DOMAIN
from .controls import (
    MONITOR_FREQUENCY_MHZ,
    SWEEP_END_MHZ,
    SWEEP_START_MHZ,
    get_radio_controls,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RtlSdrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons for discovered receivers."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_new_radios() -> None:
        radios = (coordinator.data or {}).get("radios", {})
        entities: list[RtlSdrButtonBase] = []
        for radio_id in radios:
            if radio_id in known:
                continue
            known.add(radio_id)
            entities.extend(
                (
                    RtlSdrSweepButton(entry, radio_id),
                    RtlSdrMonitorButton(entry, radio_id),
                    RtlSdrStopButton(entry, radio_id),
                )
            )
        if entities:
            async_add_entities(entities)

    add_new_radios()
    entry.async_on_unload(coordinator.async_add_listener(add_new_radios))


class RtlSdrButtonBase(ButtonEntity):
    """Base receiver button."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: RtlSdrConfigEntry,
        radio_id: str,
        key: str,
    ) -> None:
        self._entry = entry
        self._radio_id = radio_id
        self._attr_unique_id = f"{entry.entry_id}_{radio_id}_{key}"

    @property
    def _radio(self) -> dict[str, Any]:
        coordinator = self._entry.runtime_data.coordinator
        return (coordinator.data or {}).get("radios", {}).get(self._radio_id, {})

    async def async_added_to_hass(self) -> None:
        """Update button availability when receiver state changes."""
        await super().async_added_to_hass()
        coordinator = self._entry.runtime_data.coordinator
        self.async_on_remove(
            coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Link the button to the receiver device."""
        radio = self._radio
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}:{self._radio_id}")},
            name=radio.get("name")
            or f"RTL-SDR {radio.get('serial') or self._radio_id}",
            manufacturer=radio.get("manufacturer") or "Realtek",
            model=radio.get("product") or radio.get("name") or "RTL-SDR",
            serial_number=radio.get("serial"),
        )

    async def _refresh(self) -> None:
        await self._entry.runtime_data.coordinator.async_request_refresh()

    @staticmethod
    def _mhz_to_hz(value: float) -> int:
        return round(value * 1_000_000)


class RtlSdrSweepButton(RtlSdrButtonBase):
    """Run the configured one-shot sweep."""

    _attr_translation_key = "run_sweep"
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, entry: RtlSdrConfigEntry, radio_id: str) -> None:
        super().__init__(entry, radio_id, "run_sweep")

    @property
    def available(self) -> bool:
        return bool(self._radio.get("present", True)) and not self._radio.get("job")

    async def async_press(self) -> None:
        controls = get_radio_controls(self._entry, self._radio_id)
        start_hz = self._mhz_to_hz(controls[SWEEP_START_MHZ])
        end_hz = self._mhz_to_hz(controls[SWEEP_END_MHZ])
        if end_hz <= start_hz:
            raise ValueError("Sweep end frequency must be greater than start frequency")

        try:
            await self._entry.runtime_data.client.start_scan(
                self._radio_id,
                {
                    "start_frequency_hz": start_hz,
                    "end_frequency_hz": end_hz,
                    "bin_width_hz": 25_000,
                    "integration_seconds": 1,
                    "ppm": 0,
                    "bias_tee": False,
                    "threshold_db_above_noise": 10,
                },
            )
        except (RtlSdrApiError, RuntimeError) as err:
            raise RuntimeError(f"Unable to start sweep: {err}") from err
        await self._refresh()


class RtlSdrMonitorButton(RtlSdrButtonBase):
    """Start continuous generic monitoring on one frequency."""

    _attr_translation_key = "start_monitor"
    _attr_icon = "mdi:radio-tower"

    def __init__(self, entry: RtlSdrConfigEntry, radio_id: str) -> None:
        super().__init__(entry, radio_id, "start_monitor")

    @property
    def available(self) -> bool:
        return bool(self._radio.get("present", True)) and not self._radio.get("job")

    async def async_press(self) -> None:
        controls = get_radio_controls(self._entry, self._radio_id)
        frequency_hz = self._mhz_to_hz(controls[MONITOR_FREQUENCY_MHZ])

        try:
            await self._entry.runtime_data.client.start_monitor(
                self._radio_id,
                {
                    "frequency_hz": frequency_hz,
                    "span_hz": 200_000,
                    "bin_width_hz": 25_000,
                    "integration_seconds": 1,
                    "ppm": 0,
                    "bias_tee": False,
                    "threshold_db_above_noise": 10,
                },
            )
        except (RtlSdrApiError, RuntimeError) as err:
            raise RuntimeError(f"Unable to start monitor: {err}") from err
        await self._refresh()


class RtlSdrStopButton(RtlSdrButtonBase):
    """Stop the current receiver job."""

    _attr_translation_key = "stop_receiver"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, entry: RtlSdrConfigEntry, radio_id: str) -> None:
        super().__init__(entry, radio_id, "stop_receiver")

    @property
    def available(self) -> bool:
        return bool(self._radio.get("present", True)) and bool(self._radio.get("job"))

    async def async_press(self) -> None:
        try:
            await self._entry.runtime_data.client.stop(self._radio_id)
        except (RtlSdrApiError, RuntimeError) as err:
            raise RuntimeError(f"Unable to stop receiver: {err}") from err
        await self._refresh()
