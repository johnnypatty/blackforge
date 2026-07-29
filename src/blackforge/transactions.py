from __future__ import annotations

import errno
import http.client
import json
import re
import socket
import ssl
import urllib.error
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .environment import EnvironmentFileError, PackageRef
from .history import default_state_dir
from .state_lock import StateLockError, exclusive_state_lock
from .storage import atomic_write_json

TRANSACTION_SCHEMA_VERSION = 1
MAX_TRANSACTIONS = 10_000
MAX_ATTEMPTS = 10
MAX_TRANSACTION_PACKAGES = 10_000
MAX_TRANSACTION_FILE_BYTES = 32 * 1024 * 1024
TRANSACTION_STATUSES = frozenset({"pending", "completed", "failed"})
TRANSACTION_ACTIONS = frozenset(
    {
        "environment-import",
        "install",
        "remove",
        "self-update",
        "setup",
        "sync",
        "undo",
        "upgrade",
    }
)
PACKAGE_ACTIONS = frozenset(
    {"environment-import", "install", "remove", "undo", "upgrade"}
)
RETRY_CATEGORIES = frozenset({"download", "network"})

_SECURITY_OR_LOCAL_FAILURES = (
    "certificate verify failed",
    "dependency",
    "disk space",
    "file conflicts",
    "invalid or corrupted package",
    "keyring",
    "not authorized",
    "permission denied",
    "signature",
    "target not found",
    "unknown trust",
)
_NETWORK_FAILURES = (
    "connection refused",
    "connection reset",
    "could not connect",
    "could not resolve host",
    "host is unreachable",
    "name or service not known",
    "network is unreachable",
    "operation timed out",
    "temporary failure in name resolution",
)
_DOWNLOAD_FAILURES = (
    "download timed out",
    "failed retrieving file",
    "failed to download",
    "failed to retrieve",
    "operation too slow",
    "partial file",
    "transfer closed with",
    "unexpected eof while downloading",
)
_NETWORK_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "ECONNABORTED", None),
        getattr(errno, "ECONNREFUSED", None),
        getattr(errno, "ECONNRESET", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETDOWN", None),
        getattr(errno, "ENETRESET", None),
        getattr(errno, "ENETUNREACH", None),
        getattr(errno, "ETIMEDOUT", None),
    )
    if value is not None
)


class TransactionError(RuntimeError):
    pass


def default_journal_path() -> Path:
    return default_state_dir() / "transactions.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise TransactionError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TransactionError(
            f"{field} must be a timezone-aware ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise TransactionError(f"{field} must include a timezone")
    return value


def _validate_transaction_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise TransactionError("transaction_id contains unsafe characters")
    return value


def _validate_action(value: object) -> str:
    if not isinstance(value, str) or value not in TRANSACTION_ACTIONS:
        choices = ", ".join(sorted(TRANSACTION_ACTIONS))
        raise TransactionError(f"Transaction action must be one of: {choices}")
    return value


