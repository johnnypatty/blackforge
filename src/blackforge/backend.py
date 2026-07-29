from __future__ import annotations

import glob
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__

BLACKARCH_STRAP_URL = "https://blackarch.org/strap.sh"
EXPECTED_STRAP_SHA1 = "00688950aaf5e5804d2abebb8d3d3ea1d28525ed"
PACKAGE_NAME = re.compile(r"^[a-zA-Z0-9@._+][a-zA-Z0-9@._+-]*$")
MAX_STRAP_BYTES = 1024 * 1024


class BackendError(RuntimeError):
    pass


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    planned: bool = False


class Runner:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(
        self,
        args: Sequence[str],
        *,
        capture: bool = False,
        timeout: int | None = None,
        mutating: bool = False,
    ) -> CommandResult:
        command = list(args)
        if self.dry_run and mutating:
            return CommandResult(command, 0, planned=True)
        try:
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=capture,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise BackendError(f"Command not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"Command timed out: {' '.join(command)}") from exc
        except OSError as exc:
            raise BackendError(f"Unable to run {command[0]}: {exc}") from exc
        return CommandResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


def validate_package_names(names: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not PACKAGE_NAME.fullmatch(name):
            raise BackendError(f"Unsafe or invalid package name: {name!r}")
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    if not cleaned:
        raise BackendError("At least one package is required")
    return cleaned


class PacmanBackend:
    def __init__(self, *, dry_run: bool = False, assume_yes: bool = False) -> None:
        self.runner = Runner(dry_run=dry_run)
        self.assume_yes = assume_yes

    @property
    def supported(self) -> bool:
        return platform.system() == "Linux" and shutil.which("pacman") is not None

    @property
    def repo_enabled(self) -> bool:
        return self._config_has_repository(Path("/etc/pacman.conf"), "blackarch")

    def _config_has_repository(
        self,
        path: Path,
        repository: str,
        visited: set[Path] | None = None,
    ) -> bool:
        visited = visited or set()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in visited:
            return False
        visited.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.casefold() == f"[{repository.casefold()}]":
                return True
            match = re.fullmatch(r"Include\s*=\s*(.+)", line, re.IGNORECASE)
            if match:
                for included in glob.glob(match.group(1).strip()):
                    if self._config_has_repository(Path(included), repository, visited):
                        return True
        return False

    def require_supported(self) -> None:
        if not self.supported:
            raise BackendError(
                "Package operations require Arch Linux or BlackArch with pacman available"
            )

    def _privileged(self, args: Sequence[str]) -> list[str]:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return list(args)
        if shutil.which("sudo") is None:
            raise BackendError("sudo is required for this package operation")
        return ["sudo", *args]

    def available_packages(self) -> dict[str, str]:
        self.require_supported()
        result = self.runner.run(["pacman", "-Sl", "blackarch"], capture=True, timeout=120)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BackendError(f"Unable to read the BlackArch package database: {detail}")
        packages: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                packages[parts[1]] = parts[2]
        return packages

    def installed_packages(self) -> dict[str, str]:
        self.require_supported()
        result = self.runner.run(["pacman", "-Q"], capture=True, timeout=120)
        if result.returncode != 0:
            raise BackendError((result.stderr or "Unable to query installed packages").strip())
        packages: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                packages[parts[0]] = parts[1]
        return packages

    def package_executables(self, package: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        validate_package_names([package])
        result = self.runner.run(["pacman", "-Ql", package], capture=True, timeout=60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BackendError(f"Unable to inspect installed package {package}: {detail}")
        declared: list[str] = []
        missing: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            path = parts[1]
            if not path.startswith("/usr/bin/") or path.endswith("/"):
                continue
            declared.append(path)
            candidate = Path(path)
            if not candidate.exists() or not os.access(candidate, os.X_OK):
                missing.append(path)
        return tuple(sorted(set(declared))), tuple(sorted(set(missing)))

    def install(self, names: Sequence[str]) -> CommandResult:
        packages = validate_package_names(names)
        args = ["pacman", "-S", "--needed"]
        if self.assume_yes:
            args.append("--noconfirm")
        args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True)

    def remove(self, names: Sequence[str], *, purge: bool = False) -> CommandResult:
        packages = validate_package_names(names)
        args = ["pacman", "-Rns" if purge else "-R"]
        if self.assume_yes:
            args.append("--noconfirm")
        args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True)

    def upgrade(self, names: Sequence[str] = ()) -> CommandResult:
        packages = validate_package_names(names) if names else []
        args = ["pacman", "-Syu" if not packages else "-S"]
        if packages:
            args.append("--needed")
        if self.assume_yes:
            args.append("--noconfirm")
        if packages:
            args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True)

    def download_strap(
        self,
        *,
        reviewed_sha256: str | None = None,
    ) -> tuple[Path, str, str, bool]:
        request = urllib.request.Request(
            BLACKARCH_STRAP_URL,
            headers={"User-Agent": f"BlackForge/{__version__} repository setup"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                final_url = response.geturl()
                if not final_url.lower().startswith("https://"):
                    raise BackendError(f"Refusing non-HTTPS setup script: {final_url}")
                data = response.read(MAX_STRAP_BYTES + 1)
        except OSError as exc:
            raise BackendError(f"Unable to download the official setup script: {exc}") from exc
        if len(data) > MAX_STRAP_BYTES:
            raise BackendError("Official setup script exceeded the 1 MiB safety limit")
        if not data.startswith(b"#!/"):
            raise BackendError("Downloaded setup script does not have a shebang")
        sha1 = hashlib.sha1(data).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()
        checksum_matched = sha1 == EXPECTED_STRAP_SHA1
        if reviewed_sha256 is not None and not re.fullmatch(
            r"[0-9a-fA-F]{64}", reviewed_sha256
        ):
            raise BackendError("--strap-sha256 must be exactly 64 hexadecimal characters")
        reviewed_matches = (
            reviewed_sha256 is not None and reviewed_sha256.casefold() == sha256
        )
        if not checksum_matched and not reviewed_matches:
            raise BackendError(
                "The official setup script no longer matches BlackForge's pinned "
                f"SHA-1 (expected {EXPECTED_STRAP_SHA1}, got {sha1}). Review the "
                "downloaded script and rerun with "
                f"`--strap-sha256 {sha256}` only if you trust that exact file."
            )
        descriptor, raw_path = tempfile.mkstemp(prefix="blackforge-strap-", suffix=".sh")
        path = Path(raw_path)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path, sha256, sha1, checksum_matched

    def enable_repo(self, script: Path) -> CommandResult:
        self.require_supported()
        if not script.is_file():
            raise BackendError(f"Setup script does not exist: {script}")
        return self.runner.run(
            self._privileged(["bash", str(script)]),
            mutating=True,
        )
