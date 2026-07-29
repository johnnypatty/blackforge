from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier

import pytest

from blackforge.environment import (
    EnvironmentFileError,
    PackageRef,
    create_environment,
    export_environment,
    import_environment,
    read_environment,
)
from blackforge.history import (
    HistoryError,
    HistoryStore,
    default_history_path,
    make_history_record,
    plan_undo,
)
from blackforge.transactions import (
    TransactionError,
    TransactionJournal,
    classify_retry,
    default_journal_path,
)


def test_history_and_transaction_paths_follow_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_history_path() == tmp_path / "blackforge" / "history.json"
    assert default_journal_path() == tmp_path / "blackforge" / "transactions.json"


def test_history_round_trip_is_immutable_and_records_versions(tmp_path: Path) -> None:
    record = make_history_record(
        "tx-001",
        "install",
        {"arch:nmap": None, "blackarch:amass": "1.0-1"},
        {"arch:nmap": "7.95-1", "blackarch:amass": "2.0-1"},
        created_at="2026-07-29T10:00:00+00:00",
    )
    store = HistoryStore(tmp_path / "history.json")
    store.append(record)

    loaded = store.get("tx-001")
    assert loaded == record
    assert loaded.package_names == ("nmap", "amass")
    assert loaded.packages[0].before_version is None
    assert loaded.packages[0].after_version == "7.95-1"
    with pytest.raises(FrozenInstanceError):
        loaded.action = "remove"  # type: ignore[misc]


def test_history_atomic_append_preserves_existing_file_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path)
    first = make_history_record(
        "tx-first",
        "install",
        {"arch:nmap": None},
        {"arch:nmap": "7.95-1"},
    )
    second = make_history_record(
        "tx-second",
        "install",
        {"blackarch:amass": None},
        {"blackarch:amass": "2.0-1"},
    )
    store.append(first)
    original = path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr("blackforge.storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        store.append(second)

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".history.json.*.tmp"))


def test_undo_plan_distinguishes_removal_from_unavailable_exact_rollback() -> None:
    record = make_history_record(
        "tx-undo",
        "upgrade",
        {
            "arch:nmap": None,
            "blackarch:amass": "1.0-1",
            "blackarch:legacy": "3.0-1",
        },
        {
            "arch:nmap": "7.95-1",
            "blackarch:amass": "2.0-1",
            "blackarch:legacy": None,
        },
    )

    plan = plan_undo(record)
    steps = {step.ref.qualified: step for step in plan.steps}
    assert plan.plan_only is True
    assert plan.automatic_execution_supported is False
    assert steps["arch:nmap"].action == "remove-newly-installed"
    assert steps["arch:nmap"].exact is True
    assert steps["blackarch:amass"].action == "exact-rollback-unavailable"
    assert steps["blackarch:amass"].target_version == "1.0-1"
    assert steps["blackarch:amass"].exact is False
    assert steps["blackarch:legacy"].action == "exact-rollback-unavailable"


def test_corrupted_history_is_rejected_without_replacement(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HistoryError, match="Unable to read history"):
        HistoryStore(path).records()
    assert path.read_text(encoding="utf-8") == "{not json"


def test_environment_export_import_is_source_qualified_and_plan_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lab.json"
    manifest = export_environment(
        path,
        "web-lab",
        {
            "arch:nmap": "7.95-1",
            "blackarch:amass": "2.0-1",
            "blackarch:burpsuite": "2026.5-1",
        },
        created_at="2026-07-29T10:00:00+00:00",
    )

    assert read_environment(path) == manifest
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert {item["ref"] for item in payload["packages"]} == {
        "arch:nmap",
        "blackarch:amass",
        "blackarch:burpsuite",
    }

    plan = import_environment(
        path,
        {
            "arch:nmap": "7.95-1",
            "blackarch:amass": "1.0-1",
            "arch:wireshark-qt": "4.4-1",
        },
    )
    assert plan.plan_only is True
    assert [item.ref.qualified for item in plan.install] == ["blackarch:burpsuite"]
    assert [item.ref.qualified for item in plan.satisfied] == ["arch:nmap"]
    assert [item.ref.qualified for item in plan.version_drift] == [
        "blackarch:amass"
    ]
    assert plan.version_drift[0].exact_version_available is False
    assert [item.qualified for item in plan.ignored_extras] == [
        "arch:wireshark-qt"
    ]

    with pytest.raises(EnvironmentFileError, match="plan-only"):
        import_environment(path, {}, plan_only=False)


@pytest.mark.parametrize(
    "ref",
    [
        "nmap",
        "aur:nmap",
        "blackarch:--config",
        "blackarch:../../etc/passwd",
        "blackarch:amass:extra",
    ],
)
def test_environment_rejects_unqualified_or_malicious_package_refs(ref: str) -> None:
    with pytest.raises(EnvironmentFileError):
        create_environment("unsafe", {ref: "1.0-1"})


