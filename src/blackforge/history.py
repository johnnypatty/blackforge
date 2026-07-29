from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .environment import EnvironmentFileError, PackageRef
from .state_lock import StateLockError, exclusive_state_lock
from .storage import atomic_write_json

HISTORY_SCHEMA_VERSION = 1
MAX_HISTORY_RECORDS = 10_000
MAX_HISTORY_PACKAGES = 10_000
MAX_HISTORY_FILE_BYTES = 32 * 1024 * 1024
HISTORY_ACTIONS = frozenset(
    {
        "environment-import",
        "install",
        "remove",
        "setup",
        "sync",
        "undo",
        "upgrade",
    }
)
CHANGE_ACTIONS = frozenset({"install", "remove", "reinstall", "unchanged", "upgrade"})
HISTORY_OUTCOMES = frozenset({"completed", "failed"})


class HistoryError(RuntimeError):
    pass


def default_state_dir() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    candidate = Path(configured) if configured else None
    root = (
        candidate
        if candidate is not None and candidate.is_absolute()
        else Path.home() / ".local" / "state"
    )
    return root / "blackforge"


def default_history_path() -> Path:
    return default_state_dir() / "history.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_text(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoryError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise HistoryError(f"{field} exceeds the {maximum}-character limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise HistoryError(f"{field} contains control characters")
    return value


