from __future__ import annotations

import io
import tarfile

import pytest

from blackforge.health import audit_repository_snapshot
from blackforge.models import Tool
from blackforge.repository import (
    RepositoryError,
    RepositorySnapshot,
    package_base_version,
    parse_repository_database,
)


def _database(count: int = 1000) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for index in range(count):
            name = f"package-{index}"
            content = f"%NAME%\n{name}\n\n%VERSION%\n1:{index}.0-2\n\n".encode()
            info = tarfile.TarInfo(f"{name}-{index}/desc")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_repository_database_parser() -> None:
    snapshot = parse_repository_database(_database())
    assert len(snapshot.packages) == 1000
    assert snapshot.packages["package-5"] == "1:5.0-2"


def test_repository_database_rejects_incomplete_data() -> None:
    with pytest.raises(RepositoryError, match="Only"):
        parse_repository_database(_database(2))


def test_package_base_version() -> None:
    assert package_base_version("1:263.2d723ae-2") == "263.2d723ae"
    assert package_base_version("2.0-beta-1") == "2.0-beta"


def test_remote_audit() -> None:
    snapshot = RepositorySnapshot(
        packages={"present": "1:1.0-1"},
        source="test",
        fetched_at="now",
    )
    audit = audit_repository_snapshot(
        [
            Tool("present", "1.0", "", "blackarch-misc"),
            Tool("absent", "1", "", "blackarch-misc"),
        ],
        snapshot,
    )
    assert audit.counts == {"available": 1, "missing-from-repo": 1}

