# Native Arch Linux package

This directory contains a self-contained `makepkg` recipe for BlackForge.

```bash
sudo pacman -S --needed base-devel python-build python-installer python-setuptools python-wheel
cd packaging/arch
makepkg -si
blackforge setup
```

The resulting `blackforge` package installs the CLI to `/usr/bin/blackforge`
and completion files for bash, zsh, and fish. Tool installation and removal are
still delegated to pacman.

The bundled source archive and its checksum must be refreshed together for each
BlackForge release.

