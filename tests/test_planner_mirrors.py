from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from blackforge.mirrors import (
    MirrorError,
    MirrorTest,
    apply_mirror,
    parse_mirrorlist,
    probe_mirror,
    recommend_mirror,
)
from blackforge.planner import plan_install, plan_remove, plan_upgrade


class PlanningBackend:
    assume_yes = True

    def __init__(self) -> None:
        self.mutations = 0

    def plan_metadata(self, operation: str, names: tuple[str, ...]):
        assert operation == "install"
        assert names == ("nmap",)
        return {
            "packages": {
                "nmap": {
                    "version": "7.98-1",
                    "repository": "extra",
                    "download_size_bytes": 1_000,
                    "installed_size_bytes": 4_000,
                    "dependencies": ["libpcap"],
                    "conflicts": ["nmap-git"],
                },
                "libpcap": {
                    "version": "1.10-2",
                    "repository": "core",
                    "download_size_bytes": 500,
                    "installed_size_bytes": 1_000,
                },
            }
        }

    def install(self, _names) -> None:
        self.mutations += 1

    def remove(self, _names) -> None:
        self.mutations += 1

    def upgrade(self, _names) -> None:
        self.mutations += 1


def test_install_plan_has_metadata_json_and_never_mutates() -> None:
    backend = PlanningBackend()
    plan = plan_install(
        ["nmap"],
        backend=backend,
        disk_usage=lambda _path: SimpleNamespace(free=20_000),
    )
    assert backend.mutations == 0
    assert plan.dry_run is True
    assert plan.command == (
        "pacman",
        "-S",
        "--needed",
        "--noconfirm",
        "--",
        "nmap",
    )
    assert [package.name for package in plan.packages] == ["nmap", "libpcap"]
    assert plan.packages[0].requested is True
    assert plan.packages[1].requested is False
    assert plan.dependencies == ("libpcap",)
    assert plan.conflicts == ("nmap-git",)
    assert plan.download_size_bytes == 1_500
    assert plan.installed_size_bytes == 5_000
    assert plan.free_disk_bytes == 20_000
    assert plan.space_sufficient is True
    payload = json.loads(plan.to_json())
    assert payload["operation"] == "install"
    assert payload["packages"][0]["repository"] == "extra"


def test_remove_and_upgrade_commands_are_read_only_plans() -> None:
    no_metadata_disk = lambda _path: (100, 40, 60)
    remove = plan_remove(
        ["old-tool"],
        purge=True,
        assume_yes=False,
        disk_usage=no_metadata_disk,
    )
    selected_upgrade = plan_upgrade(
        ["nmap"],
        assume_yes=True,
        disk_usage=no_metadata_disk,
    )
    full_upgrade = plan_upgrade(
        assume_yes=False,
        disk_usage=no_metadata_disk,
    )
    assert remove.command == ("pacman", "-Rns", "--", "old-tool")
    assert remove.download_size_bytes == 0
    assert selected_upgrade.command == (
        "pacman",
        "-S",
        "--needed",
        "--noconfirm",
        "--",
        "nmap",
    )
    assert full_upgrade.command == ("pacman", "-Syu")
    assert full_upgrade.requested == ()


def test_plan_reports_insufficient_disk_space() -> None:
    backend = PlanningBackend()
    plan = plan_install(
        ["nmap"],
        backend=backend,
        disk_usage=lambda _path: SimpleNamespace(free=4_999),
    )
    assert plan.space_sufficient is False
    assert any("exceeds" in warning for warning in plan.warnings)


MIRRORLIST = """##
## BlackArch mirrorlist
##
## Europe
Server = https://fast.example/$repo/os/$arch
# Server = https://slow.example/$repo/os/$arch
Server = http://insecure.example/$repo/os/$arch
# Server = ftp://legacy.example/$repo/os/$arch
"""


