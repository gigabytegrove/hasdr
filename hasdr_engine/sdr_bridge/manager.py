"""Receiver/job manager for the RTL-SDR bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from .models import Job, Radio, ValidationError, optional_float, require_int
from .rtl import RtlSdrLibrary, normalize_rtl433_packet, parse_rtl_power_csv

_LOGGER = logging.getLogger(__name__)


def _runtime_command(name: str) -> str:
    runtime_root = os.environ.get("HASDR_RUNTIME_ROOT")
    if runtime_root:
        candidate = Path(runtime_root) / "usr" / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return name


def _runtime_env() -> dict[str, str] | None:
    runtime_root = os.environ.get("HASDR_RUNTIME_ROOT")
    if not runtime_root:
        return None

    env = os.environ.copy()
    library_paths = [
        str(Path(runtime_root) / "lib"),
        str(Path(runtime_root) / "usr" / "lib"),
    ]
    if env.get("LD_LIBRARY_PATH"):
        library_paths.append(env["LD_LIBRARY_PATH"])
    env["LD_LIBRARY_PATH"] = ":".join(library_paths)
    return env


class RadioNotFoundError(KeyError):
    """Requested radio is absent."""


class RadioBusyError(RuntimeError):
    """Requested radio already has an active job."""


class RadioManager:
    """Manage hardware enumeration, job locks and subprocesses."""

    def __init__(self, broadcaster: Any) -> None:
        self._rtl = RtlSdrLibrary()
        self._broadcast = broadcaster
        self.radios: dict[str, Radio] = {}
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._scan_history: dict[str, deque[dict[str, Any]]] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self.refresh_radios(broadcast=False)
        self._poll_task = asyncio.create_task(self._radio_poll_loop(), name="rtl_sdr_radio_poll")

    async def close(self) -> None:
        self._stop.set()
        if self._poll_task:
            self._poll_task.cancel()
        for radio_id in list(self.jobs):
            await self.stop_job(radio_id)
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _radio_poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except TimeoutError:
                try:
                    await self.refresh_radios(broadcast=True)
                except Exception:
                    _LOGGER.exception("Failed to refresh RTL-SDR radios")

    async def refresh_radios(self, broadcast: bool = True) -> list[Radio]:
        listed = await asyncio.to_thread(self._rtl.enumerate)
        current_signature = [(r.id, r.index, r.serial, r.product) for r in self.radios.values()]
        new_signature = [(r.id, r.index, r.serial, r.product) for r in listed]
        self.radios = {radio.id: radio for radio in listed}
        if broadcast and current_signature != new_signature:
            await self._broadcast({"type": "radios_changed", "radios": [r.to_dict() for r in listed]})
        return listed

    def get_radio(self, radio_id: str) -> Radio:
        radio = self.radios.get(radio_id)
        if radio is None:
            raise RadioNotFoundError(radio_id)
        return radio

    def list_jobs(self) -> list[dict[str, Any]]:
        return [job.to_dict() for job in self.jobs.values()]

    def scan_history(self, radio_id: str) -> list[dict[str, Any]]:
        return list(self._scan_history.get(radio_id, ()))

    def _claim(self, radio: Radio, mode: str, parameters: dict[str, Any]) -> Job:
        if radio.id in self.jobs:
            raise RadioBusyError(f"{radio.id} is already busy")
        job = Job(
            id=str(uuid.uuid4()),
            radio_id=radio.id,
            mode=mode,
            started_at=time.time(),
            parameters=parameters,
        )
        self.jobs[radio.id] = job
        return job

    async def start_scan(self, radio_id: str, payload: dict[str, Any]) -> Job:
        radio = self.get_radio(radio_id)
        start_hz = require_int(payload, "start_frequency_hz", 1)
        end_hz = require_int(payload, "end_frequency_hz", 2)
        if end_hz <= start_hz:
            raise ValidationError("end_frequency_hz must be greater than start_frequency_hz")
        if end_hz - start_hz > 3_000_000_000:
            raise ValidationError("scan span is limited to 3 GHz per job")
        bin_hz = require_int(payload, "bin_width_hz", 1, 2_800_000, 25_000)
        integration = require_int(payload, "integration_seconds", 1, 3600, 1)
        ppm = require_int(payload, "ppm", -1000, 1000, 0)
        threshold = optional_float(payload, "threshold_db_above_noise", 0, 100)
        if threshold is None:
            threshold = 10.0
        gain = optional_float(payload, "gain_db", 0, 100)
        bias_tee = bool(payload.get("bias_tee", False))

        params = {
            "start_frequency_hz": start_hz,
            "end_frequency_hz": end_hz,
            "bin_width_hz": bin_hz,
            "integration_seconds": integration,
            "ppm": ppm,
            "gain_db": gain,
            "bias_tee": bias_tee,
            "threshold_db_above_noise": threshold,
        }
        job = self._claim(radio, "scan", params)
        task = asyncio.create_task(self._run_scan(radio, job), name=f"scan_{job.id}")
        self._tasks[radio.id] = task
        await self._broadcast({"type": "job_started", "radio_id": radio.id, "job": job.to_dict()})
        return job

    async def _run_scan(self, radio: Radio, job: Job) -> None:
        p = job.parameters
        cmd = [
            _runtime_command("rtl_power"),
            "-d",
            radio.rtl_power_selector,
            "-f",
            f"{p['start_frequency_hz']}:{p['end_frequency_hz']}:{p['bin_width_hz']}",
            "-i",
            str(p["integration_seconds"]),
            "-p",
            str(p["ppm"]),
            "-1",
        ]
        if p["gain_db"] is not None:
            cmd += ["-g", str(p["gain_db"])]
        if p["bias_tee"]:
            cmd += ["-T"]
        cmd += ["-"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_runtime_env(),
            )
            self._processes[radio.id] = proc
            job.pid = proc.pid
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                detail = stderr.decode("utf-8", "replace").strip()[-4000:]
                raise RuntimeError(f"rtl_power exited {proc.returncode}: {detail}")
            result = parse_rtl_power_csv(
                stdout.decode("utf-8", "replace"),
                p["threshold_db_above_noise"],
            )
            result.update(
                {
                    "radio_id": radio.id,
                    "job_id": job.id,
                    "start_frequency_hz": p["start_frequency_hz"],
                    "end_frequency_hz": p["end_frequency_hz"],
                    "bin_width_hz_requested": p["bin_width_hz"],
                    "completed_at": time.time(),
                }
            )
            self._scan_history.setdefault(radio.id, deque(maxlen=5)).append(result)
            summary = {key: value for key, value in result.items() if key not in {"bins", "detections"}}
            await self._broadcast(
                {
                    "type": "scan_complete",
                    "radio_id": radio.id,
                    "job_id": job.id,
                    "result": summary,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            job.error = str(err)
            await self._broadcast(
                {
                    "type": "job_error",
                    "radio_id": radio.id,
                    "job_id": job.id,
                    "error": str(err),
                }
            )
        finally:
            self._processes.pop(radio.id, None)
            self.jobs.pop(radio.id, None)
            self._tasks.pop(radio.id, None)
            await self._broadcast({"type": "job_finished", "radio_id": radio.id, "job_id": job.id})

    async def start_decoder(self, radio_id: str, payload: dict[str, Any]) -> Job:
        radio = self.get_radio(radio_id)
        frequencies_raw = payload.get("frequencies_hz")
        if not isinstance(frequencies_raw, list) or not frequencies_raw:
            raise ValidationError("frequencies_hz must be a non-empty list")
        if len(frequencies_raw) > 128:
            raise ValidationError("frequencies_hz is limited to 128 entries")

        frequencies: list[int] = []
        for item in frequencies_raw:
            if isinstance(item, bool):
                raise ValidationError("frequencies_hz entries must be integers")
            try:
                value = int(item)
            except (TypeError, ValueError) as err:
                raise ValidationError("frequencies_hz entries must be integers") from err
            if value < 1:
                raise ValidationError("frequencies_hz entries must be positive")
            frequencies.append(value)

        sample_rate = require_int(payload, "sample_rate_hz", 10_000, 3_200_000, 250_000)
        hop_seconds = require_int(payload, "hop_seconds", 1, 3600, 15)
        ppm = require_int(payload, "ppm", -1000, 1000, 0)
        gain = optional_float(payload, "gain_db", 0, 100)
        bias_tee = bool(payload.get("bias_tee", False))
        protocols_raw = payload.get("protocols") or []
        if not isinstance(protocols_raw, list):
            raise ValidationError("protocols must be a list")

        protocols: list[int] = []
        for item in protocols_raw:
            try:
                protocol = int(item)
            except (TypeError, ValueError) as err:
                raise ValidationError("protocols entries must be integers") from err
            if protocol <= 0:
                raise ValidationError("protocol numbers must be positive")
            protocols.append(protocol)

        params = {
            "frequencies_hz": frequencies,
            "sample_rate_hz": sample_rate,
            "hop_seconds": hop_seconds,
            "ppm": ppm,
            "gain_db": gain,
            "bias_tee": bias_tee,
            "protocols": protocols,
        }
        job = self._claim(radio, "decode", params)
        task = asyncio.create_task(self._run_decoder(radio, job), name=f"decode_{job.id}")
        self._tasks[radio.id] = task
        await self._broadcast({"type": "job_started", "radio_id": radio.id, "job": job.to_dict()})
        return job

    async def _run_decoder(self, radio: Radio, job: Job) -> None:
        p = job.parameters
        cmd = [
            _runtime_command("rtl_433"),
            "-d",
            radio.rtl_433_selector,
            "-F",
            "json",
            "-M",
            "time:unix:usec:utc",
            "-M",
            "protocol",
            "-M",
            "level",
            "-C",
            "native",
            "-p",
            str(p["ppm"]),
            "-s",
            str(p["sample_rate_hz"]),
        ]
        if p["gain_db"] is not None:
            cmd += ["-g", str(p["gain_db"])]
        if p["bias_tee"]:
            cmd += ["-t", "biastee=1"]
        for frequency in p["frequencies_hz"]:
            cmd += ["-f", str(frequency)]
        if len(p["frequencies_hz"]) > 1:
            cmd += ["-H", str(p["hop_seconds"])]
        for protocol in p["protocols"]:
            cmd += ["-R", str(protocol)]

        stderr_tail: deque[str] = deque(maxlen=50)
        stderr_task: asyncio.Task[None] | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_runtime_env(),
            )
            self._processes[radio.id] = proc
            job.pid = proc.pid

            async def consume_stderr() -> None:
                assert proc.stderr is not None
                while line := await proc.stderr.readline():
                    stderr_tail.append(line.decode("utf-8", "replace").rstrip())

            stderr_task = asyncio.create_task(consume_stderr(), name=f"decoder_stderr_{job.id}")
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(decoded, dict):
                    continue
                packet = normalize_rtl433_packet(decoded)
                job.packets += 1
                await self._broadcast(
                    {
                        "type": "decoded_packet",
                        "radio_id": radio.id,
                        "job_id": job.id,
                        "packet": packet,
                    }
                )

            return_code = await proc.wait()
            if return_code != 0:
                raise RuntimeError(f"rtl_433 exited {return_code}: {' | '.join(stderr_tail)[-4000:]}")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            job.error = str(err)
            await self._broadcast(
                {
                    "type": "job_error",
                    "radio_id": radio.id,
                    "job_id": job.id,
                    "error": str(err),
                }
            )
        finally:
            if stderr_task:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            self._processes.pop(radio.id, None)
            self.jobs.pop(radio.id, None)
            self._tasks.pop(radio.id, None)
            await self._broadcast({"type": "job_finished", "radio_id": radio.id, "job_id": job.id})

    async def stop_job(self, radio_id: str) -> bool:
        if radio_id not in self.jobs:
            return False
        proc = self._processes.get(radio_id)
        task = self._tasks.get(radio_id)
        job = self.jobs.get(radio_id)
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), timeout=5)
            except ProcessLookupError:
                pass
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._processes.pop(radio_id, None)
        self._tasks.pop(radio_id, None)
        self.jobs.pop(radio_id, None)
        await self._broadcast(
            {
                "type": "job_stopped",
                "radio_id": radio_id,
                "job_id": job.id if job else None,
            }
        )
        return True
