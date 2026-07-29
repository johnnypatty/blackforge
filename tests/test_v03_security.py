from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from blackforge import cli
from blackforge.catalog import CatalogError, bundled_catalog
from blackforge.environment import (
    EnvironmentFileError,
    export_environment,
)
from blackforge.history import (
    HistoryError,
    HistoryStore,
    make_history_record,
)
from blackforge.mirrors import MirrorError, apply_mirror
from blackforge.self_update import (
    ReleaseAsset,
    ReleaseInfo,
    SelfUpdateError,
    _checksum_for,
    apply_release,
)
from blackforge.transactions import TransactionJournal
from blackforge.updates import UpdateError, UpdateReport


class _Response:
    def __init__(self, data: bytes, final_url: str, status: int = 200) -> None:
        self.data = data
        self.final_url = final_url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.data if limit < 0 else self.data[:limit]

    def geturl(self) -> str:
        return self.final_url


def _script_install(tmp_path: Path) -> tuple[Path, Path]:
    install_root = tmp_path / "install"
    interpreter = install_root / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    (install_root / ".blackforge-install").write_text(
        "blackforge-user-install-v1\n",
        encoding="utf-8",
    )
    return install_root, interpreter


def _release(
    wheel_name: str,
    *,
    wheel_url: str = "https://github.com/example/wheel",
    checksum_url: str = "https://github.com/example/checksums",
) -> ReleaseInfo:
    return ReleaseInfo(
        version="999.0.0",
        page_url="https://github.com/johnnypatty/blackforge/releases/tag/v999.0.0",
        published_at="2026-07-29T00:00:00Z",
        assets=(
            ReleaseAsset(wheel_name, wheel_url, 5),
            ReleaseAsset("SHA256SUMS", checksum_url, 128),
        ),
    )


def test_self_update_rejects_asset_redirect_to_untrusted_https_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An allowlisted initial URL must not authorize an arbitrary redirect."""

    wheel_name = "blackforge_cli-999.0.0-py3-none-any.whl"
    wheel_data = b"wheel"
    checksum = hashlib.sha256(wheel_data).hexdigest()
    install_root, interpreter = _script_install(tmp_path)
    monkeypatch.setattr(
        "blackforge.self_update._user_install_paths",
        lambda: (install_root, interpreter),
    )

    def fake_urlopen(request, timeout):
        del timeout
        if request.full_url.endswith("wheel"):
            return _Response(
                wheel_data,
                "https://untrusted.example/redirected.whl",
            )
        return _Response(
            f"{checksum}  {wheel_name}\n".encode(),
            request.full_url,
        )

    monkeypatch.setattr(
        "blackforge.self_update.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "blackforge.self_update.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(SelfUpdateError, match="host|redirect"):
        apply_release(_release(wheel_name))


def test_self_update_rejects_path_like_wheel_asset_name_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Release asset names must be basenames inside the private temp directory."""

    escaped = tmp_path / "blackforge_cli-999.0.0-py3-none-any.whl"
    wheel_name = str(escaped)
    wheel_data = b"wheel"
    checksum = hashlib.sha256(wheel_data).hexdigest()
    install_root, interpreter = _script_install(tmp_path)
    monkeypatch.setattr(
        "blackforge.self_update._user_install_paths",
        lambda: (install_root, interpreter),
    )

    def fake_request(url: str, *, limit: int) -> bytes:
        del limit
        if url.endswith("wheel"):
            return wheel_data
        return f"{checksum}  {wheel_name}\n".encode()

    monkeypatch.setattr("blackforge.self_update._request_bytes", fake_request)
    monkeypatch.setattr(
        "blackforge.self_update.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(SelfUpdateError, match="name|path|asset"):
        apply_release(_release(wheel_name))
    assert not escaped.exists()


def test_self_update_rejects_ambiguous_checksum_entries() -> None:
    filename = "blackforge_cli-999.0.0-py3-none-any.whl"
    first = "1" * 64
    second = "2" * 64
    with pytest.raises(SelfUpdateError, match="duplicate|ambiguous"):
        _checksum_for(
            f"{first}  {filename}\n{second}  {filename}\n",
            filename,
        )


def test_self_update_rejects_an_unrelated_versioned_wheel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root, interpreter = _script_install(tmp_path)
    monkeypatch.setattr(
        "blackforge.self_update._user_install_paths",
        lambda: (install_root, interpreter),
    )
    monkeypatch.setattr(
        "blackforge.self_update.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("an unrelated wheel must not run pip"),
    )

    with pytest.raises(SelfUpdateError, match="exact BlackForge wheel"):
        apply_release(_release("unrelated_tool-999.0.0-py3-none-any.whl"))


def test_self_update_verifies_the_installed_distribution_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel_name = "blackforge_cli-999.0.0-py3-none-any.whl"
    wheel_data = b"wheel"
    checksum = hashlib.sha256(wheel_data).hexdigest()
    install_root, interpreter = _script_install(tmp_path)
    monkeypatch.setattr(
        "blackforge.self_update._user_install_paths",
        lambda: (install_root, interpreter),
    )

    def fake_request(
        url: str,
        *,
        limit: int,
        allowed_hosts: set[str],
    ) -> bytes:
        del limit, allowed_hosts
        if url.endswith("wheel"):
            return wheel_data
        return f"{checksum}  {wheel_name}\n".encode()

    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if "-m" in args and "pip" in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="998.0.0", stderr="")

    monkeypatch.setattr("blackforge.self_update._request_bytes", fake_request)
    monkeypatch.setattr("blackforge.self_update.subprocess.run", fake_run)

    with pytest.raises(SelfUpdateError, match="expected.*999\\.0\\.0.*998\\.0\\.0"):
        apply_release(_release(wheel_name))
    assert len(calls) == 2
    assert calls[1][1:3] == ["-I", "-c"]


