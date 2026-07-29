from __future__ import annotations

import json
from datetime import date

import pytest

from blackforge.maintenance import (
    CURRENT_GROUP,
    NEEDS_ATTENTION_GROUP,
    MaintenanceError,
    MaintenanceEvidence,
    MaintenanceSnapshot,
    MaintenanceStatus,
    classify_maintenance,
    load_bundled_maintenance,
    maintenance_group,
    read_maintenance,
)
from blackforge.presets import (
    PresetError,
    bundled_presets,
    list_presets,
    resolve_preset,
    resolve_preset_packages,
)
from blackforge.sources import (
    ARCH_SOURCE_ID,
    ARCH_SOURCE_LABEL,
    SourceError,
    bundled_arch_catalog,
    is_curated_arch_tool,
    list_arch_tools,
    parse_arch_reference,
    resolve_arch_tool,
    resolve_arch_tools,
    validate_package_name,
    validate_repository,
)


def test_bundled_arch_catalog_is_curated_and_clearly_labeled() -> None:
    catalog = bundled_arch_catalog()
    assert catalog.label == ARCH_SOURCE_LABEL
    assert len(catalog.tools) >= 9
    assert len(catalog.tools) == len({tool.name for tool in catalog.tools})
    assert all(tool.source == ARCH_SOURCE_ID for tool in catalog.tools)
    assert all(tool.official_url.startswith("https://archlinux.org/packages/") for tool in catalog.tools)


def test_nmap_and_well_known_official_arch_tools_are_included() -> None:
    catalog = bundled_arch_catalog()
    expected = {
        "nmap",
        "masscan",
        "tcpdump",
        "wireshark-cli",
        "wireshark-qt",
        "aircrack-ng",
        "hashcat",
        "john",
        "sqlmap",
    }
    assert expected <= set(catalog.by_name)
    nmap = resolve_arch_tool("nmap")
    assert nmap.repository == "extra"
    assert nmap.package_target == "extra/nmap"
    assert nmap.source_label == ARCH_SOURCE_LABEL


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("nmap", (None, "nmap")),
        ("extra/nmap", ("extra", "nmap")),
        ("arch:extra/nmap", ("extra", "nmap")),
    ],
)
def test_safe_arch_reference_forms(
    reference: str,
    expected: tuple[str | None, str],
) -> None:
    assert parse_arch_reference(reference) == expected
    assert resolve_arch_tool(reference).name == "nmap"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "../nmap",
        "extra/../nmap",
        "extra/nmap/other",
        "aur/nmap",
        "evil:extra/nmap",
        "extra/nmap;id",
        "extra/$(id)",
    ],
)
def test_unsafe_or_unsupported_arch_references_are_rejected(reference: str) -> None:
    with pytest.raises(SourceError):
        parse_arch_reference(reference)


def test_repository_qualification_must_match_curated_source() -> None:
    with pytest.raises(SourceError, match="curated from extra"):
        resolve_arch_tool("core/nmap")


def test_package_and_repository_validation() -> None:
    assert validate_package_name("wireshark-cli") == "wireshark-cli"
    assert validate_repository("extra") == "extra"
    with pytest.raises(SourceError):
        validate_package_name("nmap;id")
    with pytest.raises(SourceError):
        validate_repository("aur")


def test_arch_listing_and_resolution_are_deterministic() -> None:
    scanners = list_arch_tools(category="network-scanner")
    assert [tool.name for tool in scanners] == ["masscan", "nmap"]
    assert resolve_arch_tools(["nmap", "extra/nmap", "tcpdump"]) == [
        resolve_arch_tool("nmap"),
        resolve_arch_tool("tcpdump"),
    ]
    assert is_curated_arch_tool("nmap") is True
    assert is_curated_arch_tool("not-curated") is False


def test_maintenance_three_year_boundary_is_explicit() -> None:
    as_of = date(2026, 7, 29)
    assert (
        classify_maintenance(
            date(2023, 7, 30),
            as_of=as_of,
            stale_years=3,
        )
        is MaintenanceStatus.CURRENT
    )
    assert (
        classify_maintenance(
            date(2023, 7, 29),
            as_of=as_of,
            stale_years=3,
        )
        is MaintenanceStatus.STALE
    )


def test_maintenance_supports_five_year_cutoff() -> None:
    as_of = date(2026, 7, 29)
    activity = date(2022, 1, 1)
    assert (
        classify_maintenance(activity, as_of=as_of, stale_years=3)
        is MaintenanceStatus.STALE
    )
    assert (
        classify_maintenance(activity, as_of=as_of, stale_years=5)
        is MaintenanceStatus.CURRENT
    )
    with pytest.raises(MaintenanceError):
        classify_maintenance(activity, as_of=as_of, stale_years=4)


