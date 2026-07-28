from __future__ import annotations

import pytest

from blackforge.completion import script


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_scripts_reference_blackforge(shell: str) -> None:
    value = script(shell)
    assert "blackforge" in value
    assert "install" in value


def test_unknown_completion_shell() -> None:
    with pytest.raises(ValueError):
        script("powershell")

