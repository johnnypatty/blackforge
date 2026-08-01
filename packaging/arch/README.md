# Native Arch Linux package

This directory contains a self-contained `makepkg` recipe for BlackForge.

```bash
sudo pacman -S --needed base-devel python-build python-installer python-setuptools python-wheel
cd packaging/arch
makepkg -si
blackforge setup
```

The resulting `blackforge` package installs the CLI to `/usr/bin/blackforge`,
the `blackforge(1)` man page, completion files for bash/zsh/fish, and a disabled
optional systemd user timer. Tool installation and removal are still delegated
to pacman.

Optional integrations:

```bash
sudo pacman -S arch-audit  # official security advisories for `blackforge audit`
sudo pacman -S snapper     # Btrfs snapshot support when a root config exists
systemctl --user enable --now blackforge-update.timer
```

The timer is never enabled by package installation.

The recipe downloads the versioned source archive from the matching GitHub
release. The release workflow builds that archive reproducibly and verifies its
SHA-256 against this `PKGBUILD` before publishing it.
