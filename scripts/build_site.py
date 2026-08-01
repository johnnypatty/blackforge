#!/usr/bin/env python3
"""Assemble the dependency-free GitHub Pages artifact."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT not in output.parents:
        raise SystemExit("Site output must be a dedicated directory inside the repository")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "site", output)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "docs/assets/blackforge-logo.svg", assets / "blackforge-logo.svg")
    preset_index = json.loads(
        (ROOT / "src/blackforge/data/community_presets.json").read_text(encoding="utf-8")
    )
    (output / "presets.json").write_text(
        json.dumps(preset_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    catalog = json.loads((ROOT / "src/blackforge/data/tools.json").read_text(encoding="utf-8"))
    maintenance = json.loads(
        (ROOT / "src/blackforge/data/maintenance.json").read_text(encoding="utf-8")
    )
    records = maintenance.get("records", {})
    counts = {state: 0 for state in ("current", "stale", "unknown", "archived")}
    for item in records.values():
        status = item.get("status", "unknown")
        counts[status if status in counts else "unknown"] += 1
    meta = {
        "catalog_tools": len(catalog.get("tools", [])),
        "maintenance": counts,
        "community_presets": len(preset_index.get("presets", [])),
    }
    (output / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built GitHub Pages artifact at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
