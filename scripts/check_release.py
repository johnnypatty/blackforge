#!/usr/bin/env python3
"""Fail when release metadata or bundled datasets drift out of agreement."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import os
import re
import sys
import tarfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MODULE = "blackforge"
GENERATED_SDIST_FILES = frozenset(
    {
        "PKG-INFO",
        "setup.cfg",
        "src/blackforge_cli.egg-info/PKG-INFO",
        "src/blackforge_cli.egg-info/SOURCES.txt",
        "src/blackforge_cli.egg-info/dependency_links.txt",
        "src/blackforge_cli.egg-info/entry_points.txt",
        "src/blackforge_cli.egg-info/top_level.txt",
    }
)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    github_tag = (
        os.environ.get("GITHUB_REF_NAME")
        if os.environ.get("GITHUB_REF_TYPE") == "tag"
        else None
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default=github_tag,
        help="release tag to bind to the package version (for example v0.3.0)",
    )
    return parser.parse_args(argv)


def _project_field(project_text: str, field: str) -> str:
    match = re.search(
        rf'(?ms)^\[project\]\s+.*?^{re.escape(field)}\s*=\s*"([^"]+)"',
        project_text,
    )
    return match.group(1) if match else ""


def _shell_scalar(text: str, name: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(name)}=(?:'([^']*)'|\"([^\"]*)\"|([^\s#]+))\s*$",
        text,
    )
    if match is None:
        return ""
    return next((value for value in match.groups() if value is not None), "")


def _source_value(pkgbuild: str) -> str:
    match = re.search(
        r"""(?mx)^source=\(\s*(?:'([^']+)'|"([^"]+)"|([^\s)]+))\s*\)$""",
        pkgbuild,
    )
    if match is None:
        return ""
    return next((value for value in match.groups() if value is not None), "")


def _release_source_files() -> dict[str, Path]:
    paths = [
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        *sorted((ROOT / "src" / PACKAGE_MODULE).rglob("*.py")),
        *sorted((ROOT / "src" / PACKAGE_MODULE / "data").glob("*.json")),
        *sorted((ROOT / "tests").glob("test_*.py")),
    ]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Release source file is missing: {missing[0]}")
    return {
        path.relative_to(ROOT).as_posix(): path
        for path in paths
    }


def _expected_archive_members(
    archive_prefix: str,
    source_files: dict[str, Path],
) -> set[str]:
    files = set(source_files) | set(GENERATED_SDIST_FILES)
    directories = {""}
    for filename in files:
        parent = PurePosixPath(filename).parent
        while str(parent) != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    return {
        archive_prefix if not directory else f"{archive_prefix}/{directory}"
        for directory in directories
    } | {
        f"{archive_prefix}/{filename}"
        for filename in files
    }


def _member_bytes(
    source_archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    relative: str,
) -> bytes:
    member = source_archive.extractfile(members[relative])
    if member is None:
        raise SystemExit(f"Native Arch source archive cannot read {relative}")
    return member.read()


