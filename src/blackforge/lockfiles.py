"""Version locks, drift comparison, and standard SBOM export."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .backend import PacmanBackend, validate_package_names
from .catalog import Catalog, bundled_catalog
from .sources import ArchToolCatalog, bundled_arch_catalog
from .storage import atomic_write_json

LOCK_SCHEMA = "https://johnnypatty.github.io/blackforge/schemas/lock-v1.json"
_VERSION = re.compile(r"^[^\x00-\x20/\\]{1,255}$")


class LockfileError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LockedPackage:
    name: str
    version: str
    source: str
    repository: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        validate_package_names([self.name])
        if not _VERSION.fullmatch(self.version):
            raise LockfileError(f"Invalid version for {self.name!r}")
        if self.source not in {"blackarch", "arch"}:
            raise LockfileError(f"Unsupported package source: {self.source!r}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", self.repository):
            raise LockfileError(f"Invalid repository: {self.repository!r}")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise LockfileError(f"Invalid SHA-256 for {self.name!r}")

    @property
    def ref(self) -> str:
        return (
            f"blackarch:{self.name}"
            if self.source == "blackarch"
            else f"arch:{self.repository}/{self.name}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "repository": self.repository,
            "sha256": self.sha256,
        }


def _cached_checksum(
    name: str, version: str, cache: Path = Path("/var/cache/pacman/pkg")
) -> str | None:
    try:
        candidates = [
            path
            for path in cache.iterdir()
            if path.is_file()
            and path.name.startswith(f"{name}-{version}-")
            and ".pkg.tar." in path.name
            and not path.name.endswith(".sig")
        ]
    except OSError:
        return None
    if not candidates:
        return None
    path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _source_for(
    name: str,
    blackarch: Catalog,
    arch: ArchToolCatalog,
) -> tuple[str, str] | None:
    if name in blackarch.by_name:
        return "blackarch", "blackarch"
    tool = arch.by_name.get(name)
    if tool:
        return "arch", tool.repository
    return None


def create_lock(
    backend: PacmanBackend,
    names: Sequence[str] = (),
    *,
    blackarch: Catalog | None = None,
    arch: ArchToolCatalog | None = None,
) -> dict[str, object]:
    backend.require_supported()
    installed = backend.installed_packages()
    selected = validate_package_names(names) if names else sorted(installed)
    unknown = sorted(set(selected) - set(installed))
    if unknown:
        raise LockfileError(f"Packages are not installed: {', '.join(unknown)}")
    blackarch_catalog = blackarch or bundled_catalog()
    arch_catalog = arch or bundled_arch_catalog()
    packages: list[LockedPackage] = []
    skipped: list[str] = []
    for name in selected:
        source = _source_for(name, blackarch_catalog, arch_catalog)
        if source is None:
            if names:
                raise LockfileError(f"{name!r} is not in a trusted BlackForge source")
            skipped.append(name)
            continue
        packages.append(
            LockedPackage(
                name=name,
                version=installed[name],
                source=source[0],
                repository=source[1],
                sha256=_cached_checksum(name, installed[name]),
            )
        )
    return {
        "$schema": LOCK_SCHEMA,
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generator": f"blackforge/{__version__}",
        "packages": [package.to_dict() for package in packages],
        "skipped_non_security_packages": len(skipped),
        "checksum_note": "sha256 is null when the exact signed package archive is not present in pacman's local cache.",
    }


def write_lock(path: Path, value: Mapping[str, object]) -> None:
    atomic_write_json(path, dict(value))


def read_lock(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockfileError(f"Unable to read lockfile {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise LockfileError("Unsupported lockfile schema")
    raw = value.get("packages")
    if not isinstance(raw, list):
        raise LockfileError("Lockfile has no packages list")
    seen: set[str] = set()
    packages: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise LockfileError("Malformed package in lockfile")
        try:
            package = LockedPackage(
                name=str(item["name"]),
                version=str(item["version"]),
                source=str(item["source"]),
                repository=str(item["repository"]),
                sha256=item.get("sha256")
                if isinstance(item.get("sha256"), str)
                else None,
            )
        except KeyError as exc:
            raise LockfileError(f"Malformed package in lockfile: {exc}") from exc
        if package.ref in seen:
            raise LockfileError(f"Duplicate package in lockfile: {package.ref}")
        seen.add(package.ref)
        packages.append(package.to_dict())
    return {**value, "packages": packages}


def compare_lock(
    value: Mapping[str, object], current: Mapping[str, str]
) -> dict[str, object]:
    missing: list[dict[str, object]] = []
    drift: list[dict[str, object]] = []
    matched: list[dict[str, object]] = []
    for item in value.get("packages", []):
        if not isinstance(item, dict):
            continue
        installed = current.get(str(item["name"]))
        row = {**item, "installed_version": installed}
        if installed is None:
            missing.append(row)
        elif installed != item["version"]:
            drift.append(row)
        else:
            matched.append(row)
    return {
        "matched": matched,
        "missing": missing,
        "version_drift": drift,
        "matches": not missing and not drift,
    }


def sbom_from_lock(value: Mapping[str, object], format_name: str) -> dict[str, object]:
    packages = [item for item in value.get("packages", []) if isinstance(item, dict)]
    identity = hashlib.sha256(
        json.dumps(packages, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if format_name == "cyclonedx":
        components = []
        for item in packages:
            component: dict[str, object] = {
                "type": "application",
                "name": item["name"],
                "version": item["version"],
                "purl": f"pkg:arch/{item['name']}@{urllib_quote(str(item['version']))}?repository={item['repository']}",
            }
            if item.get("sha256"):
                component["hashes"] = [{"alg": "SHA-256", "content": item["sha256"]}]
            components.append(component)
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "serialNumber": f"urn:uuid:{uuid.UUID(identity[:32])}",
            "metadata": {
                "tools": {
                    "components": [
                        {
                            "type": "application",
                            "name": "BlackForge",
                            "version": __version__,
                        }
                    ]
                }
            },
            "components": components,
        }
    if format_name == "spdx":
        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "BlackForge package lock",
            "documentNamespace": f"https://johnnypatty.github.io/blackforge/sbom/{identity}",
            "creationInfo": {
                "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "creators": [f"Tool: BlackForge-{__version__}"],
            },
            "packages": [
                {
                    "name": item["name"],
                    "SPDXID": f"SPDXRef-Package-{index}",
                    "versionInfo": item["version"],
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "checksums": (
                        [{"algorithm": "SHA256", "checksumValue": item["sha256"]}]
                        if item.get("sha256")
                        else []
                    ),
                }
                for index, item in enumerate(packages, start=1)
            ],
        }
    raise LockfileError(f"Unsupported SBOM format: {format_name!r}")


def urllib_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
