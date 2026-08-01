"""Strict, data-only community collection support."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .presets import Preset, PresetError, PresetPackage, validate_preset

COMMUNITY_SCHEMA_VERSION = 1
MAX_PRESET_BYTES = 64 * 1024
MAX_PACKAGES = 64
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_AUTHOR = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_LICENSES = frozenset({"CC0-1.0", "MIT"})


class CommunityPresetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommunityPreset:
    id: str
    name: str
    description: str
    authors: tuple[str, ...]
    tags: tuple[str, ...]
    packages: tuple[str, ...]
    license: str
    reviewed: bool = False

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.id):
            raise CommunityPresetError(f"Invalid community preset ID: {self.id!r}")
        if not self.name.strip() or len(self.name) > 80:
            raise CommunityPresetError(f"Preset {self.id!r} needs a short name")
        if not self.description.strip() or len(self.description) > 500:
            raise CommunityPresetError(f"Preset {self.id!r} needs a short description")
        if not self.authors or any(
            not _AUTHOR.fullmatch(value) for value in self.authors
        ):
            raise CommunityPresetError(
                f"Preset {self.id!r} authors must be GitHub handles such as @octocat"
            )
        if not self.tags or any(not _TAG.fullmatch(value) for value in self.tags):
            raise CommunityPresetError(f"Preset {self.id!r} has invalid tags")
        if not self.packages or len(self.packages) > MAX_PACKAGES:
            raise CommunityPresetError(
                f"Preset {self.id!r} must contain 1 to {MAX_PACKAGES} packages"
            )
        if len(self.packages) != len(set(self.packages)):
            raise CommunityPresetError(
                f"Preset {self.id!r} contains duplicate packages"
            )
        for reference in self.packages:
            if not (
                reference.startswith("blackarch:")
                or re.fullmatch(
                    r"arch:[a-z0-9][a-z0-9._-]*/[A-Za-z0-9@._+-]+", reference
                )
            ):
                raise CommunityPresetError(
                    f"Preset {self.id!r} contains an unqualified package: {reference!r}"
                )
        if self.license not in _LICENSES:
            raise CommunityPresetError(
                f"Preset {self.id!r} license must be one of {sorted(_LICENSES)}"
            )

    def as_preset(self) -> Preset:
        return Preset(
            id=self.id,
            name=self.name,
            description=self.description,
            packages=self.packages,
            categories=self.tags,
        )

    def resolved_packages(self) -> tuple[PresetPackage, ...]:
        try:
            return validate_preset(self.as_preset())
        except PresetError as exc:
            raise CommunityPresetError(str(exc)) from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": COMMUNITY_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "authors": list(self.authors),
            "tags": list(self.tags),
            "packages": list(self.packages),
            "license": self.license,
            "reviewed": self.reviewed,
        }

    @classmethod
    def from_dict(
        cls, value: Any, *, require_reviewed: bool = False
    ) -> CommunityPreset:
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != COMMUNITY_SCHEMA_VERSION
        ):
            raise CommunityPresetError("Unsupported or missing community preset schema")
        allowed = {
            "schema_version",
            "id",
            "name",
            "description",
            "authors",
            "tags",
            "packages",
            "license",
            "reviewed",
        }
        unexpected = sorted(set(value) - allowed)
        if unexpected:
            raise CommunityPresetError(
                f"Community presets are data-only; unsupported fields: {unexpected}"
            )
        try:
            preset = cls(
                id=str(value["id"]),
                name=str(value["name"]),
                description=str(value["description"]),
                authors=tuple(value["authors"]),
                tags=tuple(value["tags"]),
                packages=tuple(value["packages"]),
                license=str(value["license"]),
                reviewed=value.get("reviewed") is True,
            )
        except (KeyError, TypeError) as exc:
            raise CommunityPresetError(f"Malformed community preset: {exc}") from exc
        if require_reviewed and not preset.reviewed:
            raise CommunityPresetError(f"Preset {preset.id!r} is not release-reviewed")
        preset.resolved_packages()
        return preset


def read_community_preset(
    path: Path, *, require_reviewed: bool = False
) -> CommunityPreset:
    try:
        if path.stat().st_size > MAX_PRESET_BYTES:
            raise CommunityPresetError(f"Preset exceeds {MAX_PRESET_BYTES} bytes")
        value = json.loads(path.read_text(encoding="utf-8"))
    except CommunityPresetError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityPresetError(
            f"Unable to read community preset {path}: {exc}"
        ) from exc
    return CommunityPreset.from_dict(value, require_reviewed=require_reviewed)


def bundled_community_presets() -> tuple[CommunityPreset, ...]:
    location = resources.files("blackforge").joinpath("data/community_presets.json")
    try:
        value = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityPresetError(
            f"Bundled community presets are unreadable: {exc}"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != COMMUNITY_SCHEMA_VERSION
    ):
        raise CommunityPresetError("Unsupported bundled community preset index")
    raw_presets = value.get("presets")
    if not isinstance(raw_presets, list):
        raise CommunityPresetError("Bundled community preset index has no presets list")
    presets = tuple(
        CommunityPreset.from_dict(item, require_reviewed=True) for item in raw_presets
    )
    ids = [preset.id for preset in presets]
    if len(ids) != len(set(ids)):
        raise CommunityPresetError("Bundled community presets contain duplicate IDs")
    return tuple(sorted(presets, key=lambda item: item.id))


def resolve_community_preset(preset_id: str) -> CommunityPreset:
    if not isinstance(preset_id, str) or not _ID.fullmatch(preset_id):
        raise CommunityPresetError(f"Invalid community preset ID: {preset_id!r}")
    for preset in bundled_community_presets():
        if preset.id == preset_id:
            return preset
    raise CommunityPresetError(f"Unknown reviewed community preset: {preset_id}")