def _clean_error(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        text = str(error) or error.__class__.__name__
    elif isinstance(error, str):
        text = error
    else:
        raise TransactionError("Transaction failure must be an exception or string")
    text = "".join(
        character if character in "\n\t" or 32 <= ord(character) < 127 else " "
        for character in text
    ).strip()
    if not text:
        text = "unspecified failure"
    return text[:4096]


def _coerce_packages(packages: Iterable[PackageRef | str]) -> tuple[PackageRef, ...]:
    result: list[PackageRef] = []
    seen: set[str] = set()
    for value in packages:
        try:
            ref = value if isinstance(value, PackageRef) else PackageRef.parse(value)
        except EnvironmentFileError as exc:
            raise TransactionError(f"Invalid transaction package: {exc}") from exc
        if ref.qualified in seen:
            raise TransactionError(
                f"Duplicate transaction package reference: {ref.qualified}"
            )
        seen.add(ref.qualified)
        result.append(ref)
    return tuple(result)


def _retry_category(error: BaseException | str) -> str | None:
    text = _clean_error(error).casefold()
    if any(marker in text for marker in _SECURITY_OR_LOCAL_FAILURES):
        return None

    if isinstance(error, urllib.error.ContentTooShortError):
        return "download"
    if isinstance(error, http.client.IncompleteRead):
        return "download"
    if isinstance(error, (ssl.CertificateError, ssl.SSLCertVerificationError)):
        return None
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (ssl.CertificateError, ssl.SSLCertVerificationError)):
            return None
        if isinstance(reason, (TimeoutError, ConnectionError, socket.gaierror)):
            return "network"
        if isinstance(reason, OSError) and reason.errno in _NETWORK_ERRNOS:
            return "network"
    if isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)):
        return "network"
    if isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS:
        return "network"

    if any(marker in text for marker in _DOWNLOAD_FAILURES):
        return "download"
    if any(marker in text for marker in _NETWORK_FAILURES):
        return "network"
    return None


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retryable: bool
    category: str | None
    attempt: int
    max_attempts: int
    remaining_attempts: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "retryable": self.retryable,
            "category": self.category,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "remaining_attempts": self.remaining_attempts,
            "reason": self.reason,
        }


