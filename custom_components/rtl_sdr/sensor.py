"""Sensors for RTL-SDR receivers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RtlSdrConfigEntry
from .const import DOMAIN
from .coordinator import RtlSdrCoordinator


@dataclass(frozen=True, kw_only=True)
class RtlSdrSensorDescription(SensorEntityDescription):
    """Describe an RTL-SDR sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


def _job_mode(radio: dict[str, Any]) -> str | None:
    job = radio.get("job")
    return job.get("mode") if isinstance(job, dict) else None


def _last_scan(radio: dict[str, Any], key: str) -> Any:
    scan = radio.get("last_scan")
    return scan.get(key) if isinstance(scan, dict) else None


def _last_packet(radio: dict[str, Any], key: str) -> Any:
    packet = radio.get("last_packet")
    return packet.get(key) if isinstance(packet, dict) else None


SENSORS: tuple[RtlSdrSensorDescription, ...] = (
    RtlSdrSensorDescription(
        key="status",
        translation_key="status",
        value_fn=lambda r: "busy" if r.get("job") else ("available" if r.get("present", True) else "disconnected"),
    ),
    RtlSdrSensorDescription(key="active_job", translation_key="active_job", value_fn=_job_mode),
    RtlSdrSensorDescription(
        key="peak_frequency",
        translation_key="peak_frequency",
        native_unit_of_measurement="MHz",
        value_fn=lambda r: (float(_last_scan(r, "peak_frequency_hz")) / 1_000_000)
        if _last_scan(r, "peak_frequency_hz") is not None
        else None,
    ),
    RtlSdrSensorDescription(
        key="peak_power",
        translation_key="peak_power",
        native_unit_of_measurement="dB",
        value_fn=lambda r: _last_scan(r, "peak_power_db"),
    ),
    RtlSdrSensorDescription(
        key="noise_floor",
        translation_key="noise_floor",
        native_unit_of_measurement="dB",
        value_fn=lambda r: _last_scan(r, "noise_floor_db"),
    ),
    RtlSdrSensorDescription(
        key="last_decoded_model",
        translation_key="last_decoded_model",
        value_fn=lambda r: _last_packet(r, "model"),
    ),
    RtlSdrSensorDescription(
        key="last_decoded_frequency",
        translation_key="last_decoded_frequency",
        native_unit_of_measurement="MHz",
        value_fn=lambda r: _last_packet(r, "frequency_mhz"),
    ),
    RtlSdrSensorDescription(
        key="decoded_packets",
        translation_key="decoded_packets",
        state_class="total_increasing",
        value_fn=lambda r: int(r.get("decoded_packets", 0)),
    ),
)


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
        entities: list[RtlSdrSensor] = []
        for radio_id in radios:
            if radio_id in known:
                continue
            known.add(radio_id)
            entities.extend(
                RtlSdrSensor(coordinator, entry, radio_id, description)
                for description in SENSORS
            )
        if entities:
            async_add_entities(entities)

    add_new_radios()
    entry.async_on_unload(coordinator.async_add_listener(add_new_radios))


class RtlSdrSensor(CoordinatorEntity[RtlSdrCoordinator], SensorEntity):
    """One receiver sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RtlSdrCoordinator,
        entry: RtlSdrConfigEntry,
        radio_id: str,
        description: RtlSdrSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._radio_id = radio_id
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{radio_id}_{description.key}"

    @property
    def _radio(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("radios", {}).get(self._radio_id, {})

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._radio)

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
