from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ARCH_CATALOG_SCHEMA_VERSION = 1
ARCH_SOURCE_ID = "official-arch"
ARCH_SOURCE_LABEL = "Official Arch Linux repositories (curated)"
OFFICIAL_ARCH_REPOSITORIES = frozenset({"core", "extra", "multilib"})
SUPPORTED_ARCHITECTURES = frozenset({"any", "x86_64"})

_PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9@._+:-]*$")
_REPOSITORY_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_CATEGORY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_COMMAND_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9@._+:-]*$")


class SourceError(RuntimeError):
    pass


def validate_package_name(name: str) -> str:
    """Return a safe Arch package name or raise ``SourceError``.

    Package names are ultimately passed to pacman as individual argv entries,
    never through a shell. This validation also rejects path-like or
    repository-qualified input so the repository is checked separately.
    """

    if not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name):
        raise SourceError(f"Unsafe or invalid package name: {name!r}")
    return name


def validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or not _REPOSITORY_NAME.fullmatch(repository):
        raise SourceError(f"Unsafe or invalid repository name: {repository!r}")
    if repository not in OFFICIAL_ARCH_REPOSITORIES:
        allowed = ", ".join(sorted(OFFICIAL_ARCH_REPOSITORIES))
        raise SourceError(
            f"Repository {repository!r} is not an official supported Arch repository "
            f"({allowed})"
        )
    return repository


