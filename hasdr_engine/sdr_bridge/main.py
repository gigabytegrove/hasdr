"""HTTP/WebSocket service for local RTL-SDR receivers."""

from __future__ import annotations

import hmac
import logging
import os
import socket
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from .manager import RadioBusyError, RadioManager, RadioNotFoundError
from .models import ValidationError
from .rtl import RtlSdrLibraryError

VERSION = "0.2.1"
_LOGGER = logging.getLogger(__name__)


class BridgeApp:
    """Own API state and WebSocket clients."""

    def __init__(self) -> None:
        self.token = os.environ.get("HASDR_API_TOKEN") or os.environ.get("SDR_BRIDGE_TOKEN", "")
        if len(self.token) < 16:
            raise RuntimeError("HASDR_API_TOKEN must be set to at least 16 characters")
        self.instance_id = os.environ.get(
            "HASDR_INSTANCE_ID",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"rtl-sdr-bridge:{self.token}")),
        )
        self.name = os.environ.get("HASDR_NAME") or os.environ.get(
            "SDR_BRIDGE_NAME",
            f"HASDR SDR Engine ({socket.gethostname()})",
        )
        self.sockets: set[web.WebSocketResponse] = set()
        self.manager = RadioManager(self.broadcast)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[web.WebSocketResponse] = []
        for ws in tuple(self.sockets):
            try:
                await ws.send_json(payload)
            except (ConnectionError, RuntimeError):
                stale.append(ws)
        for ws in stale:
            self.sockets.discard(ws)

    @web.middleware
    async def auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(supplied, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "version": VERSION,
            "instance_id": self.instance_id,
            "name": self.name,
            "radios": len(self.manager.radios),
            "capabilities": [
                "multi_sdr",
                "rtl_power_scan",
                "fixed_frequency_monitor",
                "rtl_433_decode",
                "websocket_events",
            ],
        })

    async def radios(self, request: web.Request) -> web.Response:
        return web.json_response({"radios": [radio.to_dict() for radio in self.manager.radios.values()]})

    async def refresh(self, request: web.Request) -> web.Response:
        radios = await self.manager.refresh_radios(broadcast=True)
        return web.json_response({"radios": [radio.to_dict() for radio in radios]})

    async def jobs(self, request: web.Request) -> web.Response:
        return web.json_response({"jobs": self.manager.list_jobs()})

    async def history(self, request: web.Request) -> web.Response:
        radio_id = request.match_info["radio_id"]
        self.manager.get_radio(radio_id)
        return web.json_response({"history": self.manager.scan_history(radio_id)})

    async def latest_scan(self, request: web.Request) -> web.Response:
        radio_id = request.match_info["radio_id"]
        self.manager.get_radio(radio_id)
        history = self.manager.scan_history(radio_id)
        return web.json_response({"scan": history[-1] if history else None})

    async def scan(self, request: web.Request) -> web.Response:
        payload = await request.json()
        job = await self.manager.start_scan(request.match_info["radio_id"], payload)
        return web.json_response({"job": job.to_dict()}, status=202)

    async def monitor(self, request: web.Request) -> web.Response:
        payload = await request.json()
        job = await self.manager.start_monitor(
            request.match_info["radio_id"],
            payload,
        )
        return web.json_response({"job": job.to_dict()}, status=202)

    async def decode(self, request: web.Request) -> web.Response:
        payload = await request.json()
        job = await self.manager.start_decoder(request.match_info["radio_id"], payload)
        return web.json_response({"job": job.to_dict()}, status=202)

    async def stop(self, request: web.Request) -> web.Response:
        stopped = await self.manager.stop_job(request.match_info["radio_id"])
        return web.json_response({"stopped": stopped})

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, receive_timeout=120)
        await ws.prepare(request)
        self.sockets.add(ws)
        await ws.send_json({"type": "hello", "version": VERSION, "instance_id": self.instance_id})
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "ping":
                    await ws.send_str("pong")
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.sockets.discard(ws)
        return ws

    @web.middleware
    async def errors(self, request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            return await handler(request)
        except RadioNotFoundError:
            return web.json_response({"error": "radio_not_found"}, status=404)
        except RadioBusyError as err:
            return web.json_response({"error": "radio_busy", "detail": str(err)}, status=409)
        except (ValidationError, ValueError) as err:
            return web.json_response({"error": "invalid_request", "detail": str(err)}, status=400)
        except web.HTTPException:
            raise
        except Exception:
            _LOGGER.exception("Unhandled bridge request error")
            return web.json_response({"error": "internal_error"}, status=500)

    def create(self) -> web.Application:
        app = web.Application(middlewares=[self.errors, self.auth])
        app.router.add_get("/v1/health", self.health)
        app.router.add_get("/v1/radios", self.radios)
        app.router.add_post("/v1/radios/refresh", self.refresh)
        app.router.add_get("/v1/jobs", self.jobs)
        app.router.add_get("/v1/radios/{radio_id}/history", self.history)
        app.router.add_get("/v1/radios/{radio_id}/scan/latest", self.latest_scan)
        app.router.add_post("/v1/radios/{radio_id}/scan", self.scan)
        app.router.add_post("/v1/radios/{radio_id}/monitor", self.monitor)
        app.router.add_post("/v1/radios/{radio_id}/decode", self.decode)
        app.router.add_post("/v1/radios/{radio_id}/stop", self.stop)
        app.router.add_get("/v1/ws", self.websocket)

        async def startup(_: web.Application) -> None:
            await self.manager.start()

        async def cleanup(_: web.Application) -> None:
            await self.manager.close()

        app.on_startup.append(startup)
        app.on_cleanup.append(cleanup)
        return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        bridge = BridgeApp()
    except (RuntimeError, RtlSdrLibraryError) as err:
        raise SystemExit(str(err)) from err
    web.run_app(
        bridge.create(),
        host=os.environ.get("HASDR_LISTEN")
        or os.environ.get("SDR_BRIDGE_LISTEN", "0.0.0.0"),
        port=int(
            os.environ.get("HASDR_PORT")
            or os.environ.get("SDR_BRIDGE_PORT", "8099")
        ),
    )


if __name__ == "__main__":
    main()
