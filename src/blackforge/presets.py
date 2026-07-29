from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .catalog import Catalog, bundled_catalog
from .sources import (
    ARCH_SOURCE_ID,
    ArchToolCatalog,
    SourceError,
    bundled_arch_catalog,
    resolve_arch_tool,
    validate_package_name,
)

BLACKARCH_SOURCE_ID = "blackarch"
_PRESET_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PRESET_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PresetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    name: str
    description: str
    packages: tuple[str, ...]
    categories: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _PRESET_ID.fullmatch(self.id):
            raise PresetError(f"Invalid preset ID: {self.id!r}")
        if not self.name.strip() or not self.description.strip():
            raise PresetError(f"Preset {self.id!r} needs a name and description")
        if not self.packages:
            raise PresetError(f"Preset {self.id!r} cannot be empty")
        if len(self.packages) != len(set(self.packages)):
            raise PresetError(f"Preset {self.id!r} contains duplicate package references")
        if not self.categories:
            raise PresetError(f"Preset {self.id!r} needs at least one category")
        for category in self.categories:
            if not _PRESET_CATEGORY.fullmatch(category):
                raise PresetError(
                    f"Preset {self.id!r} has invalid category {category!r}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "packages": list(self.packages),
            "categories": list(self.categories),
        }


@dataclass(frozen=True, slots=True)
class PresetPackage:
    reference: str
    source: str
    repository: str
    name: str
    package_target: str

    def to_dict(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "source": self.source,
            "repository": self.repository,
            "name": self.name,
            "package_target": self.package_target,
        }


_BUILTIN_PRESETS = (
    Preset(
        id="network-discovery",
        name="Network discovery essentials",
        description=(
            "A focused discovery and packet-inspection set from BlackArch and "
            "official Arch repositories."
        ),
        packages=(
            "arch:extra/nmap",
            "arch:extra/masscan",
            "arch:extra/tcpdump",
            "amass",
        ),
        categories=("network", "recon"),
    ),
    Preset(
        id="web-assessment",
        name="Web assessment essentials",
        description=(
            "Common discovery, template scanning, content discovery, and SQL "
            "assessment tools for authorized web testing."
        ),
        packages=(
            "amass",
            "nuclei",
            "httpx",
            "subfinder",
            "ffuf",
            "arch:extra/sqlmap",
        ),
        categories=("web", "recon"),
    ),
    Preset(
        id="wireless-audit",
        name="Wireless audit essentials",
        description=(
            "A compact 802.11 auditing set; compatible wireless hardware and "
            "explicit authorization are still required."
        ),
        packages=(
            "arch:extra/aircrack-ng",
            "airgeddon",
            "airopy",
            "eapeak",
        ),
        categories=("wireless",),
    ),
    Preset(
        id="password-audit",
        name="Password audit essentials",
        description=(
            "Password recovery, wordlist generation, and hash-identification "
            "utilities for data you are authorized to assess."
        ),
        packages=(
            "arch:extra/hashcat",
            "arch:extra/john",
            "cewl",
            "crunch",
            "hashid",
        ),
        categories=("passwords", "crypto"),
    ),
    Preset(
        id="digital-forensics",
        name="Digital forensics essentials",
        description=(
            "A small starting set for file-system examination, recovery, and "
            "artifact extraction."
        ),
        packages=(
            "autopsy",
            "scalpel",
            "bulk-extractor",
        ),
        categories=("forensics",),
    ),
    Preset(
        id="binary-analysis",
        name="Binary analysis essentials",
        description=(
            "Static and program-analysis tools for binaries and Android "
            "applications."
        ),
        packages=(
            "androguard",
            "angr",
            "detect-it-easy",
            "android-apktool",
        ),
        categories=("binary", "reverse-engineering"),
    ),
    Preset(
        id="packet-analysis",
        name="Packet analysis essentials",
        description=(
            "Official Arch command-line and graphical packet-analysis tools."
        ),
        packages=(
            "arch:extra/tcpdump",
            "arch:extra/wireshark-cli",
            "arch:extra/wireshark-qt",
        ),
        categories=("network", "packet-analysis"),
    ),
)


def _resolve_package(
    reference: str,
    *,
    blackarch: Catalog,
    arch: ArchToolCatalog,
) -> PresetPackage:
    if reference.startswith("arch:") or "/" in reference:
        try:
            tool = resolve_arch_tool(reference, catalog=arch)
        except SourceError as exc:
            raise PresetError(str(exc)) from exc
        return PresetPackage(
            reference=reference,
            source=ARCH_SOURCE_ID,
            repository=tool.repository,
            name=tool.name,
            package_target=tool.package_target,
        )

    candidate = reference.removeprefix("blackarch:")
    try:
        validate_package_name(candidate)
    except SourceError as exc:
        raise PresetError(str(exc)) from exc
    if candidate not in blackarch.by_name:
        raise PresetError(f"Unknown BlackArch package in preset: {candidate}")
    return PresetPackage(
        reference=reference,
        source=BLACKARCH_SOURCE_ID,
        repository="blackarch",
        name=candidate,
        package_target=candidate,
    )


def validate_preset(
    preset: Preset,
    *,
    blackarch: Catalog | None = None,
    arch: ArchToolCatalog | None = None,
) -> tuple[PresetPackage, ...]:
    blackarch_catalog = blackarch or bundled_catalog()
    arch_catalog = arch or bundled_arch_catalog()
    resolved = tuple(
        _resolve_package(
            reference,
            blackarch=blackarch_catalog,
            arch=arch_catalog,
        )
        for reference in preset.packages
    )
    targets = [package.package_target for package in resolved]
    if len(targets) != len(set(targets)):
        raise PresetError(
            f"Preset {preset.id!r} resolves to duplicate package targets"
        )
    return resolved


@lru_cache(maxsize=1)
def bundled_presets() -> tuple[Preset, ...]:
    ids = [preset.id for preset in _BUILTIN_PRESETS]
    if len(ids) != len(set(ids)):
        raise PresetError("Built-in presets contain duplicate IDs")
    for preset in _BUILTIN_PRESETS:
        validate_preset(preset)
    return tuple(sorted(_BUILTIN_PRESETS, key=lambda preset: preset.id))


def list_presets(*, category: str | None = None) -> list[Preset]:
    if category is not None and not _PRESET_CATEGORY.fullmatch(category):
        raise PresetError(f"Invalid preset category: {category!r}")
    return [
        preset
        for preset in bundled_presets()
        if category is None or category in preset.categories
    ]


def resolve_preset(preset_id: str) -> Preset:
    if not isinstance(preset_id, str) or not _PRESET_ID.fullmatch(preset_id):
        raise PresetError(f"Invalid preset ID: {preset_id!r}")
    for preset in bundled_presets():
        if preset.id == preset_id:
            return preset
    raise PresetError(f"Unknown built-in preset: {preset_id}")


def resolve_preset_packages(
    preset: Preset | str,
    *,
    blackarch: Catalog | None = None,
    arch: ArchToolCatalog | None = None,
) -> tuple[PresetPackage, ...]:
    selected = resolve_preset(preset) if isinstance(preset, str) else preset
    return validate_preset(selected, blackarch=blackarch, arch=arch)
