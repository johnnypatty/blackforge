from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path
from typing import ClassVar

from . import __version__
from .models import Tool
from .storage import atomic_write_json

CATALOG_URL = "https://www.blackarch.org/tools.html"
SCHEMA_VERSION = 1
MAX_CATALOG_BYTES = 32 * 1024 * 1024


class CatalogError(RuntimeError):
    pass


class _BlackArchToolsParser(HTMLParser):
    """Parse the semantic classes used by blackarch.org/tools.html."""

    _FIELDS: ClassVar[dict[str, str]] = {
        "tbl-name": "name",
        "tbl-version": "version",
        "tbl-description": "description",
        "tbl-categorie": "category",
        "tbl-homepage": "website",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tools: list[Tool] = []
        self._row: dict[str, str] = {}
        self._field: str | None = None
        self._parts: list[str] = []
        self._in_row = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "tr":
            self._in_row = True
            self._row = {}
            return
        if not self._in_row:
            return
        if tag == "td":
            classes = set(attributes.get("class", "").split())
            self._field = next(
                (field for css_class, field in self._FIELDS.items() if css_class in classes),
                None,
            )
            self._parts = []
        elif tag == "a" and self._field == "website" and attributes.get("href"):
            self._row["website"] = attributes["href"].strip()

    def handle_data(self, data: str) -> None:
        if self._field and self._field != "website":
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._field:
            if self._field != "website":
                value = " ".join("".join(self._parts).split())
                self._row[self._field] = value
            self._field = None
            self._parts = []
        elif tag == "tr" and self._in_row:
            name = self._row.get("name", "").strip()
            if name:
                description = self._row.get("description", "").strip()
                category = self._row.get("category", "").strip()
                website = self._row.get("website", "").strip()
                # A small number of upstream rows contain an unescaped "|" in
                # the description, which shifts the generated HTML columns.
                # Recover the three intended values when the delimiters remain.
                if not category.startswith("blackarch-") and website.count("|") >= 2:
                    description_tail, recovered_category, recovered_website = website.split(
                        "|", maxsplit=2
                    )
                    if recovered_category.strip().startswith("blackarch-"):
                        description = f"{description} {description_tail.strip()}".strip()
                        category = recovered_category.strip()
                        website = recovered_website.strip()
                self.tools.append(
                    Tool(
                        name=name,
                        version=self._row.get("version", "").strip(),
                        description=description,
                        category=category or "uncategorized",
                        website=website,
                    )
                )
            self._row = {}
            self._in_row = False


@dataclass(slots=True)
class Catalog:
    tools: list[Tool]
    source: str
    fetched_at: str
    source_sha256: str = ""

    def __post_init__(self) -> None:
        self.tools.sort(key=lambda item: item.name.casefold())
        names = [tool.name.casefold() for tool in self.tools]
        if len(names) != len(set(names)):
            raise CatalogError("Catalog contains duplicate package names")

    @property
    def by_name(self) -> dict[str, Tool]:
        return {tool.name: tool for tool in self.tools}

    @property
    def categories(self) -> dict[str, list[Tool]]:
        result: dict[str, list[Tool]] = {}
        for tool in self.tools:
            result.setdefault(tool.category, []).append(tool)
        return dict(sorted(result.items()))

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int | None = 50,
    ) -> list[Tool]:
        terms = [term.casefold() for term in re.findall(r"[\w.+-]+", query)]
        candidates = (
            self.categories.get(category, []) if category else self.tools
        )
        ranked: list[tuple[int, str, Tool]] = []
        for tool in candidates:
            name = tool.name.casefold()
            haystack = f"{tool.name} {tool.description} {tool.category}".casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(
                100 if term == name else 50 if name.startswith(term) else 20 if term in name else 1
                for term in terms
            )
            ranked.append((-score, name, tool))
        ranked.sort(key=lambda value: (value[0], value[1]))
        values = [tool for _, _, tool in ranked]
        return values if limit is None else values[:limit]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "source_sha256": self.source_sha256,
            "tool_count": len(self.tools),
            "tools": [tool.to_dict() for tool in self.tools],
        }

    def write(self, path: Path) -> None:
        try:
            atomic_write_json(path, self.to_dict())
        except OSError as exc:
            raise CatalogError(f"Unable to save catalog {path}: {exc}") from exc

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Catalog:
        schema = value.get("schema_version")
        if schema != SCHEMA_VERSION:
            raise CatalogError(f"Unsupported catalog schema: {schema!r}")
        tools_value = value.get("tools")
        if not isinstance(tools_value, list):
            raise CatalogError("Catalog has no tools list")
        tools: list[Tool] = []
        for index, item in enumerate(tools_value):
            if not isinstance(item, dict):
                raise CatalogError(f"Catalog tool at index {index} is not an object")
            try:
                tool = Tool.from_dict(item)
            except (KeyError, TypeError, ValueError) as exc:
                raise CatalogError(f"Malformed catalog tool at index {index}: {exc}") from exc
            if not tool.name.strip() or not tool.category.strip():
                raise CatalogError(
                    f"Catalog tool at index {index} requires a name and category"
                )
            tools.append(tool)
        catalog = cls(
            tools=tools,
            source=str(value.get("source", "")),
            fetched_at=str(value.get("fetched_at", "")),
            source_sha256=str(value.get("source_sha256", "")),
        )
        declared_count = value.get("tool_count")
        if declared_count is not None:
            try:
                count = int(declared_count)
            except (TypeError, ValueError) as exc:
                raise CatalogError(f"Invalid catalog tool count: {declared_count!r}") from exc
            if count != len(catalog.tools):
                raise CatalogError(
                    f"Catalog count mismatch: declared {declared_count}, "
                    f"parsed {len(catalog.tools)}"
                )
        return catalog

    @classmethod
    def read(cls, path: Path) -> Catalog:
        try:
            if path.stat().st_size > MAX_CATALOG_BYTES:
                raise CatalogError("Catalog file exceeds its safety size limit")
            value = json.loads(path.read_text(encoding="utf-8"))
        except CatalogError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"Unable to read catalog {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise CatalogError("Catalog root must be an object")
        return cls.from_dict(value)


