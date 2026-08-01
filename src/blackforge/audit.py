"""Read-only host security and package-health audit."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass

from .backend import BackendError, PacmanBackend, Runner


class SecurityAuditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecurityAuditReport:
    installed_count: int
    outdated: tuple[dict[str, str], ...]
    vulnerable: tuple[dict[str, object], ...]
    keyring: dict[str, object]
    unavailable: tuple[str, ...]
    helper: dict[str, object]

    @property
    def exit_code(self) -> int:
        return 2 if self.vulnerable or self.keyring.get("healthy") is False else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "installed_count": self.installed_count,
            "states": {
                "outdated": list(self.outdated),
                "vulnerable": list(self.vulnerable),
                "unavailable": list(self.unavailable),
            },
            "keyring": self.keyring,
            "arch_audit": self.helper,
            "exit_code": self.exit_code,
            "notes": [
                "Unmaintained upstream projects are reported separately by `blackforge maintenance`.",
                "A package can appear in more than one state.",
            ],
        }


def _outdated(runner: Runner) -> tuple[dict[str, str], ...]:
    result = runner.run(["pacman", "-Qu"], capture=True, timeout=120)
    if result.returncode not in {0, 1}:
        raise SecurityAuditError((result.stderr or "pacman -Qu failed").strip())
    rows: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            rows.append(
                {"name": parts[0], "installed": parts[1], "available": parts[3]}
            )
    return tuple(rows)


def _vulnerabilities(
    runner: Runner,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    if shutil.which("arch-audit") is None:
        return (), {
            "available": False,
            "command": "sudo pacman -S arch-audit",
            "note": "Install the official Arch arch-audit package to include security.archlinux.org advisories.",
        }
    result = runner.run(["arch-audit", "--json"], capture=True, timeout=120)
    if result.returncode not in {0, 1}:
        raise SecurityAuditError((result.stderr or "arch-audit failed").strip())
    try:
        value = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SecurityAuditError(f"arch-audit returned invalid JSON: {exc}") from exc
    if isinstance(value, dict):
        if any(key in value for key in ("package", "pkgname", "name")):
            raw = [value]
        else:
            raw = next(
                (
                    value[key]
                    for key in ("vulnerabilities", "packages", "results", "advisories")
                    if isinstance(value.get(key), list)
                ),
                None,
            )
            if raw is None and value and all(
                isinstance(item, dict) for item in value.values()
            ):
                raw = [
                    {"package": package, **details}
                    for package, details in value.items()
                ]
            if raw is None:
                raw = []
    else:
        raw = value
    if not isinstance(raw, list):
        raise SecurityAuditError("arch-audit returned an unsupported JSON shape")
    rows = tuple(item for item in raw if isinstance(item, dict))
    return rows, {
        "available": True,
        "source": "security.archlinux.org",
        "count": len(rows),
    }


def _keyring(runner: Runner, installed: dict[str, str]) -> dict[str, object]:
    package_version = installed.get("archlinux-keyring")
    if not package_version:
        return {"healthy": False, "package": "archlinux-keyring", "installed": False}
    if shutil.which("pacman-key") is None:
        return {
            "healthy": False,
            "package": "archlinux-keyring",
            "installed": True,
            "version": package_version,
            "error": "pacman-key was not found",
        }
    result = runner.run(["pacman-key", "--list-keys"], capture=True, timeout=120)
    return {
        "healthy": result.returncode == 0,
        "package": "archlinux-keyring",
        "installed": True,
        "version": package_version,
        "error": ""
        if result.returncode == 0
        else (result.stderr or result.stdout).strip(),
    }


def audit_host(backend: PacmanBackend) -> SecurityAuditReport:
    backend.require_supported()
    installed = backend.installed_packages()
    outdated = _outdated(backend.runner)
    vulnerable, helper = _vulnerabilities(backend.runner)
    try:
        available = backend.runner.run(["pacman", "-Slq"], capture=True, timeout=120)
        available_names = (
            set(available.stdout.splitlines()) if available.returncode == 0 else set()
        )
    except BackendError:
        available_names = set()
    unavailable = (
        tuple(sorted(set(installed) - available_names)) if available_names else ()
    )
    return SecurityAuditReport(
        installed_count=len(installed),
        outdated=outdated,
        vulnerable=vulnerable,
        keyring=_keyring(backend.runner, installed),
        unavailable=unavailable,
        helper=helper,
    )
