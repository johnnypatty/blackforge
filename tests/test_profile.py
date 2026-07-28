from __future__ import annotations

from blackforge.profile import read_profile, write_profile


def test_profile_round_trip(tmp_path) -> None:
    target = tmp_path / "lab.json"
    write_profile(target, "lab", ["nmap", "wireshark", "nmap"])
    name, packages = read_profile(target)
    assert name == "lab"
    assert packages == ["nmap", "wireshark"]

