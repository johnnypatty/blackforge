from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backend import BackendError, validate_package_names
from .storage import atomic_write_json

ENVIRONMENT_SCHEMA_VERSION = 1
SUPPORTED_PACKAGE_SOURCES = frozenset({"arch", "blackarch"})
MAX_ENVIRONMENT_PACKAGES = 10_000
MAX_TEXT_LENGTH = 256
MAX_ENVIRONMENT_FILE_BYTES = 16 * 1024 * 1024


class EnvironmentFileError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_plain_text(
    value: object, field: str, *, maximum: int = MAX_TEXT_LENGTH
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EnvironmentFileError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise EnvironmentFileError(f"{field} exceeds the {maximum}-character limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EnvironmentFileError(f"{field} contains control characters")
    return value


def _validate_timestamp(value: object, field: str = "created_at") -> str:
    text = _validate_plain_text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvironmentFileError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EnvironmentFileError(f"{field} must include a timezone")
    return text


def _validate_version(value: object, field: str = "version") -> str:
    return _validate_plain_text(value, field, maximum=256)


@dataclass(frozen=True, slots=True, order=True)
class PackageRef:
    source: str
    name: str

    def __post_init__(self) -> None:
        if self.source not in SUPPORTED_PACKAGE_SOURCES:
            supported = ", ".join(sorted(SUPPORTED_PACKAGE_SOURCES))
            raise EnvironmentFileError(
                f"Unsupported package source {self.source!r}; expected one of: {supported}"
            )
        try:
            validate_package_names([self.name])
        except BackendError as exc:
            raise EnvironmentFileError(str(exc)) from exc

    @property
    def qualified(self) -> str:
        return f"{self.source}:{self.name}"

    @classmethod
    def parse(cls, value: object) -> PackageRef:
        if not isinstance(value, str):
            raise EnvironmentFileError("Package reference must be a string")
        source, separator, name = value.partition(":")
        if not separator or not source or not name or ":" in name:
            raise EnvironmentFileError(
                f"Package reference {value!r} must use SOURCE:PACKAGE form"
            )
        return cls(source=source, name=name)


@dataclass(frozen=True, slots=True)
class EnvironmentPackage:
    ref: PackageRef
    version: str

    def __post_init__(self) -> None:
        _validate_version(self.version)

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref.qualified, "version": self.version}

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> EnvironmentPackage:
        if not isinstance(value, dict):
            raise EnvironmentFileError(
                f"Environment package at index {index} is not an object"
            )
        if set(value) - {"ref", "version"}:
            unknown = ", ".join(sorted(set(value) - {"ref", "version"}))
            raise EnvironmentFileError(
                f"Environment package at index {index} has unknown fields: {unknown}"
            )
        try:
            ref_value = value["ref"]
            version_value = value["version"]
        except KeyError as exc:
            raise EnvironmentFileError(
                f"Environment package at index {index} requires ref and version"
            ) from exc
        return cls(
            ref=PackageRef.parse(ref_value),
            version=_validate_version(version_value, f"packages[{index}].version"),
        )


@dataclass(frozen=True, slots=True)
class EnvironmentManifest:
    name: str
    created_at: str
    packages: tuple[EnvironmentPackage, ...]

    def __post_init__(self) -> None:
        _validate_plain_text(self.name, "Environment name")
        _validate_timestamp(self.created_at)
        if len(self.packages) > MAX_ENVIRONMENT_PACKAGES:
            raise EnvironmentFileError(
                f"Environment exceeds the {MAX_ENVIRONMENT_PACKAGES}-package limit"
            )
        refs = [package.ref.qualified for package in self.packages]
        if len(refs) != len(set(refs)):
            raise EnvironmentFileError(
                "Environment contains duplicate package references"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "name": self.name,
            "created_at": self.created_at,
            "packages": [package.to_dict() for package in self.packages],
        }

    @classmethod
    def from_dict(cls, value: object) -> EnvironmentManifest:
        if not isinstance(value, dict):
            raise EnvironmentFileError("Environment root must be an object")
        if set(value) != {"schema_version", "name", "created_at", "packages"}:
            raise EnvironmentFileError("Environment root has an invalid shape")
        if value.get("schema_version") != ENVIRONMENT_SCHEMA_VERSION:
            raise EnvironmentFileError(
                f"Unsupported environment schema: {value.get('schema_version')!r}"
            )
        packages_value = value.get("packages")
        if not isinstance(packages_value, list):
            raise EnvironmentFileError("Environment packages must be a list")
        if len(packages_value) > MAX_ENVIRONMENT_PACKAGES:
            raise EnvironmentFileError(
                f"Environment exceeds the {MAX_ENVIRONMENT_PACKAGES}-package limit"
            )
        packages = tuple(
            EnvironmentPackage.from_dict(item, index=index)
            for index, item in enumerate(packages_value)
        )
        return cls(
            name=_validate_plain_text(value.get("name"), "Environment name"),
            created_at=_validate_timestamp(value.get("created_at")),
            packages=packages,
        )


@dataclass(frozen=True, slots=True)
class VersionDrift:
    ref: PackageRef
    installed_version: str
    requested_version: str
    exact_version_available: bool = False
    note: str = (
        "Exact rollback/install is not promised by a rolling repository; "
        "review cached signed packages or the Arch Linux Archive manually."
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref.qualified,
            "installed_version": self.installed_version,
            "requested_version": self.requested_version,
            "exact_version_available": self.exact_version_available,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentImportPlan:
    environment: str
    install: tuple[EnvironmentPackage, ...]
    satisfied: tuple[EnvironmentPackage, ...]
    version_drift: tuple[VersionDrift, ...]
    ignored_extras: tuple[PackageRef, ...]
    plan_only: bool = True
    note: str = (
        "This is a non-destructive plan. Extra installed packages are ignored, "
        "and no package manager command has been executed."
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "plan_only": self.plan_only,
            "note": self.note,
            "install": [package.to_dict() for package in self.install],
            "satisfied": [package.to_dict() for package in self.satisfied],
            "version_drift": [drift.to_dict() for drift in self.version_drift],
            "ignored_extras": [ref.qualified for ref in self.ignored_extras],
        }


def create_environment(
    name: str,
    packages: Mapping[str, str] | Iterable[EnvironmentPackage],
    *,
    created_at: str | None = None,
) -> EnvironmentManifest:
    if isinstance(packages, Mapping):
        entries = [
            EnvironmentPackage(PackageRef.parse(ref), _validate_version(version))
            for ref, version in packages.items()
        ]
    else:
        entries = list(packages)
        if not all(isinstance(item, EnvironmentPackage) for item in entries):
            raise EnvironmentFileError(
                "Environment packages must be EnvironmentPackage values"
            )
    entries.sort(key=lambda item: item.ref.qualified)
    return EnvironmentManifest(
        name=_validate_plain_text(name, "Environment name"),
        created_at=_validate_timestamp(created_at or _now()),
        packages=tuple(entries),
    )


def write_environment(path: Path, manifest: EnvironmentManifest) -> None:
    if not isinstance(manifest, EnvironmentManifest):
        raise EnvironmentFileError("manifest must be an EnvironmentManifest")
    try:
        atomic_write_json(path, manifest.to_dict())
    except OSError as exc:
        raise EnvironmentFileError(f"Unable to save environment {path}: {exc}") from exc


def export_environment(
    path: Path,
    name: str,
    packages: Mapping[str, str] | Iterable[EnvironmentPackage],
    *,
    created_at: str | None = None,
) -> EnvironmentManifest:
    manifest = create_environment(name, packages, created_at=created_at)
    write_environment(path, manifest)
    return manifest


def read_environment(path: Path) -> EnvironmentManifest:
    try:
        if path.stat().st_size > MAX_ENVIRONMENT_FILE_BYTES:
            raise EnvironmentFileError("Environment file exceeds its safety size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except EnvironmentFileError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentFileError(f"Unable to read environment {path}: {exc}") from exc
    return EnvironmentManifest.from_dict(value)


def _validated_current_packages(current: Mapping[str, str]) -> dict[str, str]:
    validated: dict[str, str] = {}
    for ref_value, version_value in current.items():
        ref = PackageRef.parse(ref_value)
        version = _validate_version(
            version_value, f"Current version for {ref.qualified}"
        )
        if ref.qualified in validated:
            raise EnvironmentFileError(
                f"Current package set contains duplicate reference {ref.qualified}"
            )
        validated[ref.qualified] = version
    return validated


def plan_environment_import(
    manifest: EnvironmentManifest,
    current: Mapping[str, str] | None = None,
) -> EnvironmentImportPlan:
    if not isinstance(manifest, EnvironmentManifest):
        raise EnvironmentFileError("manifest must be an EnvironmentManifest")
    installed = _validated_current_packages(current or {})
    desired = {package.ref.qualified: package for package in manifest.packages}
    install: list[EnvironmentPackage] = []
    satisfied: list[EnvironmentPackage] = []
    drift: list[VersionDrift] = []
    for ref_value, package in desired.items():
        installed_version = installed.get(ref_value)
        if installed_version is None:
            install.append(package)
        elif installed_version == package.version:
            satisfied.append(package)
        else:
            drift.append(
                VersionDrift(
                    ref=package.ref,
                    installed_version=installed_version,
                    requested_version=package.version,
                )
            )
    ignored_extras = tuple(
        PackageRef.parse(ref_value)
        for ref_value in sorted(set(installed) - set(desired))
    )
    return EnvironmentImportPlan(
        environment=manifest.name,
        install=tuple(install),
        satisfied=tuple(satisfied),
        version_drift=tuple(drift),
        ignored_extras=ignored_extras,
    )


def import_environment(
    path: Path,
    current: Mapping[str, str] | None = None,
    *,
    plan_only: bool = True,
) -> EnvironmentImportPlan:
    if not plan_only:
        raise EnvironmentFileError(
            "Environment import is plan-only; execute reviewed package changes separately"
        )
    return plan_environment_import(read_environment(path), current)


def is_source_qualified(value: str) -> bool:
    try:
        PackageRef.parse(value)
    except EnvironmentFileError:
        return False
    return bool(re.fullmatch(r"[^:]+:[^:]+", value))
