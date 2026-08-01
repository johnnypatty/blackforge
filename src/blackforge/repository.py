from __future__ import annotations

import hashlib
import io
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

REPOSITORY_DB_URL = (
    "https://www.blackarch.org/blackarch/blackarch/os/x86_64/blackarch.db"
)
MAX_REPOSITORY_DB_BYTES = 128 * 1024 * 1024
MAX_DESCRIPTION_BYTES = 1024 * 1024
MAX_TOTAL_DESCRIPTION_BYTES = 128 * 1024 * 1024
MAX_REPOSITORY_MEMBERS = 20_000


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    packages: dict[str, str]
    source: str
    fetched_at: str
    last_modified: str = ""
    source_sha256: str = ""


def _field(description: str, name: str) -> str:
    match = re.search(
        rf"(?m)^%{re.escape(name)}%\n(.*?)(?:\n\n|\Z)",
        description,
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def parse_repository_database(
    data: bytes,
    *,
    source: str = REPOSITORY_DB_URL,
    last_modified: str = "",
) -> RepositorySnapshot:
    packages: dict[str, str] = {}
    total_description_bytes = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for index, member in enumerate(archive, start=1):
                if index > MAX_REPOSITORY_MEMBERS:
                    raise RepositoryError(
                        "Repository database contains too many members"
                    )
                if not member.isfile() or not member.name.endswith("/desc"):
                    continue
                if member.size > MAX_DESCRIPTION_BYTES:
                    raise RepositoryError(
                        f"Repository description is too large: {member.name}"
                    )
                total_description_bytes += member.size
                if total_description_bytes > MAX_TOTAL_DESCRIPTION_BYTES:
                    raise RepositoryError(
                        "Repository descriptions exceed the total safety size limit"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                description = extracted.read().decode("utf-8", errors="replace")
                name = _field(description, "NAME")
                version = _field(description, "VERSION")
                if name and version:
                    packages[name] = version
    except (tarfile.TarError, OSError) as exc:
        raise RepositoryError(f"Invalid BlackArch repository database: {exc}") from exc
    if len(packages) < 1000:
        raise RepositoryError(
            f"Only {len(packages)} packages were parsed; refusing incomplete repository data"
        )
    return RepositorySnapshot(
        packages=packages,
        source=source,
        fetched_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        last_modified=last_modified,
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


def download_repository_database(
    url: str = REPOSITORY_DB_URL,
    *,
    timeout: int = 90,
) -> RepositorySnapshot:
    parsed_url = urllib.parse.urlsplit(url)
    initial_host = (parsed_url.hostname or "").casefold()
    if (
        parsed_url.scheme != "https"
        or not initial_host
        or parsed_url.username
        or parsed_url.password
    ):
        raise RepositoryError(
            "Repository database URL must be HTTPS and contain no credentials"
        )
    allowed_hosts = (
        {"blackarch.org", "www.blackarch.org"}
        if initial_host in {"blackarch.org", "www.blackarch.org"}
        else {initial_host}
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"BlackForge/{__version__} repository audit"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            final = urllib.parse.urlsplit(final_url)
            if (
                final.scheme != "https"
                or (final.hostname or "").casefold() not in allowed_hosts
                or final.username
                or final.password
            ):
                raise RepositoryError(
                    f"Refusing untrusted repository database: {final_url}"
                )
            data = response.read(MAX_REPOSITORY_DB_BYTES + 1)
            last_modified = response.headers.get("Last-Modified", "")
    except (OSError, urllib.error.URLError) as exc:
        raise RepositoryError(f"Unable to download {url}: {exc}") from exc
    if len(data) > MAX_REPOSITORY_DB_BYTES:
        raise RepositoryError("Repository database exceeded the 128 MiB safety limit")
    return parse_repository_database(
        data,
        source=final_url,
        last_modified=last_modified,
    )


def read_repository_database(path: Path) -> RepositorySnapshot:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RepositoryError(f"Unable to read {path}: {exc}") from exc
    return parse_repository_database(data, source=str(path.resolve()))


def package_base_version(version: str) -> str:
    """Strip an optional epoch and numeric pkgrel for website comparison."""
    without_epoch = version.split(":", 1)[-1]
    match = re.fullmatch(r"(.+)-\d+(?:\.\d+)*", without_epoch)
    return match.group(1) if match else without_epoch
