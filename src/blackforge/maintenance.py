from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

MAINTENANCE_SCHEMA_VERSION = 1
SUPPORTED_STALE_YEARS = frozenset({3, 5})
CURRENT_GROUP = "current"
NEEDS_ATTENTION_GROUP = "needs-attention"
CONFIDENCE_LEVELS = frozenset({"none", "low", "medium", "high"})


class MaintenanceError(RuntimeError):
    pass


class MaintenanceStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    ARCHIVED = "archived"


def validate_stale_years(stale_years: int) -> int:
    if stale_years not in SUPPORTED_STALE_YEARS:
        choices = " or ".join(str(value) for value in sorted(SUPPORTED_STALE_YEARS))
        raise MaintenanceError(f"Stale cutoff must be {choices} years")
    return stale_years


def _calendar_cutoff(as_of: date, years: int) -> date:
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:
        # February 29 becomes February 28 in a non-leap cutoff year.
        return as_of.replace(year=as_of.year - years, day=28)


def classify_maintenance(
    last_activity: date | None,
    *,
    archived: bool = False,
    as_of: date | None = None,
    stale_years: int = 3,
) -> MaintenanceStatus:
    """Classify verified upstream activity without guessing missing evidence."""

    years = validate_stale_years(stale_years)
    if archived:
        return MaintenanceStatus.ARCHIVED
    if last_activity is None:
        return MaintenanceStatus.UNKNOWN
    today = as_of or datetime.now(timezone.utc).date()
    cutoff = _calendar_cutoff(today, years)
    # "At least N years old" includes activity exactly on the cutoff date.
    return (
        MaintenanceStatus.STALE
        if last_activity <= cutoff
        else MaintenanceStatus.CURRENT
    )


def maintenance_group(status: MaintenanceStatus | str) -> str:
    try:
        parsed = (
            status
            if isinstance(status, MaintenanceStatus)
            else MaintenanceStatus(status)
        )
    except ValueError as exc:
        raise MaintenanceError(f"Unknown maintenance status: {status!r}") from exc
    return (
        CURRENT_GROUP
        if parsed is MaintenanceStatus.CURRENT
        else NEEDS_ATTENTION_GROUP
    )


