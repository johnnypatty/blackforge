from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterable
from typing import Any


def emit_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def table(headers: list[str], rows: Iterable[Iterable[object]]) -> None:
    values = [[str(cell) for cell in row] for row in rows]
    if not values:
        print("No results.")
        return
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    widths = [len(header) for header in headers]
    for row in values:
        for index, cell in enumerate(row[: len(headers)]):
            widths[index] = max(widths[index], len(cell))
    if widths and sum(widths) + 3 * (len(widths) - 1) > terminal_width:
        overflow = sum(widths) + 3 * (len(widths) - 1) - terminal_width
        description_index = next(
            (index for index, name in enumerate(headers) if name.lower() in {"description", "note"}),
            len(widths) - 1,
        )
        widths[description_index] = max(18, widths[description_index] - overflow)

    def fit(value: str, width: int) -> str:
        if len(value) <= width:
            return value.ljust(width)
        return (value[: max(1, width - 1)] + "…").ljust(width)

    print("   ".join(fit(header, widths[index]) for index, header in enumerate(headers)))
    print("   ".join("-" * width for width in widths))
    for row in values:
        print(
            "   ".join(
                fit(row[index] if index < len(row) else "", width)
                for index, width in enumerate(widths)
            )
        )


def command_preview(args: Iterable[str]) -> str:
    import shlex

    return shlex.join(list(args))


def error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
