"""Shared editable controls for RTL-SDR receiver devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RtlSdrConfigEntry

SWEEP_START_MHZ = "sweep_start_mhz"
SWEEP_END_MHZ = "sweep_end_mhz"
MONITOR_FREQUENCY_MHZ = "monitor_frequency_mhz"

DEFAULT_CONTROLS: dict[str, float] = {
    SWEEP_START_MHZ: 88.0,
    SWEEP_END_MHZ: 108.0,
    MONITOR_FREQUENCY_MHZ: 433.92,
}


def get_radio_controls(
    entry: RtlSdrConfigEntry,
    radio_id: str,
) -> dict[str, float]:
    """Return mutable controls for one receiver, populated with defaults."""
    controls = entry.runtime_data.controls.setdefault(radio_id, {})
    for key, value in DEFAULT_CONTROLS.items():
        controls.setdefault(key, value)
    return controls
