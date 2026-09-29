from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

import sdr_bridge.manager as manager_module
from sdr_bridge.manager import RadioBusyError, RadioManager
from sdr_bridge.models import Radio


class FakeLibrary:
    def enumerate(self) -> list[Radio]:
        return [
            Radio(
                index=0,
                name="Generic RTL2832U OEM",
                manufacturer="Realtek",
                product="RTL2838UHIDIR",
                serial="00000001",
                serial_unique=True,
            )
        ]


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 1234
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(0.02)
        return (
            b"2026-09-29, 13:00:00, 900000000, 900100000, 25000, 1000, -51.0, -50.0, -20.0, -49.0\n",
            b"",
        )

    async def wait(self) -> int:
        return self.returncode

    def send_signal(self, _signal: int) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_scan_job_locks_radio_and_emits_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []

    async def broadcast(payload: dict) -> None:
        events.append(payload)

    monkeypatch.setattr(manager_module, "RtlSdrLibrary", lambda: FakeLibrary())

    captured_cmd: list[str] = []

    async def fake_subprocess(*cmd: str, **_kwargs):
        captured_cmd.extend(cmd)
        return FakeProcess()

    monkeypatch.setattr(manager_module.asyncio, "create_subprocess_exec", fake_subprocess)

    manager = RadioManager(broadcast)
    await manager.refresh_radios(broadcast=False)
    radio_id = "serial:00000001"

    job = await manager.start_scan(
        radio_id,
        {
            "start_frequency_hz": 900_000_000,
            "end_frequency_hz": 930_000_000,
            "bin_width_hz": 25_000,
            "integration_seconds": 1,
            "ppm": 2,
            "threshold_db_above_noise": 10,
        },
    )

    with pytest.raises(RadioBusyError):
        await manager.start_scan(
            radio_id,
            {
                "start_frequency_hz": 433_000_000,
                "end_frequency_hz": 434_000_000,
            },
        )

    task = manager._tasks[radio_id]
    await task

    assert job.id
    assert "rtl_power" in captured_cmd[0]
    assert "00000001" in captured_cmd
    assert "900000000:930000000:25000" in captured_cmd
    completed = [event for event in events if event.get("type") == "scan_complete"]
    assert len(completed) == 1
    result = completed[0]["result"]
    assert result["peak_frequency_hz"] == 900_062_500
    assert result["detection_count"] == 1
    assert "bins" not in result
    latest = manager.scan_history(radio_id)[-1]
    assert len(latest["bins"]) == 4
    assert latest["peak_power_db"] == -20.0