def _parse_date(value: object, *, field: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MaintenanceError(f"{field} must be an ISO-8601 string or null")
    candidate = value.strip()
    try:
        if "T" in candidate:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        else:
            parsed = date.fromisoformat(candidate)
    except ValueError as exc:
        raise MaintenanceError(f"Invalid {field}: {value!r}") from exc
    return parsed


def _validate_evidence_url(value: str) -> str:
    if not value:
        return value
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        raise MaintenanceError(
            "Maintenance evidence URL must use HTTP(S) and contain no credentials"
        )
    return value


@dataclass(frozen=True, slots=True)
class MaintenanceEvidence:
    status: MaintenanceStatus
    last_activity: date | None = None
    checked_at: date | None = None
    evidence_url: str = ""
    evidence_kind: str = ""
    confidence: str = "none"
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, MaintenanceStatus):
            try:
                object.__setattr__(self, "status", MaintenanceStatus(self.status))
            except ValueError as exc:
                raise MaintenanceError(
                    f"Unknown maintenance status: {self.status!r}"
                ) from exc
        if (
            self.status in {MaintenanceStatus.CURRENT, MaintenanceStatus.STALE}
            and self.last_activity is None
        ):
            raise MaintenanceError(
                f"{self.status.value} maintenance evidence needs last_activity"
            )
        if self.status is MaintenanceStatus.UNKNOWN and self.last_activity is not None:
            raise MaintenanceError(
                "Unknown maintenance evidence cannot claim verified last activity"
            )
        if self.confidence not in CONFIDENCE_LEVELS:
            raise MaintenanceError(
                f"Unsupported maintenance confidence: {self.confidence!r}"
            )
        _validate_evidence_url(self.evidence_url)

    @property
    def top_group(self) -> str:
        return maintenance_group(self.status)

    def reclassified(
        self,
        *,
        stale_years: int,
        as_of: date | None = None,
    ) -> MaintenanceEvidence:
        """Apply another cutoff while preserving unknown and archived states."""

        validate_stale_years(stale_years)
        if self.status in {
            MaintenanceStatus.UNKNOWN,
            MaintenanceStatus.ARCHIVED,
        }:
            return self
        return MaintenanceEvidence(
            status=classify_maintenance(
                self.last_activity,
                as_of=as_of,
                stale_years=stale_years,
            ),
            last_activity=self.last_activity,
            checked_at=self.checked_at,
            evidence_url=self.evidence_url,
            evidence_kind=self.evidence_kind,
            confidence=self.confidence,
            note=self.note,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "top_group": self.top_group,
            "last_activity_at": (
                self.last_activity.isoformat() if self.last_activity else None
            ),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "evidence_url": self.evidence_url,
            "evidence_kind": self.evidence_kind,
            "confidence": self.confidence,
            "note": self.note,
        }

    @classmethod
    def unknown(
        cls,
        *,
        checked_at: date | None = None,
        note: str = "No verified upstream maintenance evidence is available.",
    ) -> MaintenanceEvidence:
        return cls(
            status=MaintenanceStatus.UNKNOWN,
            checked_at=checked_at,
            confidence="none",
            note=note,
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        stale_years: int = 3,
        as_of: date | None = None,
        default_checked_at: date | None = None,
    ) -> MaintenanceEvidence:
        validate_stale_years(stale_years)
        raw_status = value.get("status", MaintenanceStatus.UNKNOWN.value)
        try:
            declared_status = MaintenanceStatus(str(raw_status))
        except ValueError as exc:
            raise MaintenanceError(
                f"Unknown maintenance status: {raw_status!r}"
            ) from exc
        last_activity = _parse_date(
            value.get("last_activity_at", value.get("last_activity")),
            field="last_activity_at",
        )
        checked_at = _parse_date(value.get("checked_at"), field="checked_at")
        if checked_at is None:
            checked_at = default_checked_at

        # A current/stale label is cutoff-dependent, so recompute it from its
        # evidence. Unknown and archived are deliberate states and stay intact.
        status = declared_status
        if declared_status in {
            MaintenanceStatus.CURRENT,
            MaintenanceStatus.STALE,
        }:
            status = classify_maintenance(
                last_activity,
                as_of=as_of,
                stale_years=stale_years,
            )
        return cls(
            status=status,
            last_activity=last_activity,
            checked_at=checked_at,
            evidence_url=str(value.get("evidence_url", "")),
            evidence_kind=str(value.get("evidence_kind", "")),
            confidence=str(value.get("confidence", "none")),
            note=str(value.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class MaintenanceSnapshot:
    records: Mapping[str, MaintenanceEvidence]
    generated_at: date | None = None
    source: str = ""
    schema_version: int = MAINTENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MAINTENANCE_SCHEMA_VERSION:
            raise MaintenanceError(
                f"Unsupported maintenance schema: {self.schema_version!r}"
            )
        normalized: dict[str, MaintenanceEvidence] = {}
        for tool_id, evidence in self.records.items():
            if not isinstance(tool_id, str) or not tool_id.strip():
                raise MaintenanceError("Maintenance record has an empty tool ID")
            if tool_id in normalized:
                raise MaintenanceError(
                    f"Duplicate maintenance record for {tool_id!r}"
                )
            if not isinstance(evidence, MaintenanceEvidence):
                raise MaintenanceError(
                    f"Invalid maintenance evidence for {tool_id!r}"
                )
            normalized[tool_id] = evidence
        object.__setattr__(
            self,
            "records",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def get(self, tool_id: str) -> MaintenanceEvidence | None:
        return self.records.get(tool_id)

    def for_tool(self, tool_id: str) -> MaintenanceEvidence:
        evidence = self.get(tool_id)
        if evidence is not None:
            return evidence
        return MaintenanceEvidence.unknown(
            checked_at=self.generated_at,
            note=f"No verified maintenance record exists for {tool_id}.",
        )

    def grouped(self) -> dict[str, dict[str, MaintenanceEvidence]]:
        result: dict[str, dict[str, MaintenanceEvidence]] = {
            CURRENT_GROUP: {},
            NEEDS_ATTENTION_GROUP: {},
        }
        for tool_id, evidence in self.records.items():
            result[evidence.top_group][tool_id] = evidence
        return result

    def reclassified(
        self,
        *,
        stale_years: int,
        as_of: date | None = None,
    ) -> MaintenanceSnapshot:
        return MaintenanceSnapshot(
            records={
                tool_id: evidence.reclassified(
                    stale_years=stale_years,
                    as_of=as_of,
                )
                for tool_id, evidence in self.records.items()
            },
            generated_at=self.generated_at,
            source=self.source,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "source": self.source,
            "record_count": len(self.records),
            "records": {
                tool_id: evidence.to_dict()
                for tool_id, evidence in self.records.items()
            },
        }

    @classmethod
    def empty(cls) -> MaintenanceSnapshot:
        return cls(records={})

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        stale_years: int = 3,
        as_of: date | None = None,
    ) -> MaintenanceSnapshot:
        if value.get("schema_version") != MAINTENANCE_SCHEMA_VERSION:
            raise MaintenanceError(
                f"Unsupported maintenance schema: "
                f"{value.get('schema_version')!r}"
            )
        generated_at = _parse_date(
            value.get("generated_at"),
            field="generated_at",
        )
        raw_records = value.get("records")
        if not isinstance(raw_records, dict):
            raise MaintenanceError("Maintenance snapshot has no records object")
        records: dict[str, MaintenanceEvidence] = {}
        for tool_id, raw_evidence in raw_records.items():
            if not isinstance(tool_id, str) or not isinstance(raw_evidence, dict):
                raise MaintenanceError("Malformed maintenance record")
            if tool_id in records:
                raise MaintenanceError(
                    f"Duplicate maintenance record for {tool_id!r}"
                )
            records[tool_id] = MaintenanceEvidence.from_dict(
                raw_evidence,
                stale_years=stale_years,
                as_of=as_of,
                default_checked_at=generated_at,
            )
        declared_count = value.get("record_count")
        if declared_count is not None and int(declared_count) != len(records):
            raise MaintenanceError(
                f"Maintenance count mismatch: declared {declared_count}, "
                f"parsed {len(records)}"
            )
        return cls(
            records=records,
            generated_at=generated_at,
            source=str(value.get("source", "")),
        )


def read_maintenance(
    path: Path,
    *,
    stale_years: int = 3,
    as_of: date | None = None,
) -> MaintenanceSnapshot:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceError(
            f"Unable to read maintenance snapshot {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise MaintenanceError("Maintenance snapshot root must be an object")
    return MaintenanceSnapshot.from_dict(
        value,
        stale_years=stale_years,
        as_of=as_of,
    )


def load_bundled_maintenance(
    *,
    stale_years: int = 3,
    as_of: date | None = None,
    required: bool = False,
) -> MaintenanceSnapshot:
    """Load optional ``data/maintenance.json``.

    Releases may omit the generated snapshot. In that case every lookup is
    honestly unknown unless ``required=True`` was requested by a caller that
    needs generation to have completed.
    """

    validate_stale_years(stale_years)
    location = resources.files("blackforge").joinpath("data/maintenance.json")
    if not location.is_file():
        if required:
            raise MaintenanceError("Bundled maintenance snapshot is unavailable")
        return MaintenanceSnapshot.empty()
    try:
        value = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceError(
            f"Bundled maintenance snapshot is unreadable: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise MaintenanceError("Bundled maintenance snapshot root must be an object")
    return MaintenanceSnapshot.from_dict(
        value,
        stale_years=stale_years,
        as_of=as_of,
    )


def group_maintenance(
    records: Iterable[tuple[str, MaintenanceEvidence]],
) -> dict[str, list[tuple[str, MaintenanceEvidence]]]:
    result: dict[str, list[tuple[str, MaintenanceEvidence]]] = {
        CURRENT_GROUP: [],
        NEEDS_ATTENTION_GROUP: [],
    }
    for tool_id, evidence in records:
        result[evidence.top_group].append((tool_id, evidence))
    return result