def _mirror_file(tmp_path: Path, *, selected_enabled: bool = False) -> Path:
    target = tmp_path / "blackarch-mirrorlist"
    selected_prefix = "" if selected_enabled else "# "
    old_prefix = "# " if selected_enabled else ""
    target.write_text(
        "SigLevel = Required DatabaseOptional\n"
        f"{old_prefix}Server = https://old.example/$repo/os/$arch\n"
        f"{selected_prefix}Server = https://new.example/$repo/os/$arch\n",
        encoding="utf-8",
    )
    return target


def test_apply_mirror_requires_literal_boolean_approval(tmp_path: Path) -> None:
    target = _mirror_file(tmp_path)
    original = target.read_bytes()
    with pytest.raises(MirrorError, match="approval"):
        apply_mirror(
            target,
            "https://new.example/$repo/os/$arch",
            approved=1,  # type: ignore[arg-type]
            expected_path=target,
        )
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("blackarch-mirrorlist.bak.*"))


def test_apply_mirror_noop_does_not_write_or_create_backup(tmp_path: Path) -> None:
    target = _mirror_file(tmp_path, selected_enabled=True)
    original = target.read_bytes()
    result = apply_mirror(
        target,
        "https://new.example/$repo/os/$arch",
        approved=True,
        expected_path=target,
        now=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    assert result.changed is False
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("blackarch-mirrorlist.bak.*"))


def test_update_report_rejects_non_object_change_entries() -> None:
    payload = {
        "schema_version": 1,
        "checked_at": "2026-07-29T00:00:00+00:00",
        "old_count": 1,
        "new_count": 1,
        "added": [],
        "removed": [],
        "changed": [42],
        "has_changes": True,
    }
    with pytest.raises(UpdateError, match="changed|Malformed"):
        UpdateReport.from_dict(payload)


class _PackageBackend:
    assume_yes = True
    supported = True

    def __init__(self, installed: dict[str, str] | None = None) -> None:
        self.installed = dict(installed or {})
        self.mutations: list[tuple[str, ...]] = []

    def require_supported(self) -> None:
        return None

    def installed_packages(self) -> dict[str, str]:
        return dict(self.installed)

    def plan_metadata(self, _operation: str, _targets: tuple[str, ...]):
        return None

    def install(self, targets) -> SimpleNamespace:
        self.mutations.append(tuple(targets))
        return SimpleNamespace(returncode=0, stderr="")

    def remove(self, targets) -> SimpleNamespace:
        self.mutations.append(tuple(targets))
        return SimpleNamespace(returncode=0, stderr="")


def test_environment_apply_cannot_spoof_core_package_as_blackarch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "spoofed-environment.json"
    export_environment(
        manifest,
        "spoofed",
        {"blackarch:pacman": "999-1"},
        created_at="2026-07-29T00:00:00+00:00",
    )
    backend = _PackageBackend()
    attempted: list[str] = []

    def record_operation(_backend, _action, targets, _execute, **_kwargs):
        attempted.extend(targets)
        return 0

    monkeypatch.setattr(cli, "_recorded_package_operation", record_operation)
    args = SimpleNamespace(
        environment_command="import",
        path=manifest,
        json=False,
        apply=True,
        dry_run=False,
        allow_newer=True,
        yes=True,
    )

    with pytest.raises(
        (EnvironmentFileError, CatalogError),
        match="BlackArch|package|catalog|source",
    ):
        cli._handle_environment(args, bundled_catalog(), backend)
    assert attempted == []
    assert backend.mutations == []


