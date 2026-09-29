"""Persistent native runtime bootstrap for Home Assistant Container."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from .runtime.rtl import RtlSdrLibrary, RtlSdrLibraryError

_RUNTIME_LOCK = asyncio.Lock()
_RUNTIME_FORMAT_VERSION = 1
_PACKAGES = ("rtl-sdr", "rtl_433")


class RuntimeBootstrapError(RuntimeError):
    """HASDR could not prepare its local native runtime."""


def local_usb_access_available() -> bool:
    """Return whether libusb can see the Docker USB bus namespace."""
    return Path("/dev/bus/usb").is_dir()


def _commands_available() -> bool:
    return all(shutil.which(command) for command in ("rtl_power", "rtl_433"))


def _library_available() -> bool:
    try:
        RtlSdrLibrary()
    except RtlSdrLibraryError:
        return False
    return True


def _activate_runtime(root: Path) -> None:
    os.environ["HASDR_RUNTIME_ROOT"] = str(root)
    binary_dir = str(root / "usr" / "bin")
    path_parts = os.environ.get("PATH", "").split(":")
    if binary_dir not in path_parts:
        os.environ["PATH"] = ":".join([binary_dir, *path_parts])


def _runtime_layout_valid(root: Path) -> bool:
    return all(
        path.is_file()
        for path in (
            root / "usr" / "bin" / "rtl_power",
            root / "usr" / "bin" / "rtl_433",
            root / "usr" / "lib" / "librtlsdr.so.0",
        )
    )


def _copy_apk_configuration(target: Path) -> None:
    source_repositories = Path("/etc/apk/repositories")
    source_keys = Path("/etc/apk/keys")
    if not source_repositories.is_file() or not source_keys.is_dir():
        raise RuntimeBootstrapError("The Home Assistant container does not expose Alpine APK trust configuration")

    apk_dir = target / "etc" / "apk"
    keys_dir = apk_dir / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)

    repositories = [
        line.strip()
        for line in source_repositories.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    additions: list[str] = []
    for repository in repositories:
        normalized = repository.rstrip("/")
        if normalized.endswith("/main"):
            community = normalized[:-4] + "community"
            if community not in repositories and community not in additions:
                additions.append(community)
    repositories.extend(additions)
    (apk_dir / "repositories").write_text("\n".join(repositories) + "\n")

    for key in source_keys.iterdir():
        if key.is_file():
            shutil.copy2(key, keys_dir / key.name)


async def _run_apk_install(target: Path) -> None:
    apk = shutil.which("apk")
    if apk is None:
        raise RuntimeBootstrapError("Alpine APK is not available in this Home Assistant container")

    process = await asyncio.create_subprocess_exec(
        apk,
        "--root",
        str(target),
        "--no-cache",
        "--no-progress",
        "add",
        "--initdb",
        "--no-scripts",
        *_PACKAGES,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip()
        if not detail:
            detail = stdout.decode("utf-8", "replace").strip()
        raise RuntimeBootstrapError(
            f"Unable to install the signed RTL-SDR runtime with APK: {detail[-4000:]}"
        )


async def async_prepare_local_runtime(config_dir: str) -> dict[str, Any]:
    """Ensure a working local RTL-SDR runtime without modifying the HA image."""
    async with _RUNTIME_LOCK:
        if _commands_available() and _library_available():
            return {"source": "system", "runtime_root": None}

        alpine_release_path = Path("/etc/alpine-release")
        if shutil.which("apk") is None or not alpine_release_path.is_file():
            raise RuntimeBootstrapError(
                "Local automatic runtime installation requires the Alpine-based Home Assistant Container image"
            )

        release = alpine_release_path.read_text().strip()
        arch = platform.machine()
        root = Path(config_dir) / ".hasdr" / "runtime"
        marker = root / ".hasdr-runtime.json"

        if marker.is_file() and _runtime_layout_valid(root):
            try:
                metadata = json.loads(marker.read_text())
            except (OSError, json.JSONDecodeError):
                metadata = {}
            if (
                metadata.get("version") == _RUNTIME_FORMAT_VERSION
                and metadata.get("alpine_release") == release
                and metadata.get("arch") == arch
            ):
                _activate_runtime(root)
                if _commands_available() and _library_available():
                    return {
                        "source": "managed-local",
                        "runtime_root": str(root),
                        "alpine_release": release,
                        "arch": arch,
                    }

        parent = root.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / "runtime.installing"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)

        try:
            _copy_apk_configuration(temporary)
            await _run_apk_install(temporary)

            metadata = {
                "version": _RUNTIME_FORMAT_VERSION,
                "alpine_release": release,
                "arch": arch,
                "packages": list(_PACKAGES),
            }
            (temporary / ".hasdr-runtime.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n"
            )

            if not _runtime_layout_valid(temporary):
                raise RuntimeBootstrapError(
                    "APK completed but the RTL-SDR runtime files are incomplete"
                )

            if root.exists():
                shutil.rmtree(root)
            os.replace(temporary, root)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            raise

        _activate_runtime(root)
        if not _commands_available() or not _library_available():
            raise RuntimeBootstrapError(
                "The private RTL-SDR runtime was installed but could not be loaded"
            )

        return {
            "source": "managed-local",
            "runtime_root": str(root),
            "alpine_release": release,
            "arch": arch,
        }