def parse_catalog_html(
    data: bytes,
    *,
    source: str = CATALOG_URL,
    minimum_rows: int = 100,
) -> Catalog:
    parser = _BlackArchToolsParser()
    try:
        parser.feed(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CatalogError("BlackArch catalog is not valid UTF-8") from exc
    if len(parser.tools) < minimum_rows:
        raise CatalogError(
            f"Only {len(parser.tools)} rows were parsed; refusing to replace the catalog"
        )
    return Catalog(
        tools=parser.tools,
        source=source,
        fetched_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


def download_catalog(
    url: str = CATALOG_URL,
    *,
    timeout: int = 45,
    context: ssl.SSLContext | None = None,
) -> Catalog:
    parsed_url = urllib.parse.urlsplit(url)
    initial_host = (parsed_url.hostname or "").casefold()
    if (
        parsed_url.scheme != "https"
        or not initial_host
        or parsed_url.username
        or parsed_url.password
    ):
        raise CatalogError("Catalog URL must be HTTPS and contain no credentials")
    allowed_hosts = (
        {"blackarch.org", "www.blackarch.org"}
        if initial_host in {"blackarch.org", "www.blackarch.org"}
        else {initial_host}
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"BlackForge/{__version__} (+https://www.blackarch.org/)"
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            final_url = response.geturl()
            final = urllib.parse.urlsplit(final_url)
            if (
                final.scheme != "https"
                or (final.hostname or "").casefold() not in allowed_hosts
                or final.username
                or final.password
            ):
                raise CatalogError(f"Refusing untrusted catalog response: {final_url}")
            data = response.read(MAX_CATALOG_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise CatalogError(f"Unable to download {url}: {exc}") from exc
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("BlackArch catalog exceeded the 32 MiB safety limit")
    return parse_catalog_html(data, source=final_url)


def bundled_catalog() -> Catalog:
    location = resources.files("blackforge").joinpath("data/tools.json")
    try:
        value = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Bundled catalog is unreadable: {exc}") from exc
    return Catalog.from_dict(value)


def default_cache_path() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    candidate = Path(root) if root else None
    base = (
        candidate
        if candidate is not None and candidate.is_absolute()
        else Path.home() / ".cache"
    )
    return base / "blackforge" / "tools.json"


def load_catalog(path: Path | None = None) -> Catalog:
    if path:
        return Catalog.read(path)
    cache = default_cache_path()
    if cache.exists():
        try:
            return Catalog.read(cache)
        except CatalogError:
            pass
    return bundled_catalog()


def resolve_names(catalog: Catalog, names: Iterable[str]) -> list[Tool]:
    index = catalog.by_name
    result: list[Tool] = []
    unknown: list[str] = []
    for name in names:
        if name in index:
            result.append(index[name])
        else:
            unknown.append(name)
    if unknown:
        raise CatalogError("Unknown BlackArch package(s): " + ", ".join(unknown))
    return result
