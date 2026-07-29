from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from blackforge.models import Tool
from blackforge.self_update import SelfUpdateError, _checksum_for, _version_tuple
from blackforge.tui import TuiState
from blackforge.updates import UpdateReport, compare_catalogs


def test_catalog_update_diff_round_trip() -> None:
    old = [
        Tool("removed", "1", "", "blackarch-misc"),
        Tool("changed", "1", "", "blackarch-misc"),
    ]
    new = [
        Tool("added", "1", "", "blackarch-misc"),
        Tool("changed", "2", "", "blackarch-misc"),
    ]
    report = compare_catalogs(old, new)
    assert report.added == ("added",)
    assert report.removed == ("removed",)
    assert report.changed[0].name == "changed"
    assert UpdateReport.from_dict(report.to_dict()) == report


def test_tui_state_filters_and_selects() -> None:
    state = TuiState(
        [
            Tool("amass", "1", "subdomain scanner", "blackarch-scanner"),
            Tool("hashcat", "1", "password recovery", "arch-cracker"),
        ]
    )
    state.set_query("subdomain")
    assert [tool.name for tool in state.visible] == ["amass"]
    state.toggle()
    assert state.selected == {"amass"}


def test_self_update_checksum_parser() -> None:
    data = b"wheel"
    digest = hashlib.sha256(data).hexdigest()
    assert _checksum_for(f"{digest}  blackforge.whl\n", "blackforge.whl") == digest
    with pytest.raises(SelfUpdateError):
        _checksum_for("bad", "blackforge.whl")


def test_release_verifier_rejects_a_tag_that_does_not_match_the_package() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_release.py"),
            "--tag",
            "v999.0.0",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert completed.returncode != 0
    assert "does not match package version" in completed.stderr


@pytest.mark.parametrize(
    ("value", "expected"),
    [("v0.3.0", (0, 3, 0)), ("1.2.3-beta.1", (1, 2, 3)), ("bad", ())],
)
def test_version_parser(value: str, expected: tuple[int, ...]) -> None:
    assert _version_tuple(value) == expected
