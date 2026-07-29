from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from blackforge import cli
from blackforge.backend import BackendError, CommandResult, PacmanBackend
from blackforge.catalog import CatalogError, bundled_catalog, download_catalog
from blackforge.history import HistoryStore
from blackforge.planner import plan_install, plan_remove
from blackforge.sources import resolve_arch_tool
from blackforge.transactions import TransactionJournal
from blackforge.tui import TuiState


def test_install_retries_only_a_recognized_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = TransactionJournal(tmp_path / "transactions.json")
    history = HistoryStore(tmp_path / "history.json")
    monkeypatch.setattr(cli, "TransactionJournal", lambda: journal)
    monkeypatch.setattr(cli, "HistoryStore", lambda: history)

    class Backend:
        installed_calls = 0

        def installed_packages(self) -> dict[str, str]:
            self.installed_calls += 1
            return {} if self.installed_calls == 1 else {"amass": "4.2.0-1"}

    results = iter(
        (
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="error: failed retrieving file",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
    )
    attempts = 0

    def execute():
        nonlocal attempts
        attempts += 1
        return next(results)

    assert (
        cli._recorded_package_operation(
            Backend(),
            "install",
            ["amass"],
            execute,
            retries=1,
        )
        == 0
    )
    assert attempts == 2
    transaction = journal.records()[0]
    assert transaction.status == "completed"
    assert transaction.attempt == 2
    assert history.records()[0].packages[0].after_version == "4.2.0-1"


def test_successful_resume_records_before_and_after_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = TransactionJournal(tmp_path / "transactions.json")
    history = HistoryStore(tmp_path / "history.json")
    transaction = journal.begin(
        "install",
        ["arch:nmap"],
        transaction_id="resume-history",
        max_attempts=3,
    )
    journal.mark_failed(transaction.transaction_id, "failed retrieving file")
    monkeypatch.setattr(cli, "TransactionJournal", lambda: journal)
    monkeypatch.setattr(cli, "HistoryStore", lambda: history)

    class Backend:
        def __init__(self) -> None:
            self.installed_calls = 0
            self.installed_targets: list[str] = []

        def require_supported(self) -> None:
            pass

        def installed_packages(self) -> dict[str, str]:
            self.installed_calls += 1
            return {} if self.installed_calls == 1 else {"nmap": "7.99-3"}

        def install(self, targets) -> CommandResult:
            self.installed_targets = list(targets)
            return CommandResult(["pacman"], 0)

    backend = Backend()
    args = SimpleNamespace(
        transaction_id=transaction.transaction_id,
        apply=True,
        dry_run=False,
        json=False,
    )

    assert cli._handle_resume(args, backend) == 0
    assert backend.installed_targets == ["extra/nmap"]
    assert journal.get(transaction.transaction_id).status == "completed"
    saved = history.get(transaction.transaction_id)
    assert saved.action == "install"
    assert saved.packages[0].ref.qualified == "arch:nmap"
    assert saved.packages[0].before_version is None
    assert saved.packages[0].after_version == "7.99-3"


@pytest.mark.parametrize(
    ("interruption", "expected_type"),
    [
        (KeyboardInterrupt(), KeyboardInterrupt),
        (SystemExit(130), SystemExit),
    ],
)
def test_resume_interruption_cannot_leave_transaction_pending(
    interruption: BaseException,
    expected_type: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = TransactionJournal(tmp_path / "transactions.json")
    transaction = journal.begin(
        "install",
        ["arch:nmap"],
        transaction_id="resume-interrupted",
        max_attempts=3,
    )
    journal.mark_failed(transaction.transaction_id, "failed retrieving file")
    monkeypatch.setattr(cli, "TransactionJournal", lambda: journal)

    class Backend:
        def require_supported(self) -> None:
            pass

        def installed_packages(self) -> dict[str, str]:
            return {}

        def install(self, _targets) -> CommandResult:
            raise interruption

    args = SimpleNamespace(
        transaction_id=transaction.transaction_id,
        apply=True,
        dry_run=False,
        json=False,
    )
    with pytest.raises(expected_type):
        cli._handle_resume(args, Backend())

    failed = journal.get(transaction.transaction_id)
    assert failed.status == "failed"
    assert failed.attempt == 2


def test_planner_marks_qualified_arch_target_requested_and_strips_remove_repo() -> None:
    class Backend:
        def plan_metadata(self, _operation, _requested):
            return {
                "packages": {
                    "nmap": {
                        "name": "nmap",
                        "version": "7.99-3",
                        "repository": "extra",
                    }
                }
            }

    install = plan_install(["arch:extra/nmap"], backend=Backend())
    remove = plan_remove(["arch:extra/nmap"])
    assert install.requested == ("extra/nmap",)
    assert install.packages[0].requested is True
    assert remove.requested == ("nmap",)
    assert remove.command[-1] == "nmap"


def test_tui_handles_and_preserves_official_arch_source_identity() -> None:
    nmap = resolve_arch_tool("arch:extra/nmap")
    state = TuiState([nmap])
    state.set_query("network scanner")
    assert state.visible == [nmap]
    state.toggle()
    assert state.selected == {"arch:extra/nmap"}


def test_catalog_rejects_cross_host_https_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self) -> str:
            return "https://untrusted.example/tools.html"

    monkeypatch.setattr(
        "blackforge.catalog.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    with pytest.raises(CatalogError, match="untrusted"):
        download_catalog()


def test_setup_script_rejects_cross_host_https_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self) -> str:
            return "https://untrusted.example/strap.sh"

    monkeypatch.setattr(
        "blackforge.backend.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    with pytest.raises(BackendError, match="untrusted"):
        PacmanBackend().download_strap()


def test_global_dry_run_prevents_catalog_profile_and_update_report_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog = bundled_catalog()
    monkeypatch.setattr(cli, "download_catalog", lambda _url: catalog)
    monkeypatch.setattr(
        cli,
        "save_report",
        lambda _report: pytest.fail("dry-run saved an update report"),
    )
    catalog_path = tmp_path / "catalog.json"
    profile_path = tmp_path / "profile.json"

    assert cli.run(["sync", "--output", str(catalog_path), "--dry-run"]) == 0
    assert (
        cli.run(
            [
                "profile",
                "create",
                str(profile_path),
                "amass",
                "--dry-run",
            ]
        )
        == 0
    )
    assert cli.run(["updates", "check", "--dry-run"]) == 0
    assert not catalog_path.exists()
    assert not profile_path.exists()


def test_setup_repo_install_dry_run_remains_one_valid_json_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DryBackend:
        supported = False
        repo_enabled = False

        def __init__(self, **_kwargs) -> None:
            pass

    monkeypatch.setattr(cli, "PacmanBackend", DryBackend)
    assert (
        cli.run(
            [
                "install",
                "amass",
                "--setup-repo",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "install"
    assert payload["blackarch_repository_setup_required"] is True


def test_maintenance_status_selects_its_group_when_group_is_omitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.run(
            [
                "maintenance",
                "list",
                "--status",
                "current",
                "--limit",
                "1",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["status"] == "current"