def test_history_undo_cannot_remove_spoofed_blackarch_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.append(
        make_history_record(
            "tampered-history",
            "install",
            {"blackarch:pacman": None},
            {"blackarch:pacman": "999-1"},
            created_at="2026-07-29T00:00:00+00:00",
        )
    )
    backend = _PackageBackend({"pacman": "999-1"})
    attempted: list[str] = []

    def record_operation(_backend, _action, targets, _execute, **_kwargs):
        attempted.extend(targets)
        return 0

    monkeypatch.setattr(cli, "HistoryStore", lambda: store)
    monkeypatch.setattr(cli, "_recorded_package_operation", record_operation)
    args = SimpleNamespace(
        history_command="undo",
        transaction_id="tampered-history",
        json=False,
        apply=True,
        dry_run=False,
        yes=True,
    )

    with pytest.raises(
        (HistoryError, CatalogError),
        match="BlackArch|package|catalog|source",
    ):
        cli._handle_history(args, backend)
    assert attempted == []
    assert backend.mutations == []


def test_history_undo_refuses_package_state_that_changed_after_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.append(
        make_history_record(
            "old-install",
            "install",
            {"blackarch:amass": None},
            {"blackarch:amass": "1.0-1"},
            created_at="2026-07-29T00:00:00+00:00",
        )
    )
    backend = _PackageBackend({"amass": "2.0-1"})
    attempted: list[str] = []

    def record_operation(_backend, _action, targets, _execute, **_kwargs):
        attempted.extend(targets)
        return 0

    monkeypatch.setattr(cli, "HistoryStore", lambda: store)
    monkeypatch.setattr(cli, "_recorded_package_operation", record_operation)
    args = SimpleNamespace(
        history_command="undo",
        transaction_id="old-install",
        json=False,
        apply=True,
        dry_run=False,
        yes=True,
    )

    with pytest.raises(HistoryError, match="changed|version|state"):
        cli._handle_history(args, backend)
    assert attempted == []


def test_environment_apply_requires_allow_newer_for_uninstalled_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "version-drift.json"
    export_environment(
        manifest,
        "version-drift",
        {"blackarch:amass": "1.0-1"},
        created_at="2026-07-29T00:00:00+00:00",
    )

    class AvailableVersionBackend(_PackageBackend):
        def plan_metadata(self, _operation: str, _targets: tuple[str, ...]):
            return {
                "packages": {
                    "amass": {
                        "name": "amass",
                        "version": "2.0-1",
                        "repository": "blackarch",
                    }
                }
            }

    backend = AvailableVersionBackend()
    attempted: list[str] = []

    def record_operation(_backend, _action, targets, _execute, **_kwargs):
        attempted.extend(targets)
        return 0

    monkeypatch.setattr(cli, "_recorded_package_operation", record_operation)
    args = SimpleNamespace(
        environment_command="import",
        path=manifest,
        json=False,
        apply=True,
        dry_run=False,
        allow_newer=False,
        yes=True,
    )

    with pytest.raises(EnvironmentFileError, match="Version|version|allow-newer"):
        cli._handle_environment(args, bundled_catalog(), backend)
    assert attempted == []


def test_keyboard_interrupt_marks_package_transaction_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = TransactionJournal(tmp_path / "transactions.json")
    backend = _PackageBackend()
    monkeypatch.setattr(cli, "TransactionJournal", lambda: journal)

    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cli._recorded_package_operation(
            backend,
            "install",
            ["amass"],
            interrupted,
        )
    record = journal.records()[0]
    assert record.status == "failed"
    assert record.retryable is False


def test_mirror_symlink_target_is_never_replaced(tmp_path: Path) -> None:
    real = _mirror_file(tmp_path)
    link_dir = tmp_path / "links"
    link_dir.mkdir()
    link = link_dir / "blackarch-mirrorlist"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this test environment")
    original = real.read_bytes()
    with pytest.raises(MirrorError, match="symbolic"):
        apply_mirror(
            link,
            "https://new.example/$repo/os/$arch",
            approved=True,
            expected_path=link,
        )
    assert real.read_bytes() == original
    assert link.is_symlink()


def test_malformed_update_report_boolean_counts_are_rejected() -> None:
    payload = {
        "schema_version": 1,
        "checked_at": "2026-07-29T00:00:00+00:00",
        "old_count": True,
        "new_count": False,
        "added": [],
        "removed": [],
        "changed": [],
    }
    with pytest.raises(UpdateError, match="count|Malformed"):
        UpdateReport.from_dict(json.loads(json.dumps(payload)))
