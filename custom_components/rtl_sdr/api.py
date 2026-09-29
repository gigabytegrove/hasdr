"""HTTP/WebSocket client for remote or Supervisor-managed HASDR engines."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientWebSocketResponse, WSMsgType

_LOGGER = logging.getLogger(__name__)


class RtlSdrApiError(Exception):
    """Base API error."""


class RtlSdrApiAuthError(RtlSdrApiError):
    """Authentication failed."""


class RtlSdrApiConnectionError(RtlSdrApiError):
    """Engine connection failed."""


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class RtlSdrApiClient:
    """Client for one network HASDR engine."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        token: str,
        use_ssl: bool = False,
    ) -> None:
        self._session = session
        self.host = host.strip().strip("[]")
        self.port = int(port)
        self.token = token
        self.use_ssl = bool(use_ssl)
        scheme = "https" if self.use_ssl else "http"
        ws_scheme = "wss" if self.use_ssl else "ws"
        host_fmt = f"[{self.host}]" if ":" in self.host else self.host
        self.base_url = f"{scheme}://{host_fmt}:{self.port}"
        self.ws_url = f"{ws_scheme}://{host_fmt}:{self.port}/v1/ws"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                timeout=20,
                **kwargs,
            ) as response:
                if response.status in (401, 403):
                    raise RtlSdrApiAuthError("HASDR Engine rejected the API token")
                if response.status >= 400:
                    try:
                        error_payload = await response.json(content_type=None)
                    except Exception:
                        error_payload = {}
                    detail = error_payload.get("detail") or error_payload.get("error") or response.reason
                    raise RtlSdrApiError(f"HASDR Engine returned HTTP {response.status}: {detail}")
                if response.status == 204:
                    return None
                return await response.json()
        except RtlSdrApiAuthError:
            raise
        except ClientResponseError as err:
            raise RtlSdrApiError(f"HASDR Engine returned HTTP {err.status}: {err.message}") from err
        except (TimeoutError, ClientError) as err:
            raise RtlSdrApiConnectionError(str(err)) from err

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/health")

    async def radios(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/radios")

    async def jobs(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/jobs")

    async def scan_history(self, radio_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/radios/{radio_id}/history")

    async def latest_scan(self, radio_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/radios/{radio_id}/scan/latest")

    async def refresh_radios(self) -> dict[str, Any]:
        return await self._request("POST", "/v1/radios/refresh")

    async def start_scan(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/v1/radios/{radio_id}/scan", json=payload)

    async def start_monitor(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/v1/radios/{radio_id}/monitor", json=payload)

    async def start_decoder(self, radio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/v1/radios/{radio_id}/decode", json=payload)

    async def stop(self, radio_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v1/radios/{radio_id}/stop")

    async def event_loop(self, callback: EventCallback, stop_event: asyncio.Event) -> None:
        """Maintain the push connection until stopped."""
        backoff = 1
        while not stop_event.is_set():
            websocket: ClientWebSocketResponse | None = None
            try:
                websocket = await self._session.ws_connect(
                    self.ws_url,
                    headers=self._headers,
                    heartbeat=30,
                    receive_timeout=90,
                    timeout=20,
                )
                backoff = 1
                async for message in websocket:
                    if stop_event.is_set():
                        break
                    if message.type == WSMsgType.TEXT:
                        try:
                            payload = json.loads(message.data)
                        except json.JSONDecodeError:
                            _LOGGER.debug("Ignoring invalid HASDR WebSocket JSON: %s", message.data)
                            continue
                        if isinstance(payload, dict):
                            await callback(payload)
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                raise
            except (TimeoutError, ClientError) as err:
                _LOGGER.debug("HASDR WebSocket disconnected: %s", err)
            finally:
                if websocket is not None and not websocket.closed:
                    await websocket.close()

            if stop_event.is_set():
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            backoff = min(backoff * 2, 30)
