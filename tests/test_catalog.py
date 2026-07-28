from __future__ import annotations

from pathlib import Path

import pytest

from blackforge.catalog import Catalog, CatalogError, parse_catalog_html
from blackforge.models import Tool


def test_realistic_parser() -> None:
    data = (Path(__file__).parent / "fixtures" / "tools.html").read_bytes()
    catalog = parse_catalog_html(data, minimum_rows=1)
    assert [tool.name for tool in catalog.tools] == ["alpha-tool", "beta-tool"]
    assert catalog.tools[0].description == "An alpha & beta analyzer."
    assert catalog.tools[0].category == "blackarch-scanner"
    assert catalog.tools[0].website == "https://example.test/alpha"
def test_search_ranks_exact_name_first() -> None:
    catalog = Catalog(
        tools=[
            Tool("nmap", "1", "network mapper", "blackarch-scanner"),
            Tool("nmap-helper", "1", "helper", "blackarch-misc"),
            Tool("other", "1", "mentions nmap", "blackarch-misc"),
        ],
        source="test",
        fetched_at="now",
    )
    assert [tool.name for tool in catalog.search("nmap")] == [
        "nmap",
        "nmap-helper",
        "other",
    ]


def test_catalog_rejects_duplicate_names() -> None:
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog(
            tools=[
                Tool("same", "1", "", "blackarch-misc"),
                Tool("same", "2", "", "blackarch-misc"),
            ],
            source="test",
            fetched_at="now",
        )


def test_bundled_catalog_is_large_and_consistent() -> None:
    from blackforge.catalog import bundled_catalog

    catalog = bundled_catalog()
    assert len(catalog.tools) >= 2500
    assert "blackarch-webapp" in catalog.categories
    assert all(tool.name and tool.category for tool in catalog.tools)
