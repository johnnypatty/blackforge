from __future__ import annotations

import argparse

import pytest

from blackforge.backend import BackendError, PacmanBackend
from blackforge.catalog import bundled_catalog
from blackforge.cli import _run_install, run


def test_names_prefix(capsys) -> None:
    assert run(["names", "--prefix", "amas"]) == 0
    assert capsys.readouterr().out.strip() == "amass"


def test_install_alias_dry_run(capsys) -> None:
    assert run(["--dry-run", "get", "amass"]) == 0
    output = capsys.readouterr().out
    assert "pacman -S --needed amass" in output


def test_completion_command(capsys) -> None:
    assert run(["completion", "bash"]) == 0
    assert "complete -F" in capsys.readouterr().out


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
