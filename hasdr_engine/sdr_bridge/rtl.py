"""Native RTL-SDR enumeration and rtl_power parsing."""

from __future__ import annotations

import csv
import ctypes
import ctypes.util
import math
import os
import statistics
from contextlib import suppress
from pathlib import Path
from typing import Any

from .models import Radio


class RtlSdrLibraryError(RuntimeError):
    """librtlsdr could not be loaded."""


class RtlSdrLibrary:
    """Small ctypes wrapper around librtlsdr enumeration functions."""

    def __init__(self) -> None:
        runtime_root = os.environ.get("HASDR_RUNTIME_ROOT")
        path: str
        if runtime_root:
            lib_dir = Path(runtime_root) / "usr" / "lib"
            libusb = lib_dir / "libusb-1.0.so.0"
            if libusb.exists():
                with suppress(OSError):
                    ctypes.CDLL(str(libusb), mode=ctypes.RTLD_GLOBAL)
            runtime_library = lib_dir / "librtlsdr.so.0"
            path = str(runtime_library) if runtime_library.exists() else (
                ctypes.util.find_library("rtlsdr") or "librtlsdr.so.0"
            )
        else:
            path = ctypes.util.find_library("rtlsdr") or "librtlsdr.so.0"

        try:
            self._lib = ctypes.CDLL(path)
        except OSError as err:
            raise RtlSdrLibraryError(f"Unable to load librtlsdr: {err}") from err

        self._lib.rtlsdr_get_device_count.argtypes = []
        self._lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
        self._lib.rtlsdr_get_device_name.argtypes = [ctypes.c_uint32]
        self._lib.rtlsdr_get_device_name.restype = ctypes.c_char_p
        self._lib.rtlsdr_get_device_usb_strings.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self._lib.rtlsdr_get_device_usb_strings.restype = ctypes.c_int

    def enumerate(self) -> list[Radio]:
        count = int(self._lib.rtlsdr_get_device_count())
        rows: list[dict[str, Any]] = []
        serial_counts: dict[str, int] = {}
        for index in range(count):
            manufacturer = ctypes.create_string_buffer(256)
            product = ctypes.create_string_buffer(256)
            serial = ctypes.create_string_buffer(256)
            rc = self._lib.rtlsdr_get_device_usb_strings(index, manufacturer, product, serial)
            if rc != 0:
                manufact_s = ""
                product_s = ""
                serial_s = ""
            else:
                manufact_s = manufacturer.value.decode("utf-8", "replace")
                product_s = product.value.decode("utf-8", "replace")
                serial_s = serial.value.decode("utf-8", "replace")
            name_raw = self._lib.rtlsdr_get_device_name(index)
            name = name_raw.decode("utf-8", "replace") if name_raw else "RTL-SDR"
            rows.append({
                "index": index,
                "name": name,
                "manufacturer": manufact_s,
                "product": product_s,
                "serial": serial_s,
            })
            if serial_s:
                serial_counts[serial_s] = serial_counts.get(serial_s, 0) + 1

        return [
            Radio(
                **row,
                serial_unique=bool(row["serial"]) and serial_counts.get(row["serial"], 0) == 1,
            )
            for row in rows
        ]


def parse_rtl_power_csv(
    text: str,
    threshold_db_above_noise: float = 10.0,
    max_bins: int = 100_000,
) -> dict[str, Any]:
    """Parse one rtl_power single-shot output into spectrum data."""
    bins: list[dict[str, float]] = []
    sample_count = 0
    sweep_started: str | None = None

    for row in csv.reader(line for line in text.splitlines() if line.strip()):
        if len(row) < 7:
            continue
        try:
            low_hz = float(row[2].strip())
            step_hz = float(row[4].strip())
            sample_count += int(float(row[5].strip()))
            powers = [float(value.strip()) for value in row[6:] if value.strip()]
        except ValueError:
            continue

        if sweep_started is None:
            sweep_started = f"{row[0].strip()}T{row[1].strip()}"

        for index, power in enumerate(powers):
            if not math.isfinite(power):
                continue
            if len(bins) >= max_bins:
                raise ValueError(f"Spectrum exceeds safety limit of {max_bins} bins")
            frequency_hz = low_hz + (step_hz * (index + 0.5))
            bins.append({"frequency_hz": frequency_hz, "power_db": power})

    if not bins:
        raise ValueError("rtl_power produced no parseable spectrum bins")

    powers_only = [item["power_db"] for item in bins]
    noise_floor = float(statistics.median(powers_only))
    peak = max(bins, key=lambda item: item["power_db"])
    threshold = noise_floor + float(threshold_db_above_noise)
    crossings = [item for item in bins if item["power_db"] >= threshold]
    top_detections = sorted(crossings, key=lambda item: item["power_db"], reverse=True)[:25]

    return {
        "started": sweep_started,
        "bin_count": len(bins),
        "samples": sample_count,
        "noise_floor_db": round(noise_floor, 3),
        "peak_frequency_hz": round(peak["frequency_hz"]),
        "peak_power_db": round(peak["power_db"], 3),
        "detection_threshold_db": round(threshold, 3),
        "detection_count": len(crossings),
        "top_detections": top_detections,
        "detections": crossings,
        "bins": bins,
    }


def normalize_rtl433_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Preserve decoder output and add normalized frequency fields."""
    normalized = dict(packet)
    freq = packet.get("freq")
    if isinstance(freq, (int, float)):
        if abs(float(freq)) < 100_000:
            normalized["frequency_mhz"] = float(freq)
            normalized["frequency_hz"] = round(float(freq) * 1_000_000)
        else:
            normalized["frequency_hz"] = round(float(freq))
            normalized["frequency_mhz"] = float(freq) / 1_000_000
    return normalized
