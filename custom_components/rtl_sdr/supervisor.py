"""Automatic HASDR Engine management for Home Assistant Supervisor."""

from __future__ import annotations

import os
import secrets
from typing import Any

from aiohttp import ClientError, ClientSession

REPOSITORY_URL = "https://github.com/gigabytegrove/hasdr"
ENGINE_NAME = "HASDR SDR Engine"
ENGINE_SLUG_SUFFIX = "hasdr_engine"


class SupervisorEngineError(RuntimeError):
    """Supervisor could not provision the HASDR engine."""


class HasdrSupervisorManager:
    """Provision and manage the HASDR companion App without user plumbing."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")

    @property
    def available(self) -> bool:
        return bool(self._token)

    @staticmethod
    def generate_api_token() -> str:
        return secrets.token_hex(32)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.available:
            raise SupervisorEngineError("Home Assistant Supervisor is not available")

        try:
            async with self._session.request(
                method,
                f"http://supervisor{path}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = {}

                if response.status >= 400:
                    message = body.get("message") if isinstance(body, dict) else None
                    raise SupervisorEngineError(
                        message or f"Supervisor returned HTTP {response.status}"
                    )

                if isinstance(body, dict) and body.get("result") == "error":
                    raise SupervisorEngineError(
                        str(body.get("message") or "Supervisor operation failed")
                    )

                if isinstance(body, dict) and "data" in body:
                    return body["data"]
                return body
        except SupervisorEngineError:
            raise
        except (ClientError, TimeoutError) as err:
            raise SupervisorEngineError(str(err)) from err

    @staticmethod
    def _items(payload: Any, key: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            values = payload.get(key, [])
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
        return []

    async def _ensure_repository(self) -> dict[str, Any]:
        repositories = self._items(
            await self._request("GET", "/store/repositories"),
            "repositories",
        )
        for repository in repositories:
            if repository.get("source") == REPOSITORY_URL:
                return repository

        await self._request(
            "POST",
            "/store/repositories",
            {"repository": REPOSITORY_URL},
        )

        repositories = self._items(
            await self._request("GET", "/store/repositories"),
            "repositories",
        )
        for repository in repositories:
            if repository.get("source") == REPOSITORY_URL:
                return repository

        raise SupervisorEngineError(
            "HASDR App repository was accepted but did not appear in the Supervisor store"
        )

    async def _find_engine(self, repository: dict[str, Any]) -> dict[str, Any]:
        apps = self._items(await self._request("GET", "/store/addons"), "addons")
        repository_slug = repository.get("slug")
        for app in apps:
            slug = str(app.get("slug", ""))
            if (
                app.get("name") == ENGINE_NAME
                or slug == ENGINE_SLUG_SUFFIX
                or slug.endswith(f"_{ENGINE_SLUG_SUFFIX}")
            ):
                app_repository = app.get("repository")
                if not repository_slug or app_repository in (repository_slug, REPOSITORY_URL):
                    return app

        raise SupervisorEngineError(
            "HASDR SDR Engine was not found after adding the HASDR App repository"
        )

    async def ensure_engine(self, api_token: str) -> tuple[str, str]:
        """Install, configure and start the managed SDR engine."""
        repository = await self._ensure_repository()
        app = await self._find_engine(repository)
        slug = str(app["slug"])

        installed = app.get("installed")
        if installed in (False, None, ""):
            await self._request("POST", f"/store/addons/{slug}/install", {})

        await self._request(
            "POST",
            f"/addons/{slug}/options",
            {
                "boot": "auto",
                "auto_update": True,
                "watchdog": True,
                "options": {"api_token": api_token},
            },
        )
        await self._request("POST", f"/addons/{slug}/start", {})

        info = await self._request("GET", f"/addons/{slug}/info")
        if not isinstance(info, dict):
            raise SupervisorEngineError("Supervisor returned invalid HASDR Engine information")

        hostname = info.get("hostname")
        if not hostname:
            hostname = slug.replace("_", "-")

        return slug, str(hostname)

    async def mark_system_managed(self, slug: str, entry_id: str) -> None:
        """Tell Supervisor this App is owned by this Home Assistant config entry."""
        await self._request(
            "POST",
            f"/addons/{slug}/sys_options",
            {
                "system_managed": True,
                "system_managed_config_entry": entry_id,
            },
        )

    async def engine_info(self, slug: str) -> dict[str, Any]:
        info = await self._request("GET", f"/addons/{slug}/info")
        if not isinstance(info, dict):
            raise SupervisorEngineError("Supervisor returned invalid HASDR Engine information")
        return info
