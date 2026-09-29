"""Embedded local backend for Home Assistant Container installs."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from typing import Any

from .runtime.manager import RadioManager
from .runtime.rtl import RtlSdrLibrary, RtlSdrLibraryError

VERSION = "0.2.1"


class LocalRuntimeUnavailable(RuntimeError):
    """Required local SDR runtime is unavailable."""


class LocalRtlSdrClient:
    """Run the HASDR engine directly inside Home Assistant."""

    def __init__(self, runtime_info: dict[str, Any] | None = None) -> None:
        missing = [command for command in ("rtl_power", "rtl_433") if shutil.which(command) is None]
        if missing:
            raise LocalRuntimeUnavailable(
                "Missing local SDR runtime command(s): " + ", ".join(missing)
            )
        try:
            RtlSdrLibrary()
        except RtlSdrLibraryError as err:
            raise LocalRuntimeUnavailable(str(err)) from err

        self._runtime_info = runtime_info or {"source": "system"}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._manager = RadioManager(self._broadcast)
        self._started = False

    async def async_start(self) -> None:
        if self._started:
            return
        await self._manager.start()
        self._started = True

    async def async_close(self) -> None:
        if not self._started:
            return
        await self._manager.close()
        self._started = False

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(payload)

    async def health(self) -> dict[str, Any]:
        await self.async_start()
        return {
            "ok": True,
            "version": VERSION,
            "instance_id": "local",
            "name": "Local RTL-SDR",
            "radios": len(self._manager.radios),
            "runtime": self._runtime_info,
            "capabilities": [
                "embedded_runtime",
                "multi_sdr",
                "rtl_power_scan",
                "fixed_frequency_monitor",
                "rtl_433_decode",
            ],
        }

    async def radios(self) -> dict[str, Any]:
        await self.async_start()
        return {"radios": [radio.to_dict() for radio in self._manager.radios.values()]}

    async def jobs(self) -> dict[str, Any]:
        await self.async_start()
        return {"jobs": self._manager.list_jobs()}

    async def scan_history(self, radio_id: str) -> dict[str, Any]:
        await self.async_start()
        self._manager.get_radio(radio_id)
        return {"history": self._manager.scan_history(radio_id)}

    async def latest_scan(self, radio_id: str) -> dict[str, Any]:
        await self.async_start()
        self._manager.get_radio(radio_id)
        history = self._manager.scan_history(radio_id)
        return {"scan": history[-1] if history else None}

    async def refresh_radios(self) -> dict[str, Any]:
        await self.async_start()
        radios = await self._manager.refresh_radios(broadcast=True)
        return {"radios": [radio.to_dict() for radio in radios]}

    async def start_scan(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.async_start()
        job = await self._manager.start_scan(radio_id, payload)
        return {"job": job.to_dict()}

    async def start_monitor(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.async_start()
        job = await self._manager.start_monitor(radio_id, payload)
        return {"job": job.to_dict()}

    async def start_decoder(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.async_start()
        job = await self._manager.start_decoder(radio_id, payload)
        return {"job": job.to_dict()}

    async def stop(self, radio_id: str) -> dict[str, Any]:
        await self.async_start()
        return {"stopped": await self._manager.stop_job(radio_id)}

    async def event_loop(
        self,
        callback,
        stop_event: asyncio.Event,
    ) -> None:
        await self.async_start()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            while not stop_event.is_set():
                get_task = asyncio.create_task(queue.get())
                stop_task = asyncio.create_task(stop_event.wait())
                done, pending = await asyncio.wait(
                    {get_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done and stop_event.is_set():
                    break
                if get_task in done:
                    await callback(get_task.result())
        finally:
            self._subscribers.discard(queue)
