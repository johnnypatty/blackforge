from __future__ import annotations

import pytest

from blackforge.profile import ProfileError, read_profile, write_profile


def test_profile_round_trip(tmp_path) -> None:
    target = tmp_path / "lab.json"
    write_profile(target, "lab", ["nmap", "wireshark", "nmap"])
    name, packages = read_profile(target)
    assert name == "lab"
    assert packages == ["nmap", "wireshark"]


@pytest.mark.parametrize("content", ["[]", '{"schema_version": 1, "packages": []}'])
def test_profile_rejects_malformed_or_empty_data(tmp_path, content: str) -> None:
    target = tmp_path / "bad.json"
    target.write_text(content, encoding="utf-8")
    with pytest.raises(ProfileError):
        read_profile(target)
