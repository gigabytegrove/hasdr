"""Data models and validation for the SDR bridge."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class ValidationError(ValueError):
    """Invalid API request."""


@dataclass(slots=True)
class Radio:
    index: int
    name: str
    manufacturer: str
    product: str
    serial: str
    serial_unique: bool = True
    present: bool = True

    @property
    def id(self) -> str:
        return f"serial:{self.serial}" if self.serial and self.serial_unique else f"index:{self.index}"

    @property
    def rtl_power_selector(self) -> str:
        return self.serial if self.serial and self.serial_unique else str(self.index)

    @property
    def rtl_433_selector(self) -> str:
        return f":{self.serial}" if self.serial and self.serial_unique else str(self.index)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["id"] = self.id
        return result


@dataclass(slots=True)
class Job:
    id: str
    radio_id: str
    mode: str
    started_at: float
    parameters: dict[str, Any]
    pid: int | None = None
    packets: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_int(
    payload: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int | None = None,
    default: int | None = None,
) -> int:
    value = payload.get(key, default)
    if value is None:
        raise ValidationError(f"{key} is required")
    if isinstance(value, bool):
        raise ValidationError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as err:
        raise ValidationError(f"{key} must be an integer") from err
    if parsed < minimum or (maximum is not None and parsed > maximum):
        limit = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise ValidationError(f"{key} must be {limit}")
    return parsed


def optional_float(
    payload: dict[str, Any],
    key: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{key} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as err:
        raise ValidationError(f"{key} must be numeric") from err
    if minimum is not None and parsed < minimum:
        raise ValidationError(f"{key} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{key} must be <= {maximum}")
    return parsed
