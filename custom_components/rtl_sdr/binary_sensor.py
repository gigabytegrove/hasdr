"""Binary sensors for RTL-SDR receivers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RtlSdrConfigEntry
from .const import DOMAIN
from .coordinator import RtlSdrCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RtlSdrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_new_radios() -> None:
        radios = (coordinator.data or {}).get("radios", {})
        entities: list[RtlSdrBusySensor] = []
        for radio_id in radios:
            if radio_id not in known:
                known.add(radio_id)
                entities.append(RtlSdrBusySensor(coordinator, entry, radio_id))
        if entities:
            async_add_entities(entities)

    add_new_radios()
    entry.async_on_unload(coordinator.async_add_listener(add_new_radios))


class RtlSdrBusySensor(CoordinatorEntity[RtlSdrCoordinator], BinarySensorEntity):
    """Indicates whether a receiver is occupied by a job."""

    _attr_has_entity_name = True
    _attr_translation_key = "busy"

    def __init__(
        self,
        coordinator: RtlSdrCoordinator,
        entry: RtlSdrConfigEntry,
        radio_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._radio_id = radio_id
        self._attr_unique_id = f"{entry.entry_id}_{radio_id}_busy"

    @property
    def _radio(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("radios", {}).get(self._radio_id, {})

    @property
    def is_on(self) -> bool:
        return self._radio.get("job") is not None

    @property
    def available(self) -> bool:
        return bool(self._radio.get("present", True)) and super().available

    @property
    def device_info(self) -> DeviceInfo:
        radio = self._radio
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}:{self._radio_id}")},
            name=radio.get("name") or f"RTL-SDR {radio.get('serial') or self._radio_id}",
            manufacturer=radio.get("manufacturer") or "Realtek",
            model=radio.get("product") or radio.get("name") or "RTL-SDR",
            serial_number=radio.get("serial"),
            via_device=(DOMAIN, self._entry.entry_id),
        )
