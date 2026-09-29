from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

from sdr_bridge.rtl import normalize_rtl433_packet, parse_rtl_power_csv


def test_parse_rtl_power_csv() -> None:
    sample = "2026-09-29, 13:00:00, 900000000, 900100000, 25000, 1000, -51.0, -50.0, -20.0, -49.0\n"
    parsed = parse_rtl_power_csv(sample, threshold_db_above_noise=10)
    assert parsed["bin_count"] == 4
    assert parsed["peak_power_db"] == -20.0
    assert parsed["peak_frequency_hz"] == 900062500
    assert len(parsed["detections"]) == 1


def test_normalize_rtl433_frequency_mhz() -> None:
    packet = normalize_rtl433_packet({"model": "Test", "freq": 433.92, "rssi": -3.0})
    assert packet["frequency_hz"] == 433_920_000
    assert packet["frequency_mhz"] == 433.92
    assert packet["model"] == "Test"
