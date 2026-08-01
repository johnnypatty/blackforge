#!/usr/bin/env python3
"""Check the assembled static site for broken local links and unsafe markup."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.inline_scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        for name in ("href", "src"):
            if values.get(name):
                self.links.append(str(values[name]))
        if tag == "script" and not values.get("src"):
            self.inline_scripts += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    root = args.site.resolve()
    html_files = sorted(root.rglob("*.html"))
    if not html_files:
        raise SystemExit("Site contains no HTML files")
    parsed: dict[Path, LinkParser] = {}
    for page in html_files:
        value = LinkParser()
        value.feed(page.read_text(encoding="utf-8"))
        parsed[page] = value
        if value.inline_scripts:
            raise SystemExit(f"Inline script is not allowed: {page.relative_to(root)}")
    for page, value in parsed.items():
        for raw_link in value.links:
            parts = urlsplit(raw_link)
            if parts.scheme in {"http", "https", "mailto"} or raw_link.startswith("//"):
                continue
            if parts.scheme or raw_link.startswith("javascript:"):
                raise SystemExit(f"Unsafe link in {page.relative_to(root)}: {raw_link}")
            target = (page.parent / unquote(parts.path)).resolve() if parts.path else page
            if parts.path.endswith("/") or (target.exists() and target.is_dir()):
                target = target / "index.html"
            if root != target and root not in target.parents:
                raise SystemExit(f"Link escapes site root: {raw_link}")
            if not target.is_file():
                raise SystemExit(f"Broken local link in {page.relative_to(root)}: {raw_link}")
            if parts.fragment and target.suffix == ".html":
                target_parser = parsed.get(target)
                if target_parser is None or unquote(parts.fragment) not in target_parser.ids:
                    raise SystemExit(f"Broken fragment in {page.relative_to(root)}: {raw_link}")
    print(f"Checked {len(html_files)} pages and all local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
