from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


class ProfileError(RuntimeError):
    pass


def write_profile(path: Path, name: str, packages: Iterable[str]) -> None:
    unique = sorted(set(packages))
    if not unique:
        raise ProfileError("A profile cannot be empty")
    value = {"schema_version": 1, "name": name, "packages": unique}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_profile(path: Path) -> tuple[str, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Unable to read profile {path}: {exc}") from exc
    if value.get("schema_version") != 1 or not isinstance(value.get("packages"), list):
        raise ProfileError("Unsupported or malformed profile")
    packages = [str(item) for item in value["packages"]]
    return str(value.get("name", path.stem)), packages
