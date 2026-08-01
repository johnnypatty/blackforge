"""Generate a portable, script-free maintenance dashboard."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .catalog import Catalog
from .maintenance import MaintenanceSnapshot, MaintenanceStatus
from .storage import atomic_write_json, atomic_write_text


class DashboardError(RuntimeError):
    pass


def _read_history(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardError(f"Unable to read dashboard history {path}: {exc}") from exc
    if not isinstance(value, list):
        raise DashboardError("Dashboard history must be a JSON list")
    return [item for item in value if isinstance(item, dict)][-364:]


def build_dashboard(
    path: Path,
    catalog: Catalog,
    maintenance: MaintenanceSnapshot,
    *,
    history_path: Path | None = None,
    record: bool = False,
    write: bool = True,
    available_count: int | None = None,
) -> dict[str, object]:
    counts = {status.value: 0 for status in MaintenanceStatus}
    for tool in catalog.tools:
        counts[maintenance.for_tool(tool.name).status.value] += 1
    observation: dict[str, object] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "catalog": len(catalog.tools),
        "available": available_count,
        **counts,
    }
    history = _read_history(history_path) if history_path else []
    previous_catalog = (
        int(history[-1].get("catalog", len(catalog.tools)))
        if history
        else len(catalog.tools)
    )
    observation["added_since_previous"] = max(
        0, len(catalog.tools) - previous_catalog
    )
    observation["removed_since_previous"] = max(
        0, previous_catalog - len(catalog.tools)
    )
    if record:
        if history_path is None:
            raise DashboardError("Recording requires a history path")
        history.append(observation)
        history = history[-365:]
        atomic_write_json(history_path, history)
    points = history or [observation]
    max_catalog = max(int(item.get("catalog", 0)) for item in points) or 1
    bars = "".join(
        f'<div class="bar" style="height:{max(4, int(int(item.get("catalog", 0)) / max_catalog * 100))}%" title="{html.escape(str(item.get("at", "")))}: {int(item.get("catalog", 0))}"></div>'
        for item in points[-60:]
    )
    generated = html.escape(str(observation["at"]))
    card_values: list[tuple[str, object]] = [
        ("catalog tools", len(catalog.tools)),
        (
            "repository available",
            available_count if available_count is not None else "not checked",
        ),
        ("added since previous", observation["added_since_previous"]),
        ("removed since previous", observation["removed_since_previous"]),
        *counts.items(),
    ]
    cards = "".join(
        f"<article><strong>{html.escape(f'{count:,}' if isinstance(count, int) else str(count))}</strong>"
        f"<span>{html.escape(label.replace('_', ' ').title())}</span></article>"
        for label, count in card_values
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BlackForge maintenance report</title><style>
:root{{--bg:#090b10;--panel:#121722;--text:#f3f7ff;--muted:#97a6ba;--accent:#f6b73c;--line:#263044}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px system-ui,sans-serif}}main{{max-width:1040px;margin:auto;padding:64px 24px}}h1{{font-size:clamp(2rem,6vw,4rem);margin:.2em 0}}p{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:32px 0}}article{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}}article strong{{display:block;color:var(--accent);font-size:2rem}}article span{{color:var(--muted)}}.chart{{height:180px;display:flex;align-items:end;gap:3px;background:var(--panel);border:1px solid var(--line);padding:20px;border-radius:14px}}.bar{{min-width:6px;flex:1;background:linear-gradient(var(--accent),#945e00);border-radius:3px 3px 0 0}}code{{color:var(--accent)}}
</style></head><body><main><p>BLACKFORGE / PORTABLE REPORT</p><h1>Maintenance dashboard</h1><p>Generated {generated}. Maintenance evidence describes upstream activity, not runtime compatibility.</p><section class="cards">{cards}</section><h2>Catalog observations</h2><div class="chart" role="img" aria-label="Catalog size over recorded observations">{bars}</div><p>Keep history with <code>blackforge dashboard build report.html --record</code>.</p></main></body></html>"""
    if write:
        atomic_write_text(path, document)
    return {
        "output": str(path),
        "observation": observation,
        "history_points": len(points),
    }
