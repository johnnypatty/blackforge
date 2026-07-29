from __future__ import annotations

import argparse

import pytest

from blackforge.backend import BackendError, PacmanBackend
from blackforge.catalog import CatalogError, bundled_catalog
from blackforge.cli import _run_install, run


def test_names_prefix(capsys) -> None:
    assert run(["names", "--prefix", "amas"]) == 0
    assert capsys.readouterr().out.strip() == "amass"


def test_install_alias_dry_run(capsys) -> None:
    assert run(["--dry-run", "get", "amass"]) == 0
    output = capsys.readouterr().out
    assert "pacman -S --needed -- amass" in output


def test_completion_command(capsys) -> None:
    assert run(["completion", "bash"]) == 0
    assert "complete -F" in capsys.readouterr().out


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["list", "--limit", "-1"])
    assert exc.value.code == 2


def test_unknown_search_category_is_explained() -> None:
    with pytest.raises(CatalogError, match="Unknown category"):
        run(["search", "test", "--category", "blackarch-does-not-exist"])


def test_install_explains_missing_repository(monkeypatch) -> None:
    backend = PacmanBackend(assume_yes=True)
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: True))
    monkeypatch.setattr(PacmanBackend, "repo_enabled", property(lambda self: False))
    args = argparse.Namespace(
        names=["amass"],
        category=None,
        profile=None,
        dry_run=False,
        yes=True,
        setup_repo=False,
    )
    with pytest.raises(BackendError, match="blackforge setup"):
        _run_install(args, bundled_catalog(), backend)


def test_install_rejects_ambiguous_selection() -> None:
    backend = PacmanBackend(dry_run=True)
    args = argparse.Namespace(
        names=["amass"],
        category="blackarch-forensic",
        profile=None,
        dry_run=True,
        yes=False,
        setup_repo=False,
    )
    with pytest.raises(CatalogError, match="do not combine"):
        _run_install(args, bundled_catalog(), backend)


def test_remove_dry_run_allows_safe_installed_name_missing_from_catalog(capsys) -> None:
    assert run(["--dry-run", "remove", "retired-tool"]) == 0
    assert "pacman -R -- retired-tool" in capsys.readouterr().out
