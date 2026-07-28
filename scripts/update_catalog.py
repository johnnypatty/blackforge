#!/usr/bin/env python3
"""Build a deterministic BlackForge catalog snapshot from BlackArch tools.html."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from blackforge.catalog import CATALOG_URL, download_catalog, parse_catalog_html

    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--html", type=Path, help="parse a previously downloaded page")
    source.add_argument("--url", default=CATALOG_URL, help="catalog URL")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "src" / "blackforge" / "data" / "tools.json",
    )
    parser.add_argument("--expect-minimum", type=int, default=2500)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit nonzero if tool metadata differs; do not rewrite the snapshot",
    )
    args = parser.parse_args()

    if args.html:
        catalog = parse_catalog_html(args.html.read_bytes(), source=CATALOG_URL)
    else:
        catalog = download_catalog(args.url)
    if len(catalog.tools) < args.expect_minimum:
        parser.error(
            f"parsed {len(catalog.tools)} tools, expected at least {args.expect_minimum}"
        )
    if args.check:
        from blackforge.catalog import Catalog

        existing = Catalog.read(args.output)
        current_tools = [tool.to_dict() for tool in catalog.tools]
        existing_tools = [tool.to_dict() for tool in existing.tools]
        if current_tools != existing_tools:
            print(
                f"Catalog is stale: bundled={len(existing_tools)}, live={len(current_tools)}",
                file=sys.stderr,
            )
            return 1
        print(f"Catalog is current ({len(current_tools)} tools)")
        return 0
    catalog.write(args.output)
    print(f"Wrote {len(catalog.tools)} tools to {args.output}")
    print(f"Source SHA-256: {catalog.source_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