def classify_retry(
    error: BaseException | str,
    *,
    attempt: int,
    max_attempts: int = 3,
) -> RetryDecision:
    if not isinstance(attempt, int) or attempt < 1:
        raise TransactionError("attempt must be at least 1")
    if (
        not isinstance(max_attempts, int)
        or max_attempts < 1
        or max_attempts > MAX_ATTEMPTS
    ):
        raise TransactionError(
            f"max_attempts must be between 1 and {MAX_ATTEMPTS}"
        )
    if attempt > max_attempts:
        raise TransactionError("attempt cannot exceed max_attempts")
    category = _retry_category(error)
    remaining = max_attempts - attempt
    if category is None:
        return RetryDecision(
            retryable=False,
            category=None,
            attempt=attempt,
            max_attempts=max_attempts,
            remaining_attempts=remaining,
            reason="Only recognized network or download failures are retryable.",
        )
    if remaining == 0:
        return RetryDecision(
            retryable=False,
            category=category,
            attempt=attempt,
            max_attempts=max_attempts,
            remaining_attempts=0,
            reason="The bounded retry limit has been reached.",
        )
    return RetryDecision(
        retryable=True,
        category=category,
        attempt=attempt,
        max_attempts=max_attempts,
        remaining_attempts=remaining,
        reason=f"Recognized transient {category} failure.",
    )


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    transaction_id: str
    action: str
    status: str
    packages: tuple[PackageRef, ...]
    completed_packages: tuple[PackageRef, ...]
    created_at: str
    updated_at: str
    attempt: int
    max_attempts: int
    error: str | None = None
    retryable: bool = False
    retry_category: str | None = None

    def __post_init__(self) -> None:
        _validate_transaction_id(self.transaction_id)
        _validate_action(self.action)
        if self.status not in TRANSACTION_STATUSES:
            raise TransactionError(
                "Transaction status must be pending, completed, or failed"
            )
        if self.action in PACKAGE_ACTIONS and not self.packages:
            raise TransactionError(
                f"Transaction action {self.action!r} requires package references"
            )
        if len(self.packages) > MAX_TRANSACTION_PACKAGES:
            raise TransactionError(
                f"Transaction exceeds the {MAX_TRANSACTION_PACKAGES}-package limit"
            )
        package_values = [ref.qualified for ref in self.packages]
        if len(package_values) != len(set(package_values)):
            raise TransactionError("Transaction contains duplicate package references")
        completed_values = [ref.qualified for ref in self.completed_packages]
        if len(completed_values) != len(set(completed_values)):
            raise TransactionError(
                "Transaction contains duplicate completed package references"
            )
        if not set(completed_values).issubset(package_values):
            raise TransactionError(
                "Completed package references must belong to the transaction"
            )
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")
        if (
            not isinstance(self.attempt, int)
            or not isinstance(self.max_attempts, int)
            or self.attempt < 1
            or self.max_attempts < 1
            or self.max_attempts > MAX_ATTEMPTS
            or self.attempt > self.max_attempts
        ):
            raise TransactionError("Invalid bounded-attempt metadata")
        if self.retry_category is not None and self.retry_category not in RETRY_CATEGORIES:
            raise TransactionError("Invalid retry category")
        if self.status == "failed":
            if not isinstance(self.error, str) or not self.error:
                raise TransactionError("Failed transactions require an error")
        elif self.error is not None or self.retryable or self.retry_category is not None:
            raise TransactionError(
                "Only failed transactions may contain retry/error metadata"
            )
        if self.retryable and (
            self.retry_category is None or self.attempt >= self.max_attempts
        ):
            raise TransactionError("Invalid retryable transaction metadata")
        if self.status == "completed" and set(completed_values) != set(package_values):
            raise TransactionError(
                "Completed transactions must mark every package as completed"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "action": self.action,
            "status": self.status,
            "packages": [ref.qualified for ref in self.packages],
            "completed_packages": [
                ref.qualified for ref in self.completed_packages
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "error": self.error,
            "retryable": self.retryable,
            "retry_category": self.retry_category,
        }

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> TransactionRecord:
        if not isinstance(value, dict):
            raise TransactionError(f"Transaction at index {index} is not an object")
        required = {
            "transaction_id",
            "action",
            "status",
            "packages",
            "completed_packages",
            "created_at",
            "updated_at",
            "attempt",
            "max_attempts",
            "error",
            "retryable",
            "retry_category",
        }
        if set(value) != required:
            raise TransactionError(f"Transaction at index {index} has an invalid shape")
        packages_value = value["packages"]
        completed_value = value["completed_packages"]
        if not isinstance(packages_value, list) or not isinstance(completed_value, list):
            raise TransactionError(
                f"Transaction at index {index} package fields must be lists"
            )
        if not isinstance(value["retryable"], bool):
            raise TransactionError(
                f"Transaction at index {index} retryable must be boolean"
            )
        if not isinstance(value["attempt"], int) or not isinstance(
            value["max_attempts"], int
        ):
            raise TransactionError(
                f"Transaction at index {index} attempt fields must be integers"
            )
        error_value = value["error"]
        if error_value is not None and not isinstance(error_value, str):
            raise TransactionError(
                f"Transaction at index {index} error must be a string or null"
            )
        retry_category_value = value["retry_category"]
        if retry_category_value is not None and not isinstance(
            retry_category_value, str
        ):
            raise TransactionError(
                f"Transaction at index {index} retry_category must be a string or null"
            )
        return cls(
            transaction_id=_validate_transaction_id(value["transaction_id"]),
            action=_validate_action(value["action"]),
            status=value["status"],
            packages=_coerce_packages(packages_value),
            completed_packages=_coerce_packages(completed_value),
            created_at=_validate_timestamp(value["created_at"], "created_at"),
            updated_at=_validate_timestamp(value["updated_at"], "updated_at"),
            attempt=value["attempt"],
            max_attempts=value["max_attempts"],
            error=error_value,
            retryable=value["retryable"],
            retry_category=retry_category_value,
        )


@dataclass(frozen=True, slots=True)
class ResumeMetadata:
    transaction_id: str
    can_resume: bool
    attempt: int
    next_attempt: int | None
    max_attempts: int
    retry_category: str | None
    completed_packages: tuple[PackageRef, ...]
    remaining_packages: tuple[PackageRef, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "can_resume": self.can_resume,
            "attempt": self.attempt,
            "next_attempt": self.next_attempt,
            "max_attempts": self.max_attempts,
            "retry_category": self.retry_category,
            "completed_packages": [
                ref.qualified for ref in self.completed_packages
            ],
            "remaining_packages": [
                ref.qualified for ref in self.remaining_packages
            ],
            "reason": self.reason,
        }


def _resume_metadata(record: TransactionRecord) -> ResumeMetadata:
    completed = {ref.qualified for ref in record.completed_packages}
    remaining = tuple(
        ref for ref in record.packages if ref.qualified not in completed
    )
    can_resume = (
        record.status == "failed"
        and record.retryable
        and record.attempt < record.max_attempts
        and bool(remaining)
    )
    if can_resume:
        reason = (
            f"Retry the remaining {len(remaining)} package(s) after a recognized "
            f"{record.retry_category} failure."
        )
    elif record.status != "failed":
        reason = "Only failed transactions can be resumed."
    elif not remaining:
        reason = "No packages remain to resume."
    elif record.attempt >= record.max_attempts:
        reason = "The bounded retry limit has been reached."
    else:
        reason = "The failure is not a recognized network/download failure."
    return ResumeMetadata(
        transaction_id=record.transaction_id,
        can_resume=can_resume,
        attempt=record.attempt,
        next_attempt=record.attempt + 1 if can_resume else None,
        max_attempts=record.max_attempts,
        retry_category=record.retry_category,
        completed_packages=record.completed_packages,
        remaining_packages=remaining,
        reason=reason,
    )


class TransactionJournal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_journal_path()

    def records(self) -> tuple[TransactionRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            if self.path.stat().st_size > MAX_TRANSACTION_FILE_BYTES:
                raise TransactionError(
                    "Transaction journal exceeds its safety size limit"
                )
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except TransactionError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise TransactionError(
                f"Unable to read transaction journal {self.path}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise TransactionError("Transaction journal root must be an object")
        if set(value) != {"schema_version", "transactions"}:
            raise TransactionError("Transaction journal root has an invalid shape")
        if value.get("schema_version") != TRANSACTION_SCHEMA_VERSION:
            raise TransactionError(
                f"Unsupported transaction schema: {value.get('schema_version')!r}"
            )
        records_value = value.get("transactions")
        if not isinstance(records_value, list):
            raise TransactionError("Transaction journal entries must be a list")
        if len(records_value) > MAX_TRANSACTIONS:
            raise TransactionError(
                f"Transaction journal exceeds the {MAX_TRANSACTIONS}-entry limit"
            )
        records = tuple(
            TransactionRecord.from_dict(item, index=index)
            for index, item in enumerate(records_value)
        )
        identifiers = [record.transaction_id for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise TransactionError("Transaction journal contains duplicate identifiers")
        return records

    def _write(self, records: Iterable[TransactionRecord]) -> None:
        values = tuple(records)
        try:
            atomic_write_json(
                self.path,
                {
                    "schema_version": TRANSACTION_SCHEMA_VERSION,
                    "transactions": [record.to_dict() for record in values],
                },
            )
        except OSError as exc:
            raise TransactionError(
                f"Unable to save transaction journal {self.path}: {exc}"
            ) from exc

    def get(self, transaction_id: str) -> TransactionRecord:
        _validate_transaction_id(transaction_id)
        for record in self.records():
            if record.transaction_id == transaction_id:
                return record
        raise TransactionError(f"Unknown transaction: {transaction_id}")

    def _transition(
        self,
        transaction_id: str,
        transition: Callable[[TransactionRecord], TransactionRecord],
    ) -> TransactionRecord:
        identifier = _validate_transaction_id(transaction_id)
        try:
            with exclusive_state_lock(self.path):
                records = list(self.records())
                for index, record in enumerate(records):
                    if record.transaction_id == identifier:
                        updated = transition(record)
                        if (
                            not isinstance(updated, TransactionRecord)
                            or updated.transaction_id != identifier
                        ):
                            raise TransactionError(
                                "Transaction transition produced an invalid record"
                            )
                        records[index] = updated
                        self._write(records)
                        return updated
                raise TransactionError(f"Unknown transaction: {identifier}")
        except StateLockError as exc:
            raise TransactionError(str(exc)) from exc

    def begin(
        self,
        action: str,
        packages: Iterable[PackageRef | str] = (),
        *,
        max_attempts: int = 3,
        transaction_id: str | None = None,
    ) -> TransactionRecord:
        action = _validate_action(action)
        package_refs = _coerce_packages(packages)
        if action in PACKAGE_ACTIONS and not package_refs:
            raise TransactionError(
                f"Transaction action {action!r} requires package references"
            )
        classify_retry("not retryable", attempt=1, max_attempts=max_attempts)
        identifier = _validate_transaction_id(
            transaction_id or str(uuid.uuid4())
        )
        try:
            with exclusive_state_lock(self.path):
                records = list(self.records())
                if any(record.transaction_id == identifier for record in records):
                    raise TransactionError(
                        f"Duplicate transaction identifier: {identifier}"
                    )
                if len(records) >= MAX_TRANSACTIONS:
                    raise TransactionError(
                        f"Transaction journal reached the "
                        f"{MAX_TRANSACTIONS}-entry limit"
                    )
                timestamp = _now()
                record = TransactionRecord(
                    transaction_id=identifier,
                    action=action,
                    status="pending",
                    packages=package_refs,
                    completed_packages=(),
                    created_at=timestamp,
                    updated_at=timestamp,
                    attempt=1,
                    max_attempts=max_attempts,
                )
                records.append(record)
                self._write(records)
                return record
        except StateLockError as exc:
            raise TransactionError(str(exc)) from exc

    def mark_completed(self, transaction_id: str) -> TransactionRecord:
        def complete(record: TransactionRecord) -> TransactionRecord:
            if record.status != "pending":
                raise TransactionError(
                    f"Cannot complete transaction in {record.status!r} state"
                )
            return replace(
                record,
                status="completed",
                completed_packages=record.packages,
                updated_at=_now(),
            )

        return self._transition(transaction_id, complete)

    def mark_failed(
        self,
        transaction_id: str,
        error: BaseException | str,
        *,
        completed_packages: Iterable[PackageRef | str] = (),
    ) -> TransactionRecord:
        additions = _coerce_packages(completed_packages)

        def fail(record: TransactionRecord) -> TransactionRecord:
            if record.status != "pending":
                raise TransactionError(
                    f"Cannot fail transaction in {record.status!r} state"
                )
            allowed = {ref.qualified for ref in record.packages}
            if any(ref.qualified not in allowed for ref in additions):
                raise TransactionError(
                    "Completed package references must belong to the transaction"
                )
            complete_values = {
                ref.qualified for ref in (*record.completed_packages, *additions)
            }
            completed = tuple(
                ref for ref in record.packages if ref.qualified in complete_values
            )
            decision = classify_retry(
                error,
                attempt=record.attempt,
                max_attempts=record.max_attempts,
            )
            remaining = len(record.packages) - len(completed)
            return replace(
                record,
                status="failed",
                completed_packages=completed,
                updated_at=_now(),
                error=_clean_error(error),
                retryable=decision.retryable and remaining > 0,
                retry_category=decision.category,
            )

        return self._transition(transaction_id, fail)

    def resume_metadata(self, transaction_id: str) -> ResumeMetadata:
        return _resume_metadata(self.get(transaction_id))

    def resume(self, transaction_id: str) -> TransactionRecord:
        def resume_failed(record: TransactionRecord) -> TransactionRecord:
            metadata = _resume_metadata(record)
            if not metadata.can_resume:
                raise TransactionError(metadata.reason)
            return replace(
                record,
                status="pending",
                updated_at=_now(),
                attempt=record.attempt + 1,
                error=None,
                retryable=False,
                retry_category=None,
            )

        return self._transition(transaction_id, resume_failed)
