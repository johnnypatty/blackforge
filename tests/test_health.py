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

