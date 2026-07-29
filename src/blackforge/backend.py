from __future__ import annotations

import codecs
import glob
import hashlib
import locale
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__

BLACKARCH_STRAP_URL = "https://blackarch.org/strap.sh"
EXPECTED_STRAP_SHA1 = "00688950aaf5e5804d2abebb8d3d3ea1d28525ed"
PACKAGE_NAME = re.compile(r"^[a-zA-Z0-9@._+][a-zA-Z0-9@._+-]*$")
REPOSITORY_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
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
        tee: bool = False,
        timeout: int | None = None,
        mutating: bool = False,
    ) -> CommandResult:
        command = list(args)
        if self.dry_run and mutating:
            return CommandResult(command, 0, planned=True)
        try:
            if tee:
                completed = self._run_tee(command, timeout=timeout)
            else:
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

    @staticmethod
    def _run_tee(
        command: Sequence[str],
        *,
        timeout: int | None,
    ) -> subprocess.CompletedProcess[str]:
        """Capture both streams while forwarding them to the active terminal."""
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise BackendError("Unable to capture command output")

        encoding = locale.getpreferredencoding(False)
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def pump(stream, destination, chunks: list[str]) -> None:
            decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
            try:
                while data := stream.read1(4096):
                    text = decoder.decode(data)
                    chunks.append(text)
                    try:
                        destination.write(text)
                        destination.flush()
                    except (BrokenPipeError, OSError, UnicodeError):
                        pass
                tail = decoder.decode(b"", final=True)
                if tail:
                    chunks.append(tail)
                    try:
                        destination.write(tail)
                        destination.flush()
                    except (BrokenPipeError, OSError, UnicodeError):
                        pass
            finally:
                stream.close()

        threads = (
            threading.Thread(
                target=pump,
                args=(process.stdout, sys.stdout, stdout_chunks),
                daemon=True,
            ),
            threading.Thread(
                target=pump,
                args=(process.stderr, sys.stderr, stderr_chunks),
                daemon=True,
            ),
        )
        for thread in threads:
            thread.start()
        try:
            returncode = process.wait(timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            for thread in threads:
                thread.join()
        return subprocess.CompletedProcess(
            list(command),
            returncode,
            "".join(stdout_chunks),
            "".join(stderr_chunks),
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


def validate_package_targets(names: Sequence[str]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for value in names:
        if "/" in value:
            if value.count("/") != 1:
                raise BackendError(f"Unsafe or invalid package target: {value!r}")
            repository, package = value.split("/", 1)
            if not REPOSITORY_NAME.fullmatch(repository):
                raise BackendError(f"Unsafe or invalid repository name: {repository!r}")
            validate_package_names([package])
            target = f"{repository}/{package}"
        else:
            target = validate_package_names([value])[0]
        if target not in seen:
            targets.append(target)
            seen.add(target)
    if not targets:
        raise BackendError("At least one package is required")
    return targets


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

    def plan_metadata(
        self,
        operation: str,
        requested: Sequence[str],
    ) -> dict[str, object]:
        """Return read-only pacman transaction metadata when it is available."""

        names = validate_package_names(
            [value.split("/", 1)[-1] for value in requested]
        ) if requested else []
        if operation == "remove":
            installed = self.installed_packages()
            return {
                "packages": {
                    name: {
                        "name": name,
                        "installed_version": installed[name],
                    }
                    for name in names
                    if name in installed
                },
                "download_size_bytes": 0,
                "warnings": [
                    "Freed disk space is advisory; pacman decides dependency removals."
                ],
            }
        if operation == "upgrade" and not requested:
            return {
                "warnings": [
                    "A full-system upgrade is resolved by pacman immediately before execution."
                ]
            }
        self.require_supported()
        format_value = "%n\t%v\t%r\t%s"
        result = self.runner.run(
            [
                "pacman",
                "-Sp",
                "--print-format",
                format_value,
                "--",
                *requested,
            ],
            capture=True,
            timeout=180,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return {
                "warnings": [
                    f"pacman could not resolve full transaction metadata: {detail}"
                ]
            }
        packages: dict[str, object] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            name, version, repository, size_value = parts
            try:
                size = int(size_value)
            except ValueError:
                size = None
            packages[name] = {
                "name": name,
                "version": version,
                "repository": repository,
                "download_size_bytes": size,
            }
        dependencies = sorted(set(packages) - set(names))
        return {
            "packages": packages,
            "dependencies": dependencies,
            "warnings": [
                (
                    "Installed-size and conflict details are unavailable in pacman's "
                    "portable print format; pacman remains the final authority."
                )
            ],
        }

    def install(self, names: Sequence[str]) -> CommandResult:
        packages = validate_package_targets(names)
        args = ["pacman", "-S", "--needed"]
        if self.assume_yes:
            args.append("--noconfirm")
        args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True, tee=True)

    def remove(self, names: Sequence[str], *, purge: bool = False) -> CommandResult:
        packages = validate_package_names(names)
        args = ["pacman", "-Rns" if purge else "-R"]
        if self.assume_yes:
            args.append("--noconfirm")
        args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True, tee=True)

    def upgrade(self, names: Sequence[str] = ()) -> CommandResult:
        packages = validate_package_targets(names) if names else []
        args = ["pacman", "-Syu" if not packages else "-S"]
        if packages:
            args.append("--needed")
        if self.assume_yes:
            args.append("--noconfirm")
        if packages:
            args.extend(["--", *packages])
        self.require_supported()
        return self.runner.run(self._privileged(args), mutating=True, tee=True)

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
                final = urllib.parse.urlsplit(final_url)
                if (
                    final.scheme != "https"
                    or (final.hostname or "").casefold()
                    not in {"blackarch.org", "www.blackarch.org"}
                    or final.username
                    or final.password
                ):
                    raise BackendError(f"Refusing untrusted setup script: {final_url}")
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
