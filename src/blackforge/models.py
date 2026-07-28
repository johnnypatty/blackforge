from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    version: str
    description: str
    category: str
    website: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Tool:
        return cls(
            name=str(value["name"]),
            version=str(value.get("version", "")),
            description=str(value.get("description", "")),
            category=str(value.get("category", "")),
            website=str(value.get("website", "")),
        )


@dataclass(frozen=True, slots=True)
class PackageState:
    name: str
    catalog_version: str
    repository_version: str | None
    installed_version: str | None
    status: str
    executables: tuple[str, ...] = ()
    missing_executables: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["executables"] = list(self.executables)
        value["missing_executables"] = list(self.missing_executables)
        return value
