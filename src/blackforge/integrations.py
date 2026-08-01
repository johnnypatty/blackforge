"""Native Linux integration artifacts and machine-readable status output."""

from __future__ import annotations

from pathlib import Path

from .backend import PacmanBackend
from .catalog import Catalog
from .sources import ArchToolCatalog
from .storage import atomic_write_text

SYSTEMD_SERVICE = """[Unit]
Description=Check BlackForge catalog updates
Documentation=https://johnnypatty.github.io/blackforge/wiki/

[Service]
Type=oneshot
ExecStart={executable} updates check
"""

SYSTEMD_TIMER = """[Unit]
Description=Weekly BlackForge catalog update check

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
"""


def write_systemd_units(
    directory: Path,
    *,
    executable: str = "/usr/bin/blackforge",
) -> tuple[Path, Path]:
    if not executable or any(character in executable for character in "\r\n\x00"):
        raise ValueError("Invalid BlackForge executable path")
    escaped = executable.replace("\\", "\\\\").replace('"', '\\"')
    systemd_executable = f'"{escaped}"' if any(value.isspace() for value in escaped) else escaped
    directory.mkdir(parents=True, exist_ok=True)
    service = directory / "blackforge-update.service"
    timer = directory / "blackforge-update.timer"
    atomic_write_text(service, SYSTEMD_SERVICE.format(executable=systemd_executable))
    atomic_write_text(timer, SYSTEMD_TIMER)
    return service, timer


def packagekit_status(
    backend: PacmanBackend,
    blackarch: Catalog,
    arch: ArchToolCatalog,
) -> dict[str, object]:
    backend.require_supported()
    installed = backend.installed_packages()
    rows: list[dict[str, str]] = []
    for tool in blackarch.tools:
        version = installed.get(tool.name)
        rows.append(
            {
                "package_id": f"{tool.name};{version or ''};{tool.architecture};blackarch",
                "name": tool.name,
                "status": "installed" if version else "available",
            }
        )
    for tool in arch.tools:
        version = installed.get(tool.name)
        rows.append(
            {
                "package_id": f"{tool.name};{version or tool.version};{tool.architecture};{tool.repository}",
                "name": tool.name,
                "status": "installed" if version else "available",
            }
        )
    return {
        "schema": "blackforge-packagekit-status-v1",
        "note": "PackageKit-compatible package IDs and status values; BlackForge is not a PackageKit backend.",
        "packages": rows,
    }
