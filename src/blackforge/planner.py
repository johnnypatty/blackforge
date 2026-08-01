from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .backend import BackendError, validate_package_names
from .sources import SourceError, parse_arch_reference, resolve_arch_tool


class PlannerError(RuntimeError):
    """Raised when package-plan metadata is malformed."""


def _validated_targets(names: Sequence[str]) -> tuple[str, ...]:
    targets: list[str] = []
    seen: set[str] = set()
    for value in names:
        try:
            if value.startswith("arch:"):
                target = resolve_arch_tool(value).package_target
            elif value.startswith("blackarch:"):
                target = validate_package_names([value.removeprefix("blackarch:")])[0]
            elif "/" in value:
                repository, name = parse_arch_reference(value)
                if repository is None:
                    raise PlannerError(
                        f"Repository-qualified package expected: {value}"
                    )
                target = f"{repository}/{name}"
            else:
                target = validate_package_names([value])[0]
        except (BackendError, SourceError) as exc:
            raise PlannerError(str(exc)) from exc
        if target not in seen:
            targets.append(target)
            seen.add(target)
    if not targets:
        raise PlannerError("At least one package is required")
    return tuple(targets)


@dataclass(frozen=True, slots=True)
class PackagePlan:
    """Read-only metadata for one package in a planned transaction."""

    name: str
    version: str = ""
    repository: str = ""
    installed_version: str = ""
    download_size_bytes: int | None = None
    installed_size_bytes: int | None = None
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    requested: bool = True

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = list(self.dependencies)
        value["conflicts"] = list(self.conflicts)
        return value


@dataclass(frozen=True, slots=True)
class PlanningMetadata:
    """Optional transaction metadata supplied by a package backend."""

    packages: tuple[PackagePlan, ...] = ()
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    download_size_bytes: int | None = None
    installed_size_bytes: int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    """A package operation preview. Creating a plan never changes the system."""

    operation: str
    requested: tuple[str, ...]
    command: tuple[str, ...]
    packages: tuple[PackagePlan, ...] = ()
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    download_size_bytes: int | None = None
    installed_size_bytes: int | None = None
    free_disk_bytes: int | None = None
    space_sufficient: bool | None = None
    dry_run: bool = True
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "requested": list(self.requested),
            "command": list(self.command),
            "packages": [package.to_dict() for package in self.packages],
            "dependencies": list(self.dependencies),
            "conflicts": list(self.conflicts),
            "download_size_bytes": self.download_size_bytes,
            "installed_size_bytes": self.installed_size_bytes,
            "free_disk_bytes": self.free_disk_bytes,
            "space_sufficient": self.space_sufficient,
            "dry_run": self.dry_run,
            "warnings": list(self.warnings),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"


