"""Editable frequency controls for RTL-SDR receivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RtlSdrConfigEntry
from .const import DOMAIN
from .controls import (
    MONITOR_FREQUENCY_MHZ,
    SWEEP_END_MHZ,
    SWEEP_START_MHZ,
    get_radio_controls,
)


@dataclass(frozen=True, kw_only=True)
class RtlSdrNumberDescription(NumberEntityDescription):
    """Describe an editable receiver frequency."""

    control_key: str
    default_value: float


NUMBERS: tuple[RtlSdrNumberDescription, ...] = (
    RtlSdrNumberDescription(
        key=SWEEP_START_MHZ,
        translation_key="sweep_start_frequency",
        control_key=SWEEP_START_MHZ,
        default_value=88.0,
        native_min_value=0.001,
        native_max_value=3000.0,
        native_step=0.0001,
        native_unit_of_measurement="MHz",
        mode=NumberMode.BOX,
    ),
    RtlSdrNumberDescription(
        key=SWEEP_END_MHZ,
        translation_key="sweep_end_frequency",
        control_key=SWEEP_END_MHZ,
        default_value=108.0,
        native_min_value=0.001,
        native_max_value=3000.0,
        native_step=0.0001,
        native_unit_of_measurement="MHz",
        mode=NumberMode.BOX,
    ),
    RtlSdrNumberDescription(
        key=MONITOR_FREQUENCY_MHZ,
        translation_key="monitor_frequency",
        control_key=MONITOR_FREQUENCY_MHZ,
        default_value=433.92,
        native_min_value=0.001,
        native_max_value=3000.0,
        native_step=0.0001,
        native_unit_of_measurement="MHz",
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RtlSdrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up editable controls for discovered receivers."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_new_radios() -> None:
        radios = (coordinator.data or {}).get("radios", {})
        entities: list[RtlSdrFrequencyNumber] = []
        for radio_id in radios:
            if radio_id in known:
                continue
            known.add(radio_id)
            entities.extend(
                RtlSdrFrequencyNumber(entry, radio_id, description)
                for description in NUMBERS
            )
        if entities:
            async_add_entities(entities)

    add_new_radios()
    entry.async_on_unload(coordinator.async_add_listener(add_new_radios))


class RtlSdrFrequencyNumber(RestoreNumber):
    """One editable frequency value stored on a receiver device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: RtlSdrConfigEntry,
        radio_id: str,
        description: RtlSdrNumberDescription,
    ) -> None:
        self._entry = entry
        self._radio_id = radio_id
        self.entity_description = description
        self._attr_unique_id = (
            f"{entry.entry_id}_{radio_id}_{description.control_key}"
        )
        self._value = description.default_value

    @property
    def _radio(self) -> dict[str, Any]:
        coordinator = self._entry.runtime_data.coordinator
        return (coordinator.data or {}).get("radios", {}).get(self._radio_id, {})

    async def async_added_to_hass(self) -> None:
        """Restore the previous input value and follow receiver availability."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data is not None and last_data.native_value is not None:
            self._value = float(last_data.native_value)

        controls = get_radio_controls(self._entry, self._radio_id)
        controls[self.entity_description.control_key] = self._value

        coordinator = self._entry.runtime_data.coordinator
        self.async_on_remove(
            coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> float:
        """Return the configured MHz value."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Store a new MHz value."""
        self._value = float(value)
        controls = get_radio_controls(self._entry, self._radio_id)
        controls[self.entity_description.control_key] = self._value
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the receiver is attached."""
        return bool(self._radio.get("present", True))

    @property
    def device_info(self) -> DeviceInfo:
        """Link the control to the receiver device."""
        radio = self._radio
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}:{self._radio_id}")},
            name=radio.get("name")
            or f"RTL-SDR {radio.get('serial') or self._radio_id}",
            manufacturer=radio.get("manufacturer") or "Realtek",
            model=radio.get("product") or radio.get("name") or "RTL-SDR",
            serial_number=radio.get("serial"),
        )
