from __future__ import annotations

from blackforge.backend import PacmanBackend
from blackforge.health import audit_tools
from blackforge.models import Tool


def test_audit_is_honest_when_pacman_unavailable(monkeypatch) -> None:
    backend = PacmanBackend()
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: False))
    audit = audit_tools(
        [Tool("nmap", "7", "scanner", "blackarch-scanner")],
        backend,
    )
    assert audit.counts == {"unverified": 1}
    assert "Arch Linux" in audit.note
    assert audit.exit_code == 3


def test_live_audit_classifies_available_missing_and_installed(monkeypatch) -> None:
    backend = PacmanBackend()
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: True))
    monkeypatch.setattr(PacmanBackend, "repo_enabled", property(lambda self: True))
    monkeypatch.setattr(
        backend,
        "available_packages",
        lambda: {"available": "1", "installed": "2"},
    )
    monkeypatch.setattr(backend, "installed_packages", lambda: {"installed": "2"})
    tools = [
        Tool("available", "1", "", "blackarch-misc"),
        Tool("installed", "2", "", "blackarch-misc"),
        Tool("gone", "3", "", "blackarch-misc"),
    ]
    audit = audit_tools(tools, backend)
    assert [state.status for state in audit.states] == [
        "available",
        "installed",
        "missing-from-repo",
    ]
    assert audit.exit_code == 3


def test_local_version_comparison_ignores_epoch_and_pkgrel(monkeypatch) -> None:
    backend = PacmanBackend()
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: True))
    monkeypatch.setattr(PacmanBackend, "repo_enabled", property(lambda self: True))
    monkeypatch.setattr(backend, "available_packages", lambda: {"tool": "1:1.0-2"})
    monkeypatch.setattr(backend, "installed_packages", dict)
    audit = audit_tools(
        [Tool("tool", "1.0", "", "blackarch-misc")],
        backend,
    )
    assert audit.exit_code == 0
    assert "differs" not in audit.states[0].note
