from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .storage import atomic_write_json


class ProfileError(RuntimeError):
    pass


def write_profile(path: Path, name: str, packages: Iterable[str]) -> None:
    unique = sorted(set(packages))
    if not unique:
        raise ProfileError("A profile cannot be empty")
    value = {"schema_version": 1, "name": name, "packages": unique}
    atomic_write_json(path, value)


def read_profile(path: Path) -> tuple[str, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Unable to read profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileError("Unsupported or malformed profile")
    if value.get("schema_version") != 1 or not isinstance(value.get("packages"), list):
        raise ProfileError("Unsupported or malformed profile")
    if not value["packages"] or not all(
        isinstance(item, str) and item.strip() for item in value["packages"]
    ):
        raise ProfileError("Profile packages must be a non-empty list of package names")
    profile_name = value.get("name", path.stem)
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ProfileError("Profile name must be a non-empty string")
    packages = list(dict.fromkeys(value["packages"]))
    return profile_name, packages