def _validate_timestamp(value: object) -> str:
    text = _validate_text(value, "created_at", maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoryError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise HistoryError("created_at must include a timezone")
    return text


def _optional_version(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field, maximum=256)


def _parse_ref(value: object, field: str = "ref") -> PackageRef:
    try:
        return PackageRef.parse(value)
    except EnvironmentFileError as exc:
        raise HistoryError(f"Invalid {field}: {exc}") from exc


def _validate_action(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise HistoryError(f"{field} must be one of: {choices}")
    return value


@dataclass(frozen=True, slots=True)
class PackageChange:
    ref: PackageRef
    action: str
    before_version: str | None
    after_version: str | None

    def __post_init__(self) -> None:
        _validate_action(self.action, CHANGE_ACTIONS, "Package change action")
        _optional_version(self.before_version, "before_version")
        _optional_version(self.after_version, "after_version")
        if self.before_version is None and self.after_version is None:
            raise HistoryError("Package change cannot have two absent versions")
        if self.action == "install" and self.after_version is None:
            raise HistoryError("Install history requires an after_version")
        if self.action == "remove" and (
            self.before_version is None or self.after_version is not None
        ):
            raise HistoryError("Remove history requires a before_version and no after_version")
        if self.action in {"upgrade", "reinstall"} and (
            self.before_version is None or self.after_version is None
        ):
            raise HistoryError(f"{self.action} history requires before and after versions")
        if self.action == "unchanged" and self.before_version != self.after_version:
            raise HistoryError("Unchanged history requires equal before and after versions")

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref.qualified,
            "action": self.action,
            "before_version": self.before_version,
            "after_version": self.after_version,
        }

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> PackageChange:
        if not isinstance(value, dict):
            raise HistoryError(f"History package at index {index} is not an object")
        required = {"ref", "action", "before_version", "after_version"}
        if set(value) != required:
            missing = required - set(value)
            unknown = set(value) - required
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if unknown:
                details.append("unknown " + ", ".join(sorted(unknown)))
            raise HistoryError(
                f"Malformed history package at index {index}: {'; '.join(details)}"
            )
        return cls(
            ref=_parse_ref(value["ref"], f"packages[{index}].ref"),
            action=_validate_action(
                value["action"], CHANGE_ACTIONS, f"packages[{index}].action"
            ),
            before_version=_optional_version(
                value["before_version"], f"packages[{index}].before_version"
            ),
            after_version=_optional_version(
                value["after_version"], f"packages[{index}].after_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    transaction_id: str
    created_at: str
    action: str
    outcome: str
    packages: tuple[PackageChange, ...]

    def __post_init__(self) -> None:
        _validate_text(self.transaction_id, "transaction_id", maximum=128)
        _validate_timestamp(self.created_at)
        _validate_action(self.action, HISTORY_ACTIONS, "History action")
        _validate_action(self.outcome, HISTORY_OUTCOMES, "History outcome")
        if len(self.packages) > MAX_HISTORY_PACKAGES:
            raise HistoryError(
                f"History record exceeds the {MAX_HISTORY_PACKAGES}-package limit"
            )
        refs = [change.ref.qualified for change in self.packages]
        if len(refs) != len(set(refs)):
            raise HistoryError("History record contains duplicate package references")

    @property
    def package_names(self) -> tuple[str, ...]:
        return tuple(change.ref.name for change in self.packages)

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "created_at": self.created_at,
            "action": self.action,
            "outcome": self.outcome,
            "packages": [change.to_dict() for change in self.packages],
        }

    @classmethod
    def from_dict(cls, value: object, *, index: int = 0) -> HistoryRecord:
        if not isinstance(value, dict):
            raise HistoryError(f"History record at index {index} is not an object")
        required = {"transaction_id", "created_at", "action", "outcome", "packages"}
        if set(value) != required:
            raise HistoryError(f"History record at index {index} has an invalid shape")
        packages_value = value["packages"]
        if not isinstance(packages_value, list):
            raise HistoryError(f"History record at index {index} packages must be a list")
        if len(packages_value) > MAX_HISTORY_PACKAGES:
            raise HistoryError(
                f"History record at index {index} exceeds the package limit"
            )
        return cls(
            transaction_id=_validate_text(
                value["transaction_id"], "transaction_id", maximum=128
            ),
            created_at=_validate_timestamp(value["created_at"]),
            action=_validate_action(value["action"], HISTORY_ACTIONS, "History action"),
            outcome=_validate_action(
                value["outcome"], HISTORY_OUTCOMES, "History outcome"
            ),
            packages=tuple(
                PackageChange.from_dict(item, index=package_index)
                for package_index, item in enumerate(packages_value)
            ),
        )


def _validated_versions(
    values: Mapping[str, str | None],
    field: str,
) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for ref_value, version_value in values.items():
        ref = _parse_ref(ref_value, f"{field} package reference")
        version = _optional_version(version_value, f"{field}[{ref.qualified}]")
        if ref.qualified in result:
            raise HistoryError(f"{field} contains duplicate reference {ref.qualified}")
        result[ref.qualified] = version
    return result


def _change_action(before: str | None, after: str | None) -> str:
    if before is None:
        return "install"
    if after is None:
        return "remove"
    if before == after:
        return "unchanged"
    return "upgrade"


def make_history_record(
    transaction_id: str,
    action: str,
    before_versions: Mapping[str, str | None],
    after_versions: Mapping[str, str | None],
    *,
    outcome: str = "completed",
    created_at: str | None = None,
) -> HistoryRecord:
    before = _validated_versions(before_versions, "before_versions")
    after = _validated_versions(after_versions, "after_versions")
    changes: list[PackageChange] = []
    for ref_value in sorted(set(before) | set(after)):
        before_version = before.get(ref_value)
        after_version = after.get(ref_value)
        if before_version is None and after_version is None:
            continue
        changes.append(
            PackageChange(
                ref=_parse_ref(ref_value),
                action=_change_action(before_version, after_version),
                before_version=before_version,
                after_version=after_version,
            )
        )
    return HistoryRecord(
        transaction_id=_validate_text(transaction_id, "transaction_id", maximum=128),
        created_at=_validate_timestamp(created_at or _now()),
        action=_validate_action(action, HISTORY_ACTIONS, "History action"),
        outcome=_validate_action(outcome, HISTORY_OUTCOMES, "History outcome"),
        packages=tuple(changes),
    )


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_history_path()

    def records(self) -> tuple[HistoryRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            if self.path.stat().st_size > MAX_HISTORY_FILE_BYTES:
                raise HistoryError("History file exceeds its safety size limit")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except HistoryError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoryError(f"Unable to read history {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise HistoryError("History root must be an object")
        if set(value) != {"schema_version", "records"}:
            raise HistoryError("History root has an invalid shape")
        if value.get("schema_version") != HISTORY_SCHEMA_VERSION:
            raise HistoryError(
                f"Unsupported history schema: {value.get('schema_version')!r}"
            )
        records_value = value.get("records")
        if not isinstance(records_value, list):
            raise HistoryError("History records must be a list")
        if len(records_value) > MAX_HISTORY_RECORDS:
            raise HistoryError(
                f"History exceeds the {MAX_HISTORY_RECORDS}-record safety limit"
            )
        return tuple(
            HistoryRecord.from_dict(item, index=index)
            for index, item in enumerate(records_value)
        )

    def append(self, record: HistoryRecord) -> None:
        if not isinstance(record, HistoryRecord):
            raise HistoryError("record must be a HistoryRecord")
        try:
            with exclusive_state_lock(self.path):
                records = list(self.records())
                if any(item.transaction_id == record.transaction_id for item in records):
                    raise HistoryError(
                        f"History already contains transaction {record.transaction_id}"
                    )
                if len(records) >= MAX_HISTORY_RECORDS:
                    raise HistoryError(
                        f"History reached the {MAX_HISTORY_RECORDS}-record safety limit"
                    )
                records.append(record)
                atomic_write_json(
                    self.path,
                    {
                        "schema_version": HISTORY_SCHEMA_VERSION,
                        "records": [item.to_dict() for item in records],
                    },
                )
        except StateLockError as exc:
            raise HistoryError(str(exc)) from exc

    def get(self, transaction_id: str) -> HistoryRecord:
        _validate_text(transaction_id, "transaction_id", maximum=128)
        for record in self.records():
            if record.transaction_id == transaction_id:
                return record
        raise HistoryError(f"Unknown history transaction: {transaction_id}")


@dataclass(frozen=True, slots=True)
class UndoStep:
    ref: PackageRef
    action: str
    target_version: str | None
    exact: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref.qualified,
            "action": self.action,
            "target_version": self.target_version,
            "exact": self.exact,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class UndoPlan:
    transaction_id: str
    steps: tuple[UndoStep, ...]
    plan_only: bool = True
    automatic_execution_supported: bool = False
    note: str = (
        "Automatic undo is limited to removing packages that this transaction "
        "newly installed and whose current version still matches the record. "
        "Exact downgrades are never guessed."
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "plan_only": self.plan_only,
            "automatic_execution_supported": self.automatic_execution_supported,
            "note": self.note,
            "steps": [step.to_dict() for step in self.steps],
        }


def plan_undo(record: HistoryRecord) -> UndoPlan:
    if record.outcome != "completed":
        raise HistoryError("Only completed transactions can produce an undo plan")
    steps: list[UndoStep] = []
    for change in record.packages:
        if change.before_version is None and change.after_version is not None:
            steps.append(
                UndoStep(
                    ref=change.ref,
                    action="remove-newly-installed",
                    target_version=None,
                    exact=True,
                    reason=(
                        "The package was absent before this transaction. Removal "
                        "would restore that package-level state, subject to dependency review."
                    ),
                )
            )
        elif change.before_version is not None and change.after_version is None:
            steps.append(
                UndoStep(
                    ref=change.ref,
                    action="exact-rollback-unavailable",
                    target_version=change.before_version,
                    exact=False,
                    reason=(
                        "The package was removed. A rolling repository does not "
                        "guarantee that the exact prior version remains available."
                    ),
                )
            )
        elif change.before_version != change.after_version:
            steps.append(
                UndoStep(
                    ref=change.ref,
                    action="exact-rollback-unavailable",
                    target_version=change.before_version,
                    exact=False,
                    reason=(
                        "The package version changed. Exact downgrade requires a "
                        "reviewed cached package or an archival repository."
                    ),
                )
            )
        else:
            steps.append(
                UndoStep(
                    ref=change.ref,
                    action="no-op",
                    target_version=change.before_version,
                    exact=True,
                    reason="The recorded package version did not change.",
                )
            )
    return UndoPlan(
        transaction_id=record.transaction_id,
        steps=tuple(steps),
        automatic_execution_supported=(
            any(
                step.action == "remove-newly-installed" and step.exact
                for step in steps
            )
            and all(
                step.action in {"remove-newly-installed", "no-op"} and step.exact
                for step in steps
            )
        ),
    )
