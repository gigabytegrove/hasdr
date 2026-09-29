from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

from sdr_bridge.models import Radio, ValidationError, optional_float, require_int


def test_radio_selectors_unique_serial() -> None:
    radio = Radio(
        index=1,
        name="RTL",
        manufacturer="Realtek",
        product="RTL2838",
        serial="00000002",
        serial_unique=True,
    )
    assert radio.id == "serial:00000002"
    assert radio.rtl_power_selector == "00000002"
    assert radio.rtl_433_selector == ":00000002"


def test_radio_selectors_duplicate_serial_fall_back_to_index() -> None:
    radio = Radio(
        index=1,
        name="RTL",
        manufacturer="Realtek",
        product="RTL2838",
        serial="00000001",
        serial_unique=False,
    )
    assert radio.id == "index:1"
    assert radio.rtl_power_selector == "1"
    assert radio.rtl_433_selector == "1"


def test_integer_validation() -> None:
    assert require_int({"x": "25"}, "x", 1, 100) == 25
    assert require_int({}, "x", 1, 100, 7) == 7
    with pytest.raises(ValidationError):
        require_int({"x": True}, "x", 1, 100)
    with pytest.raises(ValidationError):
        require_int({"x": 101}, "x", 1, 100)


def test_optional_float_validation() -> None:
    assert optional_float({}, "gain") is None
    assert optional_float({"gain": "12.5"}, "gain", 0, 50) == 12.5
    with pytest.raises(ValidationError):
        optional_float({"gain": -1}, "gain", 0, 50)
