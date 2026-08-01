from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .backend import PacmanBackend
from .models import PackageState, Tool
from .repository import RepositorySnapshot, package_base_version

UNHEALTHY_STATUSES = {
    "installed-files-missing",
    "missing-from-repo",
    "repo-not-enabled",
    "unverified",
}


@dataclass(slots=True)
class Audit:
    states: list[PackageState]
    environment: str
    note: str = ""
    metadata: dict[str, str | int] | None = None

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for state in self.states:
            result[state.status] = result.get(state.status, 0) + 1
        return dict(sorted(result.items()))

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "note": self.note,
            "metadata": self.metadata or {},
            "counts": self.counts,
            "packages": [state.to_dict() for state in self.states],
        }

    @property
    def exit_code(self) -> int:
        return (
            3 if any(state.status in UNHEALTHY_STATUSES for state in self.states) else 0
        )


def audit_tools(
    tools: Iterable[Tool],
    backend: PacmanBackend,
    *,
    check_executables: bool = False,
) -> Audit:
    selected = list(tools)
    if not backend.supported:
        states = [
            PackageState(
                name=tool.name,
                catalog_version=tool.version,
                repository_version=None,
                installed_version=None,
                status="unverified",
                note="pacman is unavailable on this system",
            )
            for tool in selected
        ]
        return Audit(
            states=states,
            environment="unsupported",
            note="Run this audit on Arch Linux or BlackArch for live repository results.",
        )

    if not backend.repo_enabled:
        states = [
            PackageState(
                name=tool.name,
                catalog_version=tool.version,
                repository_version=None,
                installed_version=None,
                status="repo-not-enabled",
                note="the [blackarch] repository is not configured",
            )
            for tool in selected
        ]
        return Audit(
            states=states, environment="arch", note="Enable the repository first."
        )

    available = backend.available_packages()
    installed = backend.installed_packages()
    states: list[PackageState] = []
    for tool in selected:
        repository_version = available.get(tool.name)
        installed_version = installed.get(tool.name)
        executables: tuple[str, ...] = ()
        missing: tuple[str, ...] = ()
        note = ""
        if repository_version is None:
            status = "missing-from-repo"
            note = (
                "listed on the website but absent from the synced repository database"
            )
        elif installed_version is None:
            status = "available"
        elif check_executables:
            executables, missing = backend.package_executables(tool.name)
            if missing:
                status = "installed-files-missing"
                note = "one or more packaged /usr/bin entries are missing or not executable"
            elif executables:
                status = "installed-files-ok"
                note = "packaged command files exist; program behavior was not executed"
            else:
                status = "installed-no-cli"
                note = "package declares no /usr/bin command; it may be a library, data, or GUI package"
        else:
            status = "installed"
            note = "package database confirms installation; program behavior was not executed"
        if (
            repository_version
            and tool.version
            and package_base_version(repository_version) != tool.version
            and status
            in {"available", "installed", "installed-files-ok", "installed-no-cli"}
        ):
            note = (
                f"{note}; website version {tool.version} differs from repository "
                f"version {repository_version}"
            ).lstrip("; ")
        states.append(
            PackageState(
                name=tool.name,
                catalog_version=tool.version,
                repository_version=repository_version,
                installed_version=installed_version,
                status=status,
                executables=executables,
                missing_executables=missing,
                note=note,
            )
        )
    return Audit(states=states, environment="blackarch-repository")


def audit_repository_snapshot(
    tools: Iterable[Tool],
    snapshot: RepositorySnapshot,
) -> Audit:
    states: list[PackageState] = []
    for tool in tools:
        repository_version = snapshot.packages.get(tool.name)
        if repository_version is None:
            states.append(
                PackageState(
                    name=tool.name,
                    catalog_version=tool.version,
                    repository_version=None,
                    installed_version=None,
                    status="missing-from-repo",
                    note="listed on the website but absent from the live x86_64 repository database",
                )
            )
            continue
        note = (
            "live repository database confirms the package; install/runtime not tested"
        )
        base_version = package_base_version(repository_version)
        if tool.version and base_version != tool.version:
            note += (
                f"; website version {tool.version} differs from repository "
                f"package version {repository_version}"
            )
        states.append(
            PackageState(
                name=tool.name,
                catalog_version=tool.version,
                repository_version=repository_version,
                installed_version=None,
                status="available",
                note=note,
            )
        )
    return Audit(
        states=states,
        environment="blackarch-remote-x86_64-repository",
        note=(
            "Availability is verified from repository metadata. Runtime behavior is not "
            "tested and installed state is unknown."
        ),
        metadata={
            "repository_source": snapshot.source,
            "repository_fetched_at": snapshot.fetched_at,
            "repository_last_modified": snapshot.last_modified,
            "repository_sha256": snapshot.source_sha256,
            "repository_package_count": len(snapshot.packages),
        },
    )