def test_unknown_is_never_inferred_as_stale() -> None:
    assert (
        classify_maintenance(None, as_of=date(2026, 7, 29), stale_years=3)
        is MaintenanceStatus.UNKNOWN
    )
    unknown = MaintenanceEvidence.unknown()
    assert unknown.status is MaintenanceStatus.UNKNOWN
    assert unknown.top_group == NEEDS_ATTENTION_GROUP
    assert unknown.reclassified(stale_years=5).status is MaintenanceStatus.UNKNOWN
    assert maintenance_group(MaintenanceStatus.STALE) == NEEDS_ATTENTION_GROUP
    assert maintenance_group(MaintenanceStatus.ARCHIVED) == NEEDS_ATTENTION_GROUP
    assert maintenance_group(MaintenanceStatus.CURRENT) == CURRENT_GROUP


def test_archived_status_is_preserved_across_cutoffs() -> None:
    archived = MaintenanceEvidence(
        status=MaintenanceStatus.ARCHIVED,
        last_activity=date(2020, 1, 1),
        checked_at=date(2026, 7, 29),
        evidence_url="https://github.com/example/tool",
        evidence_kind="repository",
        confidence="high",
    )
    assert archived.reclassified(stale_years=5).status is MaintenanceStatus.ARCHIVED
    assert archived.top_group == NEEDS_ATTENTION_GROUP


def test_maintenance_snapshot_reclassifies_current_and_stale_only() -> None:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-07-29",
        "source": "https://example.test/maintenance.json",
        "record_count": 3,
        "records": {
            "new": {
                "status": "current",
                "last_activity": "2024-01-01",
                "checked_at": "2026-07-29",
                "evidence_url": "https://github.com/example/new",
                "evidence_kind": "repository",
                "confidence": "high",
            },
            "old": {
                "status": "stale",
                "last_activity": "2022-01-01",
                "checked_at": "2026-07-29",
                "evidence_url": "https://github.com/example/old",
                "evidence_kind": "repository",
                "confidence": "high",
            },
            "unknown": {
                "status": "unknown",
                "last_activity": None,
                "checked_at": "2026-07-29",
                "confidence": "none",
            },
        },
    }
    snapshot = MaintenanceSnapshot.from_dict(
        payload,
        stale_years=3,
        as_of=date(2026, 7, 29),
    )
    assert snapshot.for_tool("new").status is MaintenanceStatus.CURRENT
    assert snapshot.for_tool("old").status is MaintenanceStatus.STALE
    assert snapshot.for_tool("unknown").status is MaintenanceStatus.UNKNOWN
    assert snapshot.for_tool("missing").status is MaintenanceStatus.UNKNOWN
    assert set(snapshot.grouped()[CURRENT_GROUP]) == {"new"}
    assert set(snapshot.grouped()[NEEDS_ATTENTION_GROUP]) == {"old", "unknown"}

    five_year = snapshot.reclassified(
        stale_years=5,
        as_of=date(2026, 7, 29),
    )
    assert five_year.for_tool("old").status is MaintenanceStatus.CURRENT
    assert five_year.for_tool("unknown").status is MaintenanceStatus.UNKNOWN


def test_read_maintenance_and_optional_bundled_loader(tmp_path) -> None:
    target = tmp_path / "maintenance.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-07-29T12:00:00Z",
                "source": "GitHub GraphQL repository activity metadata",
                "records": {
                    "nmap": {
                        "status": "current",
                        "last_activity_at": "2026-07-19T13:50:00Z",
                        "evidence_url": "https://nmap.org/",
                        "evidence_kind": "release",
                        "confidence": "high",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    snapshot = read_maintenance(
        target,
        as_of=date(2026, 7, 29),
        stale_years=3,
    )
    assert snapshot.generated_at == date(2026, 7, 29)
    assert snapshot.for_tool("nmap").status is MaintenanceStatus.CURRENT

    # maintenance.json is generated separately and is intentionally optional.
    bundled = load_bundled_maintenance(required=False)
    assert isinstance(bundled, MaintenanceSnapshot)


def test_malformed_maintenance_evidence_is_rejected() -> None:
    with pytest.raises(MaintenanceError):
        MaintenanceEvidence(status=MaintenanceStatus.STALE)
    with pytest.raises(MaintenanceError):
        MaintenanceEvidence(
            status=MaintenanceStatus.UNKNOWN,
            last_activity=date(2020, 1, 1),
        )
    with pytest.raises(MaintenanceError):
        MaintenanceEvidence(
            status=MaintenanceStatus.CURRENT,
            last_activity=date(2026, 1, 1),
            evidence_url="file:///tmp/tool",
        )


def test_bundled_presets_resolve_only_known_packages() -> None:
    presets = bundled_presets()
    assert len(presets) >= 7
    assert len(presets) == len({preset.id for preset in presets})
    assert "network-discovery" in {preset.id for preset in presets}
    for preset in presets:
        resolved = resolve_preset_packages(preset)
        assert len(resolved) == len(preset.packages)
        assert all(package.package_target for package in resolved)

    network = resolve_preset_packages("network-discovery")
    assert any(package.name == "nmap" and package.source == ARCH_SOURCE_ID for package in network)
    assert any(package.name == "amass" and package.source == "blackarch" for package in network)


def test_preset_filtering_and_errors() -> None:
    assert resolve_preset("packet-analysis") in list_presets(category="network")
    with pytest.raises(PresetError):
        resolve_preset("../packet-analysis")
    with pytest.raises(PresetError):
        list_presets(category="../network")
