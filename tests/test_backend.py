from __future__ import annotations

import pytest

from blackforge.backend import BackendError, PacmanBackend, validate_package_names


def test_package_name_validation() -> None:
    assert validate_package_names(["nmap", "python-tool_git", "lib32-thing"]) == [
        "nmap",
        "python-tool_git",
        "lib32-thing",
    ]


@pytest.mark.parametrize("name", ["", "nmap;id", "../pkg", "pkg name", "$(id)"])
def test_package_name_rejects_shell_syntax(name: str) -> None:
    with pytest.raises(BackendError):
        validate_package_names([name])


def test_non_linux_backend_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("blackforge.backend.platform.system", lambda: "Windows")
    assert PacmanBackend().supported is False


def test_install_command_is_list_form(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PacmanBackend(dry_run=True, assume_yes=True)
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: True))
    monkeypatch.setattr(backend, "_privileged", lambda args: ["sudo", *args])
    result = backend.install(["nmap"])
    assert result.planned is True
    assert result.args == ["sudo", "pacman", "-S", "--needed", "--noconfirm", "nmap"]

