from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from blackforge import cli
from blackforge.catalog import Catalog
from blackforge.maintenance import (
    MaintenanceEvidence,
    MaintenanceSnapshot,
    MaintenanceStatus,
)
from blackforge.models import Tool
from blackforge.sources import ARCH_SOURCE_LABEL


def _catalog() -> Catalog:
    return Catalog(
        tools=[
            Tool("archived-tool", "1", "archived", "blackarch-misc"),
            Tool("current-tool", "1", "current", "blackarch-misc"),
            Tool("stale-tool", "1", "stale", "blackarch-misc"),
            Tool("unknown-tool", "1", "unknown", "blackarch-misc"),
        ],
        source="test",
        fetched_at="2026-07-29T00:00:00+00:00",
    )


@pytest.fixture
def offline_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Catalog, Path]:
    catalog = _catalog()
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "load_catalog", lambda _path=None: catalog)
    monkeypatch.setattr(
        cli.PacmanBackend,
        "supported",
        property(lambda _self: False),
    )
    monkeypatch.setattr(
        cli.PacmanBackend,
        "repo_enabled",
        property(lambda _self: False),
    )
    return catalog, state_home


def test_help_and_nested_help_do_not_load_catalog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_catalog_load(*_args, **_kwargs):
        raise AssertionError("help must be available before catalog loading")

    monkeypatch.setattr(cli, "load_catalog", fail_catalog_load)

    assert cli.run(["help"]) == 0
    assert "usage: blackforge" in capsys.readouterr().out

    assert cli.run(["help", "install"]) == 0
    install_help = capsys.readouterr().out
    assert "usage: blackforge install" in install_help
    assert "--setup-repo" in install_help

    assert cli.run(["help", "profile", "create"]) == 0
    nested_help = capsys.readouterr().out
    assert "usage: blackforge profile create" in nested_help

    with pytest.raises(SystemExit) as stopped:
        cli.run(["profile", "create", "--help"])
    assert stopped.value.code == 0
    assert "usage: blackforge profile create" in capsys.readouterr().out


def test_global_json_and_catalog_options_work_after_search_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_catalog = tmp_path / "selected-catalog.json"
    loaded: list[Path | None] = []

    def load(path: Path | None = None) -> Catalog:
        loaded.append(path)
        return _catalog()

    monkeypatch.setattr(cli, "load_catalog", load)
    monkeypatch.setattr(
        cli.PacmanBackend,
        "supported",
        property(lambda _self: False),
    )

    assert (
        cli.run(
            [
                "search",
                "nmap",
                "--source",
                "arch",
                "--catalog",
                str(selected_catalog),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert loaded == [selected_catalog]
    assert [item["name"] for item in payload] == ["nmap"]
    assert payload[0]["source"] == "Arch/extra"
    assert payload[0]["package_target"] == "extra/nmap"


def test_source_qualified_show_reports_curated_arch_nmap(
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli
    assert cli.run(["show", "arch:extra/nmap", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["name"] == "nmap"
    assert payload["source"] == ARCH_SOURCE_LABEL
    assert payload["repository"] == "extra"
    assert payload["package_target"] == "extra/nmap"
    assert payload["installed_version"] is None
    assert state_home.exists() is False


def test_maintenance_summary_has_two_top_groups_and_evidence_counts(
    monkeypatch: pytest.MonkeyPatch,
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli
    snapshot = MaintenanceSnapshot(
        records={
            "current-tool": MaintenanceEvidence(
                status=MaintenanceStatus.CURRENT,
                last_activity=date(2026, 1, 1),
                checked_at=date(2026, 7, 29),
                confidence="high",
            ),
            "stale-tool": MaintenanceEvidence(
                status=MaintenanceStatus.STALE,
                last_activity=date(2019, 1, 1),
                checked_at=date(2026, 7, 29),
                confidence="high",
            ),
            "unknown-tool": MaintenanceEvidence.unknown(
                checked_at=date(2026, 7, 29)
            ),
            "archived-tool": MaintenanceEvidence(
                status=MaintenanceStatus.ARCHIVED,
                last_activity=date(2018, 1, 1),
                checked_at=date(2026, 7, 29),
                confidence="high",
            ),
        },
        generated_at=date(2026, 7, 29),
        source="deterministic-test",
    )

    def load_maintenance(*, stale_years: int, required: bool) -> MaintenanceSnapshot:
        assert stale_years == 5
        assert required is True
        return snapshot

    monkeypatch.setattr(cli, "load_bundled_maintenance", load_maintenance)
    assert (
        cli.run(["maintenance", "summary", "--stale-years", "5", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "catalog_tools": 4,
        "recently_maintained": 1,
        "needs_attention": 3,
        "current": 1,
        "stale": 1,
        "unknown": 1,
        "archived": 1,
        "cutoff_years": 5,
        "generated_at": "2026-07-29",
    }
    assert state_home.exists() is False


def test_collection_dry_run_is_json_and_never_installs(
    monkeypatch: pytest.MonkeyPatch,
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli

    def forbidden_install(*_args, **_kwargs):
        raise AssertionError("collection dry-run must not call pacman install")

    monkeypatch.setattr(cli.PacmanBackend, "install", forbidden_install)
    assert (
        cli.run(
            [
                "collection",
                "apply",
                "network-discovery",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "install"
    assert payload["dry_run"] is True
    assert payload["command"][:3] == ["pacman", "-S", "--needed"]
    assert {"extra/nmap", "extra/masscan", "extra/tcpdump", "amass"} <= set(
        payload["requested"]
    )
    assert state_home.exists() is False


def test_plan_json_after_nested_subcommand_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli

    def forbidden_mutation(*_args, **_kwargs):
        raise AssertionError("plan command must not mutate packages")

    monkeypatch.setattr(cli.PacmanBackend, "install", forbidden_mutation)
    monkeypatch.setattr(cli.PacmanBackend, "remove", forbidden_mutation)
    monkeypatch.setattr(cli.PacmanBackend, "upgrade", forbidden_mutation)

    assert cli.run(["plan", "install", "extra/nmap", "--json", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "install"
    assert payload["requested"] == ["extra/nmap"]
    assert payload["command"] == [
        "pacman",
        "-S",
        "--needed",
        "--",
        "extra/nmap",
    ]
    assert payload["dry_run"] is True
    assert state_home.exists() is False


def test_install_dry_run_does_not_create_journal_history_or_call_backend(
    monkeypatch: pytest.MonkeyPatch,
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run crossed a mutation boundary")

    monkeypatch.setattr(cli.PacmanBackend, "install", forbidden)
    monkeypatch.setattr(cli.TransactionJournal, "begin", forbidden)
    monkeypatch.setattr(cli.HistoryStore, "append", forbidden)

    assert cli.run(["install", "arch:extra/nmap", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "install"
    assert payload["requested"] == ["extra/nmap"]
    assert state_home.exists() is False


def test_empty_history_json_read_is_non_mutating(
    offline_cli: tuple[Catalog, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, state_home = offline_cli
    assert cli.run(["history", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert state_home.exists() is False
