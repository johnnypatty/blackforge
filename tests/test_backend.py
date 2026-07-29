from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from blackforge.backend import (
    BackendError,
    CommandResult,
    PacmanBackend,
    Runner,
    validate_package_names,
)


def test_package_name_validation() -> None:
    assert validate_package_names(
        ["nmap", "python-tool_git", "nmap", "lib32-thing"]
    ) == [
        "nmap",
        "python-tool_git",
        "lib32-thing",
    ]


@pytest.mark.parametrize(
    "name",
    ["", "nmap;id", "../pkg", "pkg name", "$(id)", "--noconfirm", "-Rns", ":"],
)
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
    assert result.args == [
        "sudo",
        "pacman",
        "-S",
        "--needed",
        "--noconfirm",
        "--",
        "nmap",
    ]


def test_runner_wraps_operating_system_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "blackforge.backend.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(BackendError, match="Unable to run pacman"):
        Runner().run(["pacman", "-Q"])


def test_dry_run_does_not_suppress_read_only_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "blackforge.backend.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, "result", ""),
    )
    result = Runner(dry_run=True).run(["pacman", "-Q"], capture=True)
    assert result.planned is False
    assert result.stdout == "result"


def test_runner_tee_captures_and_streams_both_outputs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = Runner().run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "print('visible stdout'); "
                "print('failed retrieving file', file=sys.stderr)"
            ),
        ],
        tee=True,
    )

    displayed = capsys.readouterr()
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["visible stdout"]
    assert result.stderr.splitlines() == ["failed retrieving file"]
    assert displayed.out == result.stdout
    assert displayed.err == result.stderr


@pytest.mark.parametrize("operation", ["install", "remove", "upgrade"])
def test_pacman_mutations_capture_while_streaming(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = PacmanBackend(assume_yes=True)
    monkeypatch.setattr(PacmanBackend, "supported", property(lambda self: True))
    monkeypatch.setattr(backend, "_privileged", list)
    calls: list[dict[str, object]] = []

    def run(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        return CommandResult(list(args), 0)

    monkeypatch.setattr(backend.runner, "run", run)
    getattr(backend, operation)(["nmap"])

    assert calls[0]["mutating"] is True
    assert calls[0]["tee"] is True


def test_package_executable_query_failure_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = PacmanBackend()
    monkeypatch.setattr(
        backend.runner,
        "run",
        lambda *args, **kwargs: CommandResult(["pacman"], 1, stderr="not installed"),
    )
    with pytest.raises(BackendError, match="Unable to inspect"):
        backend.package_executables("nmap")


def test_repo_configuration_follows_include_files(tmp_path: Path) -> None:
    included = tmp_path / "repositories.conf"
    included.write_text("[blackarch]\nServer = https://example.test/\n", encoding="utf-8")
    config = tmp_path / "pacman.conf"
    config.write_text(f"Include = {included}\n", encoding="utf-8")
    assert PacmanBackend()._config_has_repository(config, "blackarch") is True


def test_changed_strap_requires_exact_reviewed_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"#!/bin/sh\nprintf test\n"
    digest = hashlib.sha256(data).hexdigest()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self) -> str:
            return "https://blackarch.org/strap.sh"

        def read(self, _limit: int) -> bytes:
            return data

    monkeypatch.setattr("blackforge.backend.urllib.request.urlopen", lambda *a, **k: Response())
    backend = PacmanBackend()
    with pytest.raises(BackendError, match="strap-sha256"):
        backend.download_strap()
    path, actual_digest, _, matched = backend.download_strap(reviewed_sha256=digest)
    try:
        assert actual_digest == digest
        assert matched is False
    finally:
        path.unlink()
