"""Conservative Btrfs/Snapper and pacman-cache rollback helpers."""

from __future__ import annotations

import platform
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .backend import CommandResult, Runner, validate_package_names


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotStatus:
    linux: bool
    btrfs_root: bool
    snapper_installed: bool
    snapper_root_configured: bool

    @property
    def ready(self) -> bool:
        return (
            self.linux
            and self.btrfs_root
            and self.snapper_installed
            and self.snapper_root_configured
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "linux": self.linux,
            "btrfs_root": self.btrfs_root,
            "snapper_installed": self.snapper_installed,
            "snapper_root_configured": self.snapper_root_configured,
            "ready": self.ready,
        }


def snapshot_status(runner: Runner | None = None) -> SnapshotStatus:
    active = runner or Runner()
    linux = platform.system() == "Linux"
    btrfs = False
    configured = False
    if linux and shutil.which("findmnt"):
        result = active.run(
            ["findmnt", "-n", "-o", "FSTYPE", "/"], capture=True, timeout=15
        )
        btrfs = result.returncode == 0 and result.stdout.strip() == "btrfs"
    snapper = linux and shutil.which("snapper") is not None
    if snapper:
        result = active.run(
            ["snapper", "-c", "root", "get-config"], capture=True, timeout=30
        )
        configured = result.returncode == 0
    return SnapshotStatus(linux, btrfs, snapper, configured)


def create_snapshot(
    description: str, *, apply: bool, runner: Runner | None = None
) -> CommandResult:
    if not description.strip() or len(description) > 120 or "\n" in description:
        raise SnapshotError(
            "Snapshot description must be 1 to 120 characters on one line"
        )
    status = snapshot_status(runner)
    if not status.ready:
        raise SnapshotError(
            "Btrfs root and a configured Snapper 'root' profile are required"
        )
    command = [
        "snapper",
        "-c",
        "root",
        "create",
        "--type",
        "single",
        "--print-number",
        "--description",
        description,
    ]
    if hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() != 0:
        if shutil.which("sudo") is None:
            raise SnapshotError("sudo is required to create a system snapshot")
        command.insert(0, "sudo")
    if not apply:
        return CommandResult(command, 0, planned=True)
    active = runner or Runner()
    result = active.run(command, capture=True, timeout=120, mutating=True)
    if result.returncode != 0:
        raise SnapshotError(
            (result.stderr or result.stdout or "Snapper snapshot failed").strip()
        )
    return result


def pacman_cache_rollback_plan(
    versions: dict[str, str],
    *,
    cache: Path = Path("/var/cache/pacman/pkg"),
) -> dict[str, object]:
    names = validate_package_names(list(versions))
    archives: list[str] = []
    missing: list[dict[str, str]] = []
    try:
        cache_files = tuple(path for path in cache.iterdir() if path.is_file())
    except OSError as exc:
        raise SnapshotError(f"Unable to read pacman cache {cache}: {exc}") from exc
    for name in names:
        version = versions[name]
        if not re.fullmatch(r"[^\x00-\x20/\\]{1,255}", version):
            raise SnapshotError(f"Unsafe version for {name!r}")
        matches = sorted(
            (
                path
                for path in cache_files
                if path.name.startswith(f"{name}-{version}-")
                and ".pkg.tar." in path.name
                and not path.name.endswith(".sig")
            ),
            key=lambda path: path.name,
        )
        if matches:
            archives.append(str(matches[-1]))
        else:
            missing.append({"name": name, "version": version})
    command = ["sudo", "pacman", "-U", "--", *archives] if archives else []
    return {
        "complete": not missing,
        "archives": archives,
        "missing": missing,
        "command": command,
    }
