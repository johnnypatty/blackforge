#!/usr/bin/env python3
"""Fail when release metadata or bundled datasets drift out of agreement."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MODULE = "blackforge"


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
    parser.add_argument(
        "--archive",
        type=Path,
        help="verify the reproducible source archive and its PKGBUILD checksum",
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
        ROOT / "docs" / "blackforge.1",
        ROOT / "packaging" / "systemd" / "blackforge-update.service",
        ROOT / "packaging" / "systemd" / "blackforge-update.timer",
        *sorted((ROOT / "src" / PACKAGE_MODULE).rglob("*.py")),
        *sorted((ROOT / "src" / PACKAGE_MODULE / "data").glob("*.json")),
    ]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Release source file is missing: {missing[0]}")
    return {path.relative_to(ROOT).as_posix(): path for path in paths}


def _member_bytes(
    source_archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    relative: str,
) -> bytes:
    member = source_archive.extractfile(members[relative])
    if member is None:
        raise SystemExit(f"Native Arch source archive cannot read {relative}")
    return member.read()


def _validate_archive(
    archive: Path,
    *,
    archive_prefix: str,
) -> None:
    source_files = _release_source_files()
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
                    or not (
                        name == archive_prefix or name.startswith(f"{archive_prefix}/")
                    )
                ):
                    raise SystemExit(
                        f"Native Arch source archive has an unsafe member: {name}"
                    )
            file_members = {
                name.removeprefix(f"{archive_prefix}/"): member
                for member, name in zip(members, names, strict=True)
                if member.isfile()
            }
            missing = sorted(set(source_files) - set(file_members))
            if missing:
                raise SystemExit(f"Native Arch source archive is missing {missing[0]}")
            for relative, source_path in source_files.items():
                archive_bytes = _member_bytes(
                    source_archive,
                    file_members,
                    relative,
                )
                source_bytes = source_path.read_bytes()
                if archive_bytes.replace(b"\r\n", b"\n") != source_bytes.replace(
                    b"\r\n", b"\n"
                ):
                    raise SystemExit(
                        f"Native Arch source archive is stale for {relative}"
                    )
            excluded_roots = {
                ".github",
                "community",
                "reports",
                "scripts",
                "site",
                "tests",
            }
            leaked = sorted(
                name
                for name in file_members
                if PurePosixPath(name).parts[0] in excluded_roots
            )
            if leaked:
                raise SystemExit(
                    f"Native Arch source archive includes export-ignored {leaked[0]}"
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
    from blackforge.community import bundled_community_presets
    from blackforge.maintenance import load_bundled_maintenance
    from blackforge.presets import bundled_presets
    from blackforge.sources import bundled_arch_catalog

    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_version = _project_field(project_text, "version")
    project_name = _project_field(project_text, "name")
    pkgbuild = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
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
    expanded_source = source_value.replace("${_python_name}", python_name).replace(
        "${pkgver}", __version__
    )
    archive_name = f"{python_name}-{__version__}.tar.gz"
    expected_source = (
        f"{archive_name}::${{url}}/releases/download/v{__version__}/{archive_name}"
    )
    if expanded_source != expected_source:
        raise SystemExit(
            "PKGBUILD source does not identify the native release archive: "
            f"{expanded_source!r} != {expected_source!r}"
        )
    checksum_match = re.search(
        r"(?m)^sha256sums=\('([0-9a-f]{64})'\)$",
        pkgbuild,
    )
    if checksum_match is None:
        raise SystemExit("The native Arch source archive checksum is missing")
    if args.archive is not None:
        archive = args.archive.resolve()
        if not archive.is_file() or archive.is_symlink():
            raise SystemExit(f"Release source archive is missing: {archive}")
        actual_checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_checksum != checksum_match.group(1):
            raise SystemExit(f"PKGBUILD archive checksum mismatch: {actual_checksum}")
        _validate_archive(
            archive,
            archive_prefix=f"{python_name}-{__version__}",
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
    community = bundled_community_presets()
    print(
        f"Release {__version__}: {len(catalog.tools)} BlackArch tools, "
        f"{len(maintenance.records)} maintenance records, "
        f"{len(arch.tools)} curated Arch tools, {len(presets)} collections, "
        f"{len(community)} reviewed community presets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
