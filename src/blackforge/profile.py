from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .storage import atomic_write_json

MAX_PROFILE_BYTES = 4 * 1024 * 1024
MAX_PROFILE_PACKAGES = 10_000


class ProfileError(RuntimeError):
    pass


def write_profile(path: Path, name: str, packages: Iterable[str]) -> None:
    unique = sorted(set(packages))
    if not unique:
        raise ProfileError("A profile cannot be empty")
    if len(unique) > MAX_PROFILE_PACKAGES:
        raise ProfileError("A profile contains too many packages")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ProfileError("Profile name must be a short non-empty string")
    value = {"schema_version": 1, "name": name, "packages": unique}
    try:
        atomic_write_json(path, value)
    except OSError as exc:
        raise ProfileError(f"Unable to save profile {path}: {exc}") from exc


def read_profile(path: Path) -> tuple[str, list[str]]:
    try:
        if path.stat().st_size > MAX_PROFILE_BYTES:
            raise ProfileError("Profile exceeds its safety size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ProfileError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Unable to read profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileError("Unsupported or malformed profile")
    if set(value) != {"schema_version", "name", "packages"}:
        raise ProfileError("Unsupported or malformed profile shape")
    if value.get("schema_version") != 1 or not isinstance(value.get("packages"), list):
        raise ProfileError("Unsupported or malformed profile")
    if not value["packages"] or not all(
        isinstance(item, str) and item.strip() for item in value["packages"]
    ):
        raise ProfileError("Profile packages must be a non-empty list of package names")
    if len(value["packages"]) > MAX_PROFILE_PACKAGES:
        raise ProfileError("Profile contains too many packages")
    profile_name = value.get("name", path.stem)
    if (
        not isinstance(profile_name, str)
        or not profile_name.strip()
        or len(profile_name) > 256
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in profile_name
        )
    ):
        raise ProfileError("Profile name must be a short non-empty string")
    packages = list(dict.fromkeys(value["packages"]))
    return profile_name, packages
