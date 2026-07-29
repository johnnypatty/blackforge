from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Tool
from .storage import atomic_write_json

MAX_UPDATE_REPORT_BYTES = 16 * 1024 * 1024
MAX_UPDATE_ITEMS = 10_000


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VersionChange:
    name: str
    old_version: str
    new_version: str


@dataclass(frozen=True, slots=True)
class UpdateReport:
    checked_at: str
    old_count: int
    new_count: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[VersionChange, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "checked_at": self.checked_at,
            "old_count": self.old_count,
            "new_count": self.new_count,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": [asdict(item) for item in self.changed],
            "has_changes": self.has_changes,
        }

    @classmethod
    def from_dict(cls, value: object) -> UpdateReport:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise UpdateError("Unsupported or malformed update report")
        required = {
            "schema_version",
            "checked_at",
            "old_count",
            "new_count",
            "added",
            "removed",
            "changed",
        }
        if not required.issubset(value) or set(value) - (required | {"has_changes"}):
            raise UpdateError("Malformed update report shape")
        try:
            changed_value = value["changed"]
            if not isinstance(changed_value, list):
                raise TypeError("changed is not a list")
            if len(changed_value) > MAX_UPDATE_ITEMS:
                raise ValueError("changed exceeds the safety item limit")
            changed_items: list[VersionChange] = []
            for index, item in enumerate(changed_value):
                if not isinstance(item, dict) or set(item) != {
                    "name",
                    "old_version",
                    "new_version",
                }:
                    raise TypeError(f"changed[{index}] is malformed")
                changed_items.append(
                    VersionChange(
                        name=_plain_text(item["name"], f"changed[{index}].name"),
                        old_version=_plain_text(
                            item["old_version"],
                            f"changed[{index}].old_version",
                        ),
                        new_version=_plain_text(
                            item["new_version"],
                            f"changed[{index}].new_version",
                        ),
                    )
                )
            changed = tuple(changed_items)
            added = _string_tuple(value["added"])
            removed = _string_tuple(value["removed"])
            checked_at = _timestamp(value["checked_at"])
            old_count = _count(value["old_count"], "old_count")
            new_count = _count(value["new_count"], "new_count")
            report = cls(
                checked_at=checked_at,
                old_count=old_count,
                new_count=new_count,
                added=added,
                removed=removed,
                changed=changed,
            )
            declared_changes = value.get("has_changes")
            if declared_changes is not None and (
                not isinstance(declared_changes, bool)
                or declared_changes != report.has_changes
            ):
                raise ValueError("has_changes does not match the report contents")
            return report
        except (KeyError, TypeError, ValueError) as exc:
            raise UpdateError(f"Malformed update report: {exc}") from exc


def _plain_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise TypeError(f"{field} must be a short non-empty string")
    return value


def _timestamp(value: object) -> str:
    text = _plain_text(value, "checked_at")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    return text


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{field} must be a non-negative integer")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("expected a list of strings")
    if len(value) > MAX_UPDATE_ITEMS:
        raise ValueError("update list exceeds the safety item limit")
    cleaned = tuple(
        _plain_text(item, "update package name")
        for item in value
    )
    if len(cleaned) != len(set(cleaned)):
        raise ValueError("update list contains duplicates")
    return cleaned


def compare_catalogs(old: Iterable[Tool], new: Iterable[Tool]) -> UpdateReport:
    old_index = {tool.name: tool for tool in old}
    new_index = {tool.name: tool for tool in new}
    added = tuple(sorted(new_index.keys() - old_index.keys()))
    removed = tuple(sorted(old_index.keys() - new_index.keys()))
    changed = tuple(
        VersionChange(name, old_index[name].version, new_index[name].version)
        for name in sorted(old_index.keys() & new_index.keys())
        if old_index[name].version != new_index[name].version
    )
    return UpdateReport(
        checked_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        old_count=len(old_index),
        new_count=len(new_index),
        added=added,
        removed=removed,
        changed=changed,
    )


def default_report_path() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    candidate = Path(root) if root else None
    base = (
        candidate
        if candidate is not None and candidate.is_absolute()
        else Path.home() / ".local" / "state"
    )
    return base / "blackforge" / "last-update-report.json"


def save_report(report: UpdateReport, path: Path | None = None) -> Path:
    destination = path or default_report_path()
    try:
        atomic_write_json(destination, report.to_dict())
    except OSError as exc:
        raise UpdateError(f"Unable to save update report {destination}: {exc}") from exc
    return destination


def read_report(path: Path | None = None) -> UpdateReport:
    source = path or default_report_path()
    try:
        if source.stat().st_size > MAX_UPDATE_REPORT_BYTES:
            raise UpdateError("Update report exceeds its safety size limit")
        value = json.loads(source.read_text(encoding="utf-8"))
    except UpdateError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Unable to read update report {source}: {exc}") from exc
    return UpdateReport.from_dict(value)