def test_mirror_parser_keeps_unsupported_entries_visible() -> None:
    mirrors = parse_mirrorlist(MIRRORLIST)
    assert [mirror.enabled for mirror in mirrors] == [True, False, True, False]
    assert mirrors[0].supported is True
    assert mirrors[0].section == "Europe"
    assert mirrors[2].supported is False
    assert "http" in mirrors[2].reason
    assert mirrors[3].supported is False


class Response:
    def __init__(self, url: str, status: int = 200) -> None:
        self._url = url
        self.status = status
        self.closed = False

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


def test_mirror_test_and_recommendation_prefer_reachable_https() -> None:
    mirrors = parse_mirrorlist(MIRRORLIST)
    ticks = iter([1.0, 1.025])
    result = probe_mirror(
        mirrors[0],
        opener=lambda request, timeout: Response(request.full_url),
        clock=lambda: next(ticks),
    )
    insecure = probe_mirror(mirrors[2])
    assert result.status == "ok"
    assert result.latency_ms == 25.0
    assert result.tested_url.endswith("/blackarch.db")
    assert insecure.status == "unsupported"

    slower = MirrorTest(
        mirror=mirrors[1],
        status="ok",
        latency_ms=80.0,
        http_status=200,
    )
    assert recommend_mirror([slower, result]) == result


def test_mirror_test_reports_network_failure() -> None:
    mirror = parse_mirrorlist(MIRRORLIST)[0]
    ticks = iter([2.0, 2.1])
    result = probe_mirror(
        mirror,
        opener=lambda _request, timeout: (_ for _ in ()).throw(
            URLError("offline")
        ),
        clock=lambda: next(ticks),
    )
    assert result.status == "unreachable"
    assert "offline" in result.error


def _mirror_file(tmp_path: Path) -> tuple[Path, str]:
    target = tmp_path / "blackarch-mirrorlist"
    original = (
        "# BlackArch mirrorlist\n"
        "SigLevel = Required DatabaseOptional\n"
        "Server = https://old.example/$repo/os/$arch\n"
        "# Server = https://new.example/$repo/os/$arch\n"
        "Server = http://insecure.example/$repo/os/$arch\n"
    )
    target.write_text(original, encoding="utf-8")
    return target, original


def test_apply_mirror_is_approved_atomic_and_preserves_siglevel(
    tmp_path: Path,
) -> None:
    target, original = _mirror_file(tmp_path)
    selected = "https://new.example/$repo/os/$arch"
    result = apply_mirror(
        target,
        selected,
        approved=True,
        expected_path=target,
        now=lambda: datetime(2026, 7, 29, 10, 11, 12, tzinfo=timezone.utc),
    )
    updated = target.read_text(encoding="utf-8")
    assert result.backup.name.startswith(
        "blackarch-mirrorlist.bak.20260729T101112."
    )
    assert result.backup.read_text(encoding="utf-8") == original
    assert "SigLevel = Required DatabaseOptional" in updated
    assert "# Server = https://old.example/$repo/os/$arch" in updated
    assert "Server = https://new.example/$repo/os/$arch" in updated
    assert "# Server = http://insecure.example/$repo/os/$arch" in updated


def test_apply_requires_approval_and_exact_listed_file(tmp_path: Path) -> None:
    target, original = _mirror_file(tmp_path)
    with pytest.raises(MirrorError, match="approval"):
        apply_mirror(
            target,
            "https://new.example/$repo/os/$arch",
            approved=False,
            expected_path=target,
        )
    assert target.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.bak.*"))

    with pytest.raises(MirrorError, match="not present"):
        apply_mirror(
            target,
            "https://unknown.example/$repo/os/$arch",
            approved=True,
            expected_path=target,
        )
    assert not list(tmp_path.glob("*.bak.*"))

    unexpected = tmp_path / "expected-blackarch-mirrorlist"
    unexpected.write_text(original, encoding="utf-8")
    with pytest.raises(MirrorError, match="expected exactly"):
        apply_mirror(
            target,
            "https://new.example/$repo/os/$arch",
            approved=True,
            expected_path=unexpected,
        )