def _validate_generated_metadata(
    source_archive: tarfile.TarFile,
    file_members: dict[str, tarfile.TarInfo],
    *,
    project_name: str,
    version: str,
) -> None:
    top_metadata = _member_bytes(source_archive, file_members, "PKG-INFO")
    egg_metadata = _member_bytes(
        source_archive,
        file_members,
        "src/blackforge_cli.egg-info/PKG-INFO",
    )
    if top_metadata != egg_metadata:
        raise SystemExit("Native Arch source archive has inconsistent PKG-INFO files")
    metadata = BytesParser().parsebytes(top_metadata)
    if metadata.get("Name") != project_name or metadata.get("Version") != version:
        raise SystemExit(
            "Native Arch source archive has the wrong distribution identity"
        )

    setup = configparser.ConfigParser(interpolation=None)
    try:
        setup.read_string(
            _member_bytes(source_archive, file_members, "setup.cfg").decode("utf-8")
        )
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise SystemExit(f"Native Arch setup.cfg is malformed: {exc}") from exc
    if setup.sections() != ["egg_info"] or dict(setup["egg_info"]) != {
        "tag_build": "",
        "tag_date": "0",
    }:
        raise SystemExit("Native Arch setup.cfg contains unexpected build settings")

    entry_points = configparser.ConfigParser(interpolation=None)
    try:
        entry_points.read_string(
            _member_bytes(
                source_archive,
                file_members,
                "src/blackforge_cli.egg-info/entry_points.txt",
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise SystemExit(f"Native Arch entry points are malformed: {exc}") from exc
    if entry_points.sections() != ["console_scripts"] or dict(
        entry_points["console_scripts"]
    ) != {"blackforge": "blackforge.cli:main"}:
        raise SystemExit("Native Arch source archive has unexpected entry points")

    dependency_links = _member_bytes(
        source_archive,
        file_members,
        "src/blackforge_cli.egg-info/dependency_links.txt",
    )
    if dependency_links.strip():
        raise SystemExit("Native Arch source archive has unexpected dependency links")
    top_level = _member_bytes(
        source_archive,
        file_members,
        "src/blackforge_cli.egg-info/top_level.txt",
    )
    if top_level.decode("utf-8").splitlines() != [PACKAGE_MODULE]:
        raise SystemExit("Native Arch source archive has the wrong import package")

    sources_text = _member_bytes(
        source_archive,
        file_members,
        "src/blackforge_cli.egg-info/SOURCES.txt",
    )
    try:
        sources = sources_text.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SystemExit("Native Arch SOURCES.txt is not valid UTF-8") from exc
    expected_sources = (
        set(_release_source_files())
        | {
            value
            for value in GENERATED_SDIST_FILES
            if value.startswith("src/blackforge_cli.egg-info/")
        }
    )
    if len(sources) != len(set(sources)) or set(sources) != expected_sources:
        missing = sorted(expected_sources - set(sources))
        extra = sorted(set(sources) - expected_sources)
        raise SystemExit(
            "Native Arch SOURCES.txt does not describe the exact release: "
            f"missing={missing[:5]!r}, extra={extra[:5]!r}"
        )


def _validate_archive(
    archive: Path,
    *,
    archive_prefix: str,
    project_name: str,
    version: str,
) -> None:
    source_files = _release_source_files()
    expected_members = _expected_archive_members(archive_prefix, source_files)
    try:
        with tarfile.open(archive, mode="r:gz") as source_archive:
            members = source_archive.getmembers()
            names = [member.name.rstrip("/") for member in members]
            if len(names) != len(set(names)):
                raise SystemExit("Native Arch source archive has duplicate members")
            for member, name in zip(members, names, strict=True):
                path = PurePosixPath(name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise SystemExit(
                        f"Native Arch source archive has an unsafe member: {name}"
                    )
            actual_members = set(names)
            if actual_members != expected_members:
                missing = sorted(expected_members - actual_members)
                extra = sorted(actual_members - expected_members)
                raise SystemExit(
                    "Native Arch source archive member set is stale: "
                    f"missing={missing[:5]!r}, extra={extra[:5]!r}"
                )
            file_members = {
                name.removeprefix(f"{archive_prefix}/"): member
                for member, name in zip(members, names, strict=True)
                if member.isfile()
            }
            for relative, source_path in source_files.items():
                if _member_bytes(
                    source_archive,
                    file_members,
                    relative,
                ) != source_path.read_bytes():
                    raise SystemExit(
                        f"Native Arch source archive is stale for {relative}"
                    )
            _validate_generated_metadata(
                source_archive,
                file_members,
                project_name=project_name,
                version=version,
            )
    except tarfile.TarError as exc:
        raise SystemExit(f"Native Arch source archive is invalid: {exc}") from exc


def _validate_tag(tag: str | None, version: str) -> None:
    if tag is None:
        return
    expected = f"v{version}"
    if tag != expected:
        raise SystemExit(
            f"Release tag {tag!r} does not match package version {version!r}; "
            f"expected {expected!r}"
        )
    notes = ROOT / "docs" / "releases" / f"{tag}.md"
    if not notes.is_file() or not notes.read_text(encoding="utf-8").strip():
        raise SystemExit(f"Release notes are missing or empty: {notes}")


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    sys.path.insert(0, str(ROOT / "src"))

    from blackforge import __version__
    from blackforge.catalog import bundled_catalog
    from blackforge.maintenance import load_bundled_maintenance
    from blackforge.presets import bundled_presets
    from blackforge.sources import bundled_arch_catalog

    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_version = _project_field(project_text, "version")
    project_name = _project_field(project_text, "name")
    pkgbuild = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text(
        encoding="utf-8"
    )
    pkgbuild_version = _shell_scalar(pkgbuild, "pkgver")
    python_name = _shell_scalar(pkgbuild, "_python_name")
    versions = {
        "Python package": __version__,
        "pyproject.toml": project_version,
        "PKGBUILD": pkgbuild_version,
    }
    if len(set(versions.values())) != 1:
        for source, version in versions.items():
            print(f"{source}: {version}", file=sys.stderr)
        raise SystemExit("Release versions do not match")
    expected_python_name = re.sub(r"[-_.]+", "_", project_name).casefold()
    if not project_name or python_name != expected_python_name:
        raise SystemExit(
            "PKGBUILD _python_name does not match the Python distribution: "
            f"{python_name!r} != {expected_python_name!r}"
        )
    _validate_tag(args.tag, __version__)

    source_value = _source_value(pkgbuild)
    expanded_source = (
        source_value.replace("${_python_name}", python_name)
        .replace("${pkgver}", __version__)
    )
    archive_name = f"{python_name}-{__version__}-final.tar.gz"
    if expanded_source != archive_name:
        raise SystemExit(
            "PKGBUILD source does not identify the native release archive: "
            f"{expanded_source!r} != {archive_name!r}"
        )
    checksum_match = re.search(
        r"(?m)^sha256sums=\('([0-9a-f]{64})'\)$",
        pkgbuild,
    )
    archive = ROOT / "packaging" / "arch" / archive_name
    if (
        checksum_match is None
        or not archive.is_file()
        or archive.is_symlink()
    ):
        raise SystemExit("The native Arch source archive/checksum is missing")
    actual_checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual_checksum != checksum_match.group(1):
        raise SystemExit(
            f"PKGBUILD archive checksum mismatch: {actual_checksum}"
        )
    _validate_archive(
        archive,
        archive_prefix=f"{python_name}-{__version__}",
        project_name=project_name,
        version=__version__,
    )

    catalog = bundled_catalog()
    maintenance = load_bundled_maintenance(required=True)
    catalog_names = set(catalog.by_name)
    maintenance_names = set(maintenance.records)
    if catalog_names != maintenance_names:
        missing = sorted(catalog_names - maintenance_names)
        extra = sorted(maintenance_names - catalog_names)
        raise SystemExit(
            "Maintenance/catalog mismatch: "
            f"missing={missing[:10]!r}, extra={extra[:10]!r}"
        )

    arch = bundled_arch_catalog()
    presets = bundled_presets()
    print(
        f"Release {__version__}: {len(catalog.tools)} BlackArch tools, "
        f"{len(maintenance.records)} maintenance records, "
        f"{len(arch.tools)} curated Arch tools, {len(presets)} collections"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