def _validate_https_url(value: str, *, field: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise SourceError(f"{field} must be an HTTPS URL without credentials")
    return value


def _strings(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str],
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SourceError(f"{field} must be a non-empty list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise SourceError(f"Invalid {field} entry: {item!r}")
        if item not in result:
            result.append(item)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ArchTool:
    """A reviewed security tool from an official Arch Linux repository.

    This is deliberately distinct from a BlackArch catalog entry. BlackForge
    does not mirror or vendor the program; pacman installs the signed package
    from Arch's configured official repository.
    """

    name: str
    version: str
    description: str
    repository: str
    architecture: str
    website: str
    official_url: str
    categories: tuple[str, ...]
    commands: tuple[str, ...]
    source: str = ARCH_SOURCE_ID

    def __post_init__(self) -> None:
        validate_package_name(self.name)
        validate_repository(self.repository)
        if self.architecture not in SUPPORTED_ARCHITECTURES:
            raise SourceError(
                f"Unsupported package architecture: {self.architecture!r}"
            )
        if not self.version.strip():
            raise SourceError(f"Package {self.name!r} has no version")
        if not self.description.strip():
            raise SourceError(f"Package {self.name!r} has no description")
        _validate_https_url(self.website, field="website")
        _validate_https_url(self.official_url, field="official_url")
        if not self.categories:
            raise SourceError(f"Package {self.name!r} has no categories")
        for category in self.categories:
            if not _CATEGORY_NAME.fullmatch(category):
                raise SourceError(f"Invalid category: {category!r}")
        if not self.commands:
            raise SourceError(f"Package {self.name!r} has no documented commands")
        for command in self.commands:
            if not _COMMAND_NAME.fullmatch(command):
                raise SourceError(f"Invalid command name: {command!r}")
        if self.source != ARCH_SOURCE_ID:
            raise SourceError(f"Unsupported source identifier: {self.source!r}")

    @property
    def id(self) -> str:
        return f"arch:{self.repository}/{self.name}"

    @property
    def package_target(self) -> str:
        """Return the explicitly qualified pacman sync target."""

        return f"{self.repository}/{self.name}"

    @property
    def source_label(self) -> str:
        return ARCH_SOURCE_LABEL

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["categories"] = list(self.categories)
        value["commands"] = list(self.commands)
        value["id"] = self.id
        value["package_target"] = self.package_target
        value["source_label"] = self.source_label
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArchTool:
        try:
            categories = _strings(
                value.get("categories"),
                field="categories",
                pattern=_CATEGORY_NAME,
            )
            commands = _strings(
                value.get("commands"),
                field="commands",
                pattern=_COMMAND_NAME,
            )
            return cls(
                name=str(value["name"]),
                version=str(value["version"]),
                description=str(value["description"]),
                repository=str(value["repository"]),
                architecture=str(value["architecture"]),
                website=str(value["website"]),
                official_url=str(value["official_url"]),
                categories=categories,
                commands=commands,
                source=str(value.get("source", ARCH_SOURCE_ID)),
            )
        except KeyError as exc:
            raise SourceError(f"Arch tool is missing field {exc.args[0]!r}") from exc


@dataclass(frozen=True, slots=True)
class ArchToolCatalog:
    tools: tuple[ArchTool, ...]
    source: str
    fetched_at: str
    label: str = ARCH_SOURCE_LABEL

    def __post_init__(self) -> None:
        if self.label != ARCH_SOURCE_LABEL:
            raise SourceError("The curated source must retain its official Arch label")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise SourceError("Curated Arch catalog contains duplicate package names")
        ids = [tool.id for tool in self.tools]
        if len(ids) != len(set(ids)):
            raise SourceError("Curated Arch catalog contains duplicate package IDs")
        if (
            tuple(sorted(self.tools, key=lambda tool: tool.name.casefold()))
            != self.tools
        ):
            raise SourceError("Curated Arch catalog must be sorted by package name")
        _validate_https_url(self.source, field="catalog source")

    @property
    def by_name(self) -> dict[str, ArchTool]:
        return {tool.name: tool for tool in self.tools}

    @property
    def categories(self) -> dict[str, tuple[ArchTool, ...]]:
        result: dict[str, list[ArchTool]] = {}
        for tool in self.tools:
            for category in tool.categories:
                result.setdefault(category, []).append(tool)
        return {category: tuple(tools) for category, tools in sorted(result.items())}

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
    ) -> list[ArchTool]:
        if category is not None and not _CATEGORY_NAME.fullmatch(category):
            raise SourceError(f"Invalid category: {category!r}")
        candidates = self.categories.get(category, ()) if category else self.tools
        terms = [term.casefold() for term in re.findall(r"[\w.+-]+", query)]
        ranked: list[tuple[int, str, ArchTool]] = []
        for tool in candidates:
            name = tool.name.casefold()
            haystack = " ".join(
                (tool.name, tool.description, *tool.categories, *tool.commands)
            ).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(
                100
                if term == name
                else 50
                if name.startswith(term)
                else 20
                if term in name
                else 1
                for term in terms
            )
            ranked.append((-score, name, tool))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [tool for _, _, tool in ranked]

    def resolve(self, reference: str) -> ArchTool:
        repository, name = parse_arch_reference(reference)
        tool = self.by_name.get(name)
        if tool is None:
            raise SourceError(f"Unknown curated official Arch package: {name}")
        if repository is not None and repository != tool.repository:
            raise SourceError(
                f"{name} is curated from {tool.repository}, not {repository}"
            )
        return tool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArchToolCatalog:
        if value.get("schema_version") != ARCH_CATALOG_SCHEMA_VERSION:
            raise SourceError(
                f"Unsupported curated Arch catalog schema: "
                f"{value.get('schema_version')!r}"
            )
        raw_tools = value.get("tools")
        if not isinstance(raw_tools, list):
            raise SourceError("Curated Arch catalog has no tools list")
        tools = tuple(
            sorted(
                (
                    ArchTool.from_dict(item)
                    for item in raw_tools
                    if isinstance(item, dict)
                ),
                key=lambda tool: tool.name.casefold(),
            )
        )
        declared_count = value.get("tool_count")
        if declared_count is not None and int(declared_count) != len(tools):
            raise SourceError(
                f"Curated Arch catalog count mismatch: declared {declared_count}, "
                f"parsed {len(tools)}"
            )
        return cls(
            tools=tools,
            source=str(value.get("source", "")),
            fetched_at=str(value.get("fetched_at", "")),
            label=str(value.get("label", "")),
        )


def parse_arch_reference(reference: str) -> tuple[str | None, str]:
    """Parse ``name``, ``repo/name``, or ``arch:repo/name`` safely."""

    if not isinstance(reference, str) or not reference:
        raise SourceError("Package reference cannot be empty")
    candidate = reference
    if candidate.startswith("arch:"):
        candidate = candidate.removeprefix("arch:")
    elif ":" in candidate and "/" in candidate:
        raise SourceError(f"Unsupported package source in reference: {reference!r}")
    if candidate.count("/") > 1:
        raise SourceError(f"Invalid package reference: {reference!r}")
    if "/" not in candidate:
        return None, validate_package_name(candidate)
    repository, name = candidate.split("/", maxsplit=1)
    return validate_repository(repository), validate_package_name(name)


def read_arch_catalog(path: Path) -> ArchToolCatalog:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"Unable to read curated Arch catalog {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceError("Curated Arch catalog root must be an object")
    return ArchToolCatalog.from_dict(value)


@lru_cache(maxsize=1)
def bundled_arch_catalog() -> ArchToolCatalog:
    location = resources.files("blackforge").joinpath("data/arch_tools.json")
    try:
        value = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"Bundled curated Arch catalog is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceError("Bundled curated Arch catalog root must be an object")
    return ArchToolCatalog.from_dict(value)


def list_arch_tools(
    *,
    query: str = "",
    category: str | None = None,
    catalog: ArchToolCatalog | None = None,
) -> list[ArchTool]:
    return (catalog or bundled_arch_catalog()).search(query, category=category)


def resolve_arch_tool(
    reference: str,
    *,
    catalog: ArchToolCatalog | None = None,
) -> ArchTool:
    return (catalog or bundled_arch_catalog()).resolve(reference)


def resolve_arch_tools(
    references: Iterable[str],
    *,
    catalog: ArchToolCatalog | None = None,
) -> list[ArchTool]:
    source = catalog or bundled_arch_catalog()
    result: list[ArchTool] = []
    seen: set[str] = set()
    for reference in references:
        tool = source.resolve(reference)
        if tool.id not in seen:
            result.append(tool)
            seen.add(tool.id)
    if not result:
        raise SourceError("At least one official Arch package is required")
    return result


def is_curated_arch_tool(
    name: str,
    *,
    catalog: ArchToolCatalog | None = None,
) -> bool:
    try:
        validate_package_name(name)
    except SourceError:
        return False
    return name in (catalog or bundled_arch_catalog()).by_name