def _non_negative_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlannerError(f"{field} must be a non-negative integer or null")
    return value


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence):
        values = value
    else:
        raise PlannerError(f"{field} must be a string or sequence of strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return tuple(cleaned)


def _package_from_value(
    name: str,
    value: PackagePlan | Mapping[str, Any] | str,
    requested: set[str],
) -> PackagePlan:
    if isinstance(value, PackagePlan):
        return PackagePlan(
            name=value.name,
            version=value.version,
            repository=value.repository,
            installed_version=value.installed_version,
            download_size_bytes=value.download_size_bytes,
            installed_size_bytes=value.installed_size_bytes,
            dependencies=value.dependencies,
            conflicts=value.conflicts,
            requested=value.name in requested,
        )
    if isinstance(value, str):
        return PackagePlan(name=name, version=value, requested=name in requested)
    if not isinstance(value, Mapping):
        raise PlannerError(f"Metadata for {name!r} must be a mapping")
    actual_name = str(value.get("name", name)).strip()
    if not actual_name:
        raise PlannerError("Package metadata contains an empty name")
    return PackagePlan(
        name=actual_name,
        version=str(value.get("version", "")),
        repository=str(value.get("repository", value.get("source", ""))),
        installed_version=str(value.get("installed_version", "")),
        download_size_bytes=_non_negative_int(
            value.get("download_size_bytes", value.get("download_size")),
            f"{actual_name}.download_size_bytes",
        ),
        installed_size_bytes=_non_negative_int(
            value.get("installed_size_bytes", value.get("installed_size")),
            f"{actual_name}.installed_size_bytes",
        ),
        dependencies=_strings(
            value.get("dependencies", ()), f"{actual_name}.dependencies"
        ),
        conflicts=_strings(value.get("conflicts", ()), f"{actual_name}.conflicts"),
        requested=actual_name in requested,
    )


def _packages_from_mapping(
    values: Mapping[str, Any],
    requested: tuple[str, ...],
) -> tuple[PackagePlan, ...]:
    requested_names = tuple(value.split("/", 1)[-1] for value in requested)
    requested_set = {*requested, *requested_names}
    by_name = {
        str(name): _package_from_value(str(name), value, requested_set)
        for name, value in values.items()
    }
    ordered_names = [name for name in requested_names if name in by_name]
    ordered_names.extend(sorted(set(by_name) - set(ordered_names)))
    return tuple(by_name[name] for name in ordered_names)


def _normalise_metadata(
    raw: PlanningMetadata | Mapping[str, Any] | Sequence[PackagePlan] | None,
    requested: tuple[str, ...],
) -> PlanningMetadata:
    if raw is None:
        return PlanningMetadata()
    if isinstance(raw, PlanningMetadata):
        return raw
    if isinstance(raw, Mapping):
        if "packages" not in raw:
            return PlanningMetadata(packages=_packages_from_mapping(raw, requested))
        package_values = raw.get("packages", {})
        if isinstance(package_values, Mapping):
            packages = _packages_from_mapping(package_values, requested)
        elif isinstance(package_values, Sequence) and not isinstance(
            package_values, (str, bytes)
        ):
            requested_set = {
                *requested,
                *(value.split("/", 1)[-1] for value in requested),
            }
            packages = tuple(
                _package_from_value(
                    getattr(value, "name", "")
                    if isinstance(value, PackagePlan)
                    else str(value.get("name", "")),
                    value,
                    requested_set,
                )
                for value in package_values
            )
        else:
            raise PlannerError("packages must be a mapping or sequence")
        return PlanningMetadata(
            packages=packages,
            dependencies=_strings(raw.get("dependencies"), "dependencies"),
            conflicts=_strings(raw.get("conflicts"), "conflicts"),
            download_size_bytes=_non_negative_int(
                raw.get("download_size_bytes", raw.get("download_size")),
                "download_size_bytes",
            ),
            installed_size_bytes=_non_negative_int(
                raw.get("installed_size_bytes", raw.get("installed_size")),
                "installed_size_bytes",
            ),
            warnings=_strings(raw.get("warnings"), "warnings"),
        )
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        requested_set = {
            *requested,
            *(value.split("/", 1)[-1] for value in requested),
        }
        return PlanningMetadata(
            packages=tuple(
                _package_from_value(value.name, value, requested_set) for value in raw
            )
        )
    raise PlannerError("Unsupported planning metadata")


def _fallback_metadata(
    backend: Any,
    operation: str,
    requested: tuple[str, ...],
) -> PlanningMetadata:
    if not requested:
        return PlanningMetadata()
    method_name = (
        "installed_packages" if operation == "remove" else "available_packages"
    )
    provider = getattr(backend, method_name, None)
    if not callable(provider):
        return PlanningMetadata()
    try:
        versions = provider()
    except (BackendError, OSError) as exc:
        return PlanningMetadata(warnings=(f"Package metadata unavailable: {exc}",))
    if not isinstance(versions, Mapping):
        raise PlannerError(f"{method_name}() must return a mapping")
    packages: list[PackagePlan] = []
    for name in requested:
        if name not in versions:
            continue
        version = str(versions[name])
        packages.append(
            PackagePlan(
                name=name,
                version="" if operation == "remove" else version,
                installed_version=version if operation == "remove" else "",
            )
        )
    return PlanningMetadata(packages=tuple(packages))


def _metadata(
    backend: Any | None,
    operation: str,
    requested: tuple[str, ...],
) -> PlanningMetadata:
    if backend is None:
        return PlanningMetadata()
    provider = getattr(backend, "plan_metadata", None)
    if callable(provider):
        try:
            return _normalise_metadata(provider(operation, requested), requested)
        except (BackendError, OSError) as exc:
            return PlanningMetadata(
                warnings=(f"Transaction metadata unavailable: {exc}",)
            )
    return _fallback_metadata(backend, operation, requested)


def _known_total(
    packages: tuple[PackagePlan, ...],
    field: str,
) -> int | None:
    if not packages:
        return None
    values = [getattr(package, field) for package in packages]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _free_disk(
    path: Path,
    disk_usage: Callable[[str | bytes | Path], Any],
) -> tuple[int | None, str | None]:
    try:
        usage = disk_usage(path)
        free = usage.free if hasattr(usage, "free") else usage[2]
        return int(free), None
    except (OSError, TypeError, ValueError, IndexError) as exc:
        return None, f"Free disk space unavailable: {exc}"


def _command(
    operation: str,
    names: tuple[str, ...],
    *,
    assume_yes: bool,
    purge: bool,
) -> tuple[str, ...]:
    if operation == "install":
        command = ["pacman", "-S", "--needed"]
    elif operation == "remove":
        command = ["pacman", "-Rns" if purge else "-R"]
    elif operation == "upgrade":
        command = ["pacman", "-Syu" if not names else "-S"]
        if names:
            command.append("--needed")
    else:
        raise PlannerError(f"Unsupported operation: {operation}")
    if assume_yes:
        command.append("--noconfirm")
    if names:
        command.extend(["--", *names])
    return tuple(command)


def _make_plan(
    operation: str,
    names: Sequence[str],
    *,
    backend: Any | None,
    assume_yes: bool | None,
    purge: bool,
    disk_path: Path,
    disk_usage: Callable[[str | bytes | Path], Any],
) -> Plan:
    if operation == "upgrade" and not names:
        requested: tuple[str, ...] = ()
    else:
        requested = _validated_targets(names)
        if operation == "remove":
            requested = tuple(
                dict.fromkeys(value.split("/", 1)[-1] for value in requested)
            )
    if assume_yes is None:
        assume_yes = bool(getattr(backend, "assume_yes", False))

    metadata = _metadata(backend, operation, requested)
    package_dependencies = (
        dependency
        for package in metadata.packages
        for dependency in package.dependencies
    )
    package_conflicts = (
        conflict for package in metadata.packages for conflict in package.conflicts
    )
    dependencies = tuple(dict.fromkeys((*metadata.dependencies, *package_dependencies)))
    conflicts = tuple(dict.fromkeys((*metadata.conflicts, *package_conflicts)))

    download_size = metadata.download_size_bytes
    if download_size is None:
        download_size = (
            0
            if operation == "remove"
            else _known_total(metadata.packages, "download_size_bytes")
        )
    installed_size = metadata.installed_size_bytes
    if installed_size is None:
        installed_size = _known_total(metadata.packages, "installed_size_bytes")

    free_disk, disk_warning = _free_disk(disk_path, disk_usage)
    warnings = list(metadata.warnings)
    if disk_warning:
        warnings.append(disk_warning)
    space_sufficient = None
    if (
        operation in {"install", "upgrade"}
        and installed_size is not None
        and free_disk is not None
    ):
        space_sufficient = free_disk >= installed_size
        if not space_sufficient:
            warnings.append(
                "Estimated installed size exceeds currently available disk space"
            )

    return Plan(
        operation=operation,
        requested=requested,
        command=_command(
            operation,
            requested,
            assume_yes=assume_yes,
            purge=purge,
        ),
        packages=metadata.packages,
        dependencies=dependencies,
        conflicts=conflicts,
        download_size_bytes=download_size,
        installed_size_bytes=installed_size,
        free_disk_bytes=free_disk,
        space_sufficient=space_sufficient,
        warnings=tuple(warnings),
    )


def plan_install(
    names: Sequence[str],
    *,
    backend: Any | None = None,
    assume_yes: bool | None = None,
    disk_path: Path = Path("/"),
    disk_usage: Callable[[str | bytes | Path], Any] = shutil.disk_usage,
) -> Plan:
    return _make_plan(
        "install",
        names,
        backend=backend,
        assume_yes=assume_yes,
        purge=False,
        disk_path=disk_path,
        disk_usage=disk_usage,
    )


def plan_remove(
    names: Sequence[str],
    *,
    backend: Any | None = None,
    assume_yes: bool | None = None,
    purge: bool = False,
    disk_path: Path = Path("/"),
    disk_usage: Callable[[str | bytes | Path], Any] = shutil.disk_usage,
) -> Plan:
    return _make_plan(
        "remove",
        names,
        backend=backend,
        assume_yes=assume_yes,
        purge=purge,
        disk_path=disk_path,
        disk_usage=disk_usage,
    )


def plan_upgrade(
    names: Sequence[str] = (),
    *,
    backend: Any | None = None,
    assume_yes: bool | None = None,
    disk_path: Path = Path("/"),
    disk_usage: Callable[[str | bytes | Path], Any] = shutil.disk_usage,
) -> Plan:
    return _make_plan(
        "upgrade",
        names,
        backend=backend,
        assume_yes=assume_yes,
        purge=False,
        disk_path=disk_path,
        disk_usage=disk_usage,
    )