def test_environment_rejects_corruption_and_duplicate_refs(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(EnvironmentFileError, match="root must be an object"):
        read_environment(malformed)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "duplicate",
                "created_at": "2026-07-29T10:00:00+00:00",
                "packages": [
                    {"ref": "arch:nmap", "version": "1"},
                    {"ref": "arch:nmap", "version": "2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EnvironmentFileError, match="duplicate"):
        read_environment(duplicate)


def test_transaction_journal_tracks_failure_resume_and_completion(
    tmp_path: Path,
) -> None:
    journal = TransactionJournal(tmp_path / "transactions.json")
    pending = journal.begin(
        "install",
        ["blackarch:amass", "arch:nmap"],
        transaction_id="tx-resume",
        max_attempts=3,
    )
    assert pending.status == "pending"
    assert pending.attempt == 1

    failed = journal.mark_failed(
        pending.transaction_id,
        "failed retrieving file from mirror",
        completed_packages=["blackarch:amass"],
    )
    assert failed.status == "failed"
    assert failed.retryable is True
    assert failed.retry_category == "download"

    metadata = journal.resume_metadata(pending.transaction_id)
    assert metadata.can_resume is True
    assert metadata.next_attempt == 2
    assert [ref.qualified for ref in metadata.completed_packages] == [
        "blackarch:amass"
    ]
    assert [ref.qualified for ref in metadata.remaining_packages] == ["arch:nmap"]

    resumed = journal.resume(pending.transaction_id)
    assert resumed.status == "pending"
    assert resumed.attempt == 2
    assert resumed.error is None
    completed = journal.mark_completed(pending.transaction_id)
    assert completed.status == "completed"
    assert {ref.qualified for ref in completed.completed_packages} == {
        "blackarch:amass",
        "arch:nmap",
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timed out"), True),
        ("could not resolve host mirror.example", True),
        ("failed retrieving file package.pkg.tar.zst", True),
        ("invalid or corrupted package (PGP signature)", False),
        ("target not found: missing-tool", False),
        (PermissionError("permission denied"), False),
    ],
)
def test_retry_classifier_is_conservative(
    error: BaseException | str,
    expected: bool,
) -> None:
    decision = classify_retry(error, attempt=1, max_attempts=3)
    assert decision.retryable is expected


def test_retry_classifier_enforces_bound_and_non_network_resume(
    tmp_path: Path,
) -> None:
    exhausted = classify_retry(
        "failed retrieving file package.pkg.tar.zst",
        attempt=3,
        max_attempts=3,
    )
    assert exhausted.retryable is False
    assert exhausted.remaining_attempts == 0

    journal = TransactionJournal(tmp_path / "transactions.json")
    pending = journal.begin(
        "install",
        ["arch:nmap"],
        transaction_id="tx-no-retry",
    )
    failed = journal.mark_failed(pending.transaction_id, "target not found: nmap")
    assert failed.retryable is False
    assert journal.resume_metadata(pending.transaction_id).can_resume is False
    with pytest.raises(TransactionError, match="not a recognized"):
        journal.resume(pending.transaction_id)


@pytest.mark.parametrize(
    "ref",
    ["nmap", "blackarch:--config", "blackarch:../../etc", "aur:tool"],
)
def test_transaction_journal_rejects_malicious_package_refs(
    ref: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(TransactionError):
        TransactionJournal(tmp_path / "transactions.json").begin(
            "install",
            [ref],
        )


def test_transaction_journal_rejects_corruption_and_invalid_transitions(
    tmp_path: Path,
) -> None:
    corrupted = tmp_path / "corrupted.json"
    corrupted.write_text('{"schema_version": 1, "transactions": "wrong"}', encoding="utf-8")
    with pytest.raises(TransactionError, match="entries must be a list"):
        TransactionJournal(corrupted).records()

    journal = TransactionJournal(tmp_path / "valid.json")
    record = journal.begin(
        "install",
        [PackageRef("arch", "nmap")],
        transaction_id="tx-complete",
    )
    journal.mark_completed(record.transaction_id)
    with pytest.raises(TransactionError, match="Cannot fail"):
        journal.mark_failed(record.transaction_id, "network is unreachable")


def test_concurrent_complete_and_fail_have_one_state_transition_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "complete-fail-race.json"
    journal = TransactionJournal(path)
    pending = journal.begin(
        "install",
        ["arch:nmap"],
        transaction_id="tx-complete-fail-race",
    )

    # This synchronizes the stale pre-lock reads used by the vulnerable
    # implementation. Correct transitions do not call get() before locking.
    barrier = Barrier(2)
    original_get = journal.get

    def synchronized_get(transaction_id: str):
        record = original_get(transaction_id)
        barrier.wait(timeout=5)
        return record

    monkeypatch.setattr(journal, "get", synchronized_get)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(journal.mark_completed, pending.transaction_id),
            executor.submit(
                journal.mark_failed,
                pending.transaction_id,
                "network is unreachable",
            ),
        )

    successes = []
    errors = []
    for future in futures:
        try:
            successes.append(future.result())
        except TransactionError as exc:
            errors.append(exc)

    assert len(successes) == 1
    assert len(errors) == 1
    stored = TransactionJournal(path).get(pending.transaction_id)
    assert stored == successes[0]
    assert stored.status in {"completed", "failed"}


def test_concurrent_resume_has_one_state_transition_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "resume-race.json"
    journal = TransactionJournal(path)
    pending = journal.begin(
        "install",
        ["arch:nmap"],
        transaction_id="tx-resume-race",
        max_attempts=3,
    )
    journal.mark_failed(pending.transaction_id, "network is unreachable")

    # The vulnerable implementation performs two unlocked reads per resume.
    # Pairing both phases makes the lost-update race deterministic there.
    barrier = Barrier(2)
    original_get = journal.get

    def synchronized_get(transaction_id: str):
        record = original_get(transaction_id)
        barrier.wait(timeout=5)
        return record

    monkeypatch.setattr(journal, "get", synchronized_get)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(journal.resume, pending.transaction_id),
            executor.submit(journal.resume, pending.transaction_id),
        )

    successes = []
    errors = []
    for future in futures:
        try:
            successes.append(future.result())
        except TransactionError as exc:
            errors.append(exc)

    assert len(successes) == 1
    assert len(errors) == 1
    stored = TransactionJournal(path).get(pending.transaction_id)
    assert stored == successes[0]
    assert stored.status == "pending"
    assert stored.attempt == 2
