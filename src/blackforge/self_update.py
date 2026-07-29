from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

LATEST_RELEASE_URL = (
    "https://api.github.com/repos/johnnypatty/blackforge/releases/latest"
)
MAX_RELEASE_METADATA_BYTES = 2 * 1024 * 1024
MAX_RELEASE_ASSET_BYTES = 64 * 1024 * 1024
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
ALLOWED_METADATA_HOSTS = {"api.github.com"}
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$")
_RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
WHEEL_DISTRIBUTION = "blackforge_cli"
METADATA_DISTRIBUTION = "blackforge-cli"


class SelfUpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    size: int


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    page_url: str
    published_at: str
    assets: tuple[ReleaseAsset, ...]

    @property
    def update_available(self) -> bool:
        return _version_tuple(self.version) > _version_tuple(__version__)

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": __version__,
            "latest_version": self.version,
            "update_available": self.update_available,
            "page_url": self.page_url,
            "published_at": self.published_at,
            "assets": [
                {"name": asset.name, "url": asset.url, "size": asset.size}
                for asset in self.assets
            ],
        }


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:[-+].*)?", value.strip())
    if not match:
        return ()
    return tuple(int(item) for item in match.group(1).split("."))


def _request_bytes(
    url: str,
    *,
    limit: int,
    allowed_hosts: set[str],
) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    initial_host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or initial_host not in allowed_hosts
    ):
        raise SelfUpdateError(f"Refusing untrusted update URL: {url}")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"BlackForge/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            final_url = response.geturl()
            final = urllib.parse.urlsplit(final_url)
            final_host = (final.hostname or "").casefold()
            if (
                final.scheme != "https"
                or final.username
                or final.password
                or final_host not in allowed_hosts
            ):
                raise SelfUpdateError(
                    f"Refusing untrusted update redirect: {final_url}"
                )
            data = response.read(limit + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise SelfUpdateError(f"Unable to download update data: {exc}") from exc
    if len(data) > limit:
        raise SelfUpdateError("Update download exceeded its safety size limit")
    return data


def check_latest(url: str = LATEST_RELEASE_URL) -> ReleaseInfo:
    try:
        value = json.loads(
            _request_bytes(
                url,
                limit=MAX_RELEASE_METADATA_BYTES,
                allowed_hosts=ALLOWED_METADATA_HOSTS,
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfUpdateError(f"GitHub returned invalid release metadata: {exc}") from exc
    if not isinstance(value, dict):
        raise SelfUpdateError("GitHub returned malformed release metadata")
    tag = value.get("tag_name")
    assets_value = value.get("assets", [])
    if not isinstance(tag, str) or not _version_tuple(tag):
        raise SelfUpdateError("Latest release has no valid semantic version tag")
    assets: list[ReleaseAsset] = []
    seen_asset_names: set[str] = set()
    if isinstance(assets_value, list):
        for item in assets_value:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            asset_url = item.get("browser_download_url")
            size = item.get("size", 0)
            if isinstance(name, str) and isinstance(asset_url, str):
                if not _ASSET_NAME.fullmatch(name):
                    raise SelfUpdateError(f"Release has an unsafe asset name: {name!r}")
                if isinstance(size, bool) or not isinstance(size, int):
                    raise SelfUpdateError(f"Release asset {name!r} has an invalid size")
                parsed_size = size
                if parsed_size < 0:
                    raise SelfUpdateError(
                        f"Release asset {name!r} has an invalid size"
                    )
                parsed_asset_url = urllib.parse.urlsplit(asset_url)
                if (
                    parsed_asset_url.scheme != "https"
                    or (parsed_asset_url.hostname or "").casefold()
                    not in ALLOWED_DOWNLOAD_HOSTS
                    or parsed_asset_url.username
                    or parsed_asset_url.password
                ):
                    raise SelfUpdateError(
                        f"Release asset {name!r} has an untrusted URL"
                    )
                if name in seen_asset_names:
                    raise SelfUpdateError(f"Release repeats asset name {name!r}")
                seen_asset_names.add(name)
                assets.append(ReleaseAsset(name, asset_url, parsed_size))
    return ReleaseInfo(
        version=tag.lstrip("v"),
        page_url=str(value.get("html_url", "")),
        published_at=str(value.get("published_at", "")),
        assets=tuple(assets),
    )


def _user_install_paths() -> tuple[Path, Path]:
    data_root = os.environ.get("XDG_DATA_HOME")
    if data_root:
        base = Path(data_root)
        if not base.is_absolute():
            raise SelfUpdateError("XDG_DATA_HOME must be an absolute path")
    else:
        base = Path.home() / ".local" / "share"
    install_root = base / "blackforge"
    return install_root, install_root / "venv" / "bin" / "python"


def _validate_release_asset(asset: ReleaseAsset) -> None:
    if not _ASSET_NAME.fullmatch(asset.name):
        raise SelfUpdateError(f"Release has an unsafe asset name: {asset.name!r}")
    if Path(asset.name).name != asset.name or "/" in asset.name or "\\" in asset.name:
        raise SelfUpdateError(f"Release asset name is not a basename: {asset.name!r}")
    if isinstance(asset.size, bool) or not isinstance(asset.size, int) or asset.size < 0:
        raise SelfUpdateError(f"Release asset {asset.name!r} has an invalid size")


def _expected_wheel_name(version: str) -> str:
    if not _RELEASE_VERSION.fullmatch(version):
        raise SelfUpdateError(
            f"Self-update requires an exact three-part release version, got {version!r}"
        )
    return f"{WHEEL_DISTRIBUTION}-{version}-py3-none-any.whl"


def apply_release(release: ReleaseInfo) -> str:
    if not release.update_available:
        return f"BlackForge {__version__} is already current."
    names: set[str] = set()
    for asset in release.assets:
        _validate_release_asset(asset)
        if asset.name in names:
            raise SelfUpdateError(f"Release repeats asset name {asset.name!r}")
        names.add(asset.name)
    install_root, interpreter = _user_install_paths()
    marker = install_root / ".blackforge-install"
    if install_root.is_symlink() or marker.is_symlink():
        raise SelfUpdateError("Refusing a symbolic-link BlackForge installation")
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SelfUpdateError(
            "Self-update only supports installations created by install.sh. "
            "Use pacman/makepkg or git for this installation."
        ) from exc
    if marker_value != "blackforge-user-install-v1" or not interpreter.is_file():
        raise SelfUpdateError("The BlackForge user installation is incomplete or untrusted")
    expected_wheel_name = _expected_wheel_name(release.version)
    wheel = next(
        (
            asset
            for asset in release.assets
            if asset.name == expected_wheel_name
        ),
        None,
    )
    checksums = next(
        (asset for asset in release.assets if asset.name.upper() == "SHA256SUMS"),
        None,
    )
    if wheel is None or checksums is None:
        raise SelfUpdateError(
            "The release does not provide the exact BlackForge wheel "
            f"{expected_wheel_name!r} and SHA256SUMS"
        )
    if wheel.size > MAX_RELEASE_ASSET_BYTES:
        raise SelfUpdateError("The release wheel exceeds the safety size limit")
    if checksums.size > MAX_RELEASE_METADATA_BYTES:
        raise SelfUpdateError("SHA256SUMS exceeds the safety size limit")
    for asset in (wheel, checksums):
        host = urllib.parse.urlsplit(asset.url).hostname or ""
        if host.casefold() not in ALLOWED_DOWNLOAD_HOSTS:
            raise SelfUpdateError(f"Refusing update asset host: {host}")
    with tempfile.TemporaryDirectory(prefix="blackforge-update-") as raw_directory:
        directory = Path(raw_directory)
        wheel_path = directory / wheel.name
        if wheel_path.parent != directory:
            raise SelfUpdateError("Release wheel would escape its staging directory")
        wheel_data = _request_bytes(
            wheel.url,
            limit=MAX_RELEASE_ASSET_BYTES,
            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS,
        )
        try:
            wheel_path.write_bytes(wheel_data)
        except OSError as exc:
            raise SelfUpdateError(f"Unable to stage the release wheel: {exc}") from exc
        try:
            checksum_text = _request_bytes(
                checksums.url,
                limit=MAX_RELEASE_METADATA_BYTES,
                allowed_hosts=ALLOWED_DOWNLOAD_HOSTS,
            ).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SelfUpdateError("SHA256SUMS is not valid UTF-8") from exc
        expected = _checksum_for(checksum_text, wheel.name)
        actual = hashlib.sha256(wheel_data).hexdigest()
        if actual != expected:
            raise SelfUpdateError(
                f"Wheel checksum mismatch (expected {expected}, got {actual})"
            )
        try:
            completed = subprocess.run(
                [
                    str(interpreter),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--no-deps",
                    "--upgrade",
                    str(wheel_path),
                ],
                check=False,
                text=True,
            )
        except OSError as exc:
            raise SelfUpdateError(f"Unable to run the installed Python: {exc}") from exc
        if completed.returncode != 0:
            raise SelfUpdateError("pip could not install the verified release wheel")
        try:
            verified = subprocess.run(
                [
                    str(interpreter),
                    "-I",
                    "-c",
                    (
                        "from importlib.metadata import version; "
                        f"print(version({METADATA_DISTRIBUTION!r}), end='')"
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SelfUpdateError(
                f"Unable to verify the installed BlackForge version: {exc}"
            ) from exc
        installed_version = (verified.stdout or "").strip()
        if verified.returncode != 0 or installed_version != release.version:
            raise SelfUpdateError(
                "The installed package version could not be verified "
                f"(expected {release.version!r}, got {installed_version!r})"
            )
    return f"Updated BlackForge from {__version__} to {release.version}."


def _checksum_for(content: str, filename: str) -> str:
    matches: list[str] = []
    for line in content.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            digest = parts[0].casefold()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                matches.append(digest)
    if not matches:
        raise SelfUpdateError(f"SHA256SUMS has no valid entry for {filename}")
    if len(matches) != 1:
        raise SelfUpdateError(f"SHA256SUMS has ambiguous duplicate entries for {filename}")
    return matches[0]
