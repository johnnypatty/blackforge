#!/usr/bin/env python3
"""Validate source community presets and their bundled release index."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from blackforge.community import (
    bundled_community_presets,
    read_community_preset,
)


def main() -> int:
    source = tuple(
        read_community_preset(path, require_reviewed=True)
        for path in sorted((ROOT / "community" / "presets").glob("*.json"))
    )
    if not source:
        raise SystemExit("No community presets were found")
    bundled = bundled_community_presets()
    source_value = [preset.to_dict() for preset in source]
    bundled_value = [preset.to_dict() for preset in bundled]
    if source_value != bundled_value:
        raise SystemExit("Community source presets and bundled release index differ")
    ids = [preset.id for preset in source]
    if len(ids) != len(set(ids)):
        raise SystemExit("Community presets contain duplicate IDs")
    print(f"Validated {len(source)} reviewed, data-only community presets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
