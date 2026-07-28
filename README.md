# BlackForge

BlackForge is an unofficial, zero-dependency package navigator for the official
[BlackArch tools repository](https://www.blackarch.org/tools.html). It gives
the complete BlackArch catalog a searchable CLI, guided menu, reproducible
profiles, safe install/remove commands, and an evidence-based health report.

It is not affiliated with or endorsed by the BlackArch project. “BlackArch” is
used descriptively to identify the repository it manages.

## Why this shape

The repository does **not** copy thousands of upstream projects. Vendoring them
would become stale immediately, lose upstream fixes, and create a licensing and
supply-chain mess. BlackForge bundles one generated metadata snapshot and asks
`pacman` to install signed packages from BlackArch's own repository.

The bundled snapshot was generated from the live BlackArch catalog and contains
all 2,861 entries visible on the site at generation time. `blackforge sync`
refreshes it without waiting for a BlackForge release.

The included [package-availability report](reports/README.md) compares every
entry with a live x86_64 repository snapshot.

## Highlights

- Complete official catalog, not a hand-curated subset
- Fast search across package name, description, and category
- Install one package, several packages, a category, or a saved profile
- Remove conservatively by default; dependency cleanup requires `--purge`
- Full-system upgrades use `pacman -Syu`, avoiding unsafe partial upgrades
- Dry-run and JSON modes for automation
- Repository doctor and explicit, inspectable setup flow
- Honest health statuses for all catalog packages
- No arbitrary project installers and no automatic execution of security tools
- Weekly catalog-freshness and repository-availability workflows
- Python standard library only at runtime

## Arch Linux quick start

BlackForge's package-changing commands support Arch Linux and BlackArch.
Browsing, search, export, and offline catalog inspection work anywhere with
Python 3.10+.

```bash
git clone https://github.com/johnnypatty/blackforge.git
cd blackforge
bash install.sh
```

Make sure `~/.local/bin` is on your `PATH`, then initialize the official
BlackArch repository once:

```bash
blackforge setup
```

After that, every BlackArch tool is a normal command:

```bash
blackforge search "subdomain enumeration"
blackforge show amass
blackforge --dry-run install amass 0trace
blackforge install amass 0trace
blackforge remove amass
blackforge list --category blackarch-forensic --limit 30
```

You can also initialize the repository and install a tool in one operation:

```bash
blackforge install --setup-repo amass
```

Short aliases are available:

```bash
blackforge get amass
blackforge rm amass
blackforge check amass
```

Run `blackforge` with no arguments in a terminal, or
`blackforge interactive`, for the guided menu.

The installer creates an isolated environment under
`~/.local/share/blackforge`, puts the command in `~/.local/bin`, and installs
bash, zsh, and fish completion. It does not modify system Python.

To remove BlackForge itself:

```bash
bash uninstall.sh
```

This leaves any BlackArch packages you installed untouched.

If you prefer a native pacman-managed BlackForge installation, a self-contained
[`PKGBUILD`](packaging/arch/README.md) is included:

```bash
cd packaging/arch
makepkg -si
```

## Current health, without pretending

There is no safe universal test that can launch 2,861 security programs and
declare each “working.” BlackForge checks what can be established consistently:

```bash
blackforge status --all
blackforge status --all --executables
blackforge --json status --all --remote --output blackforge-audit.json
```

The first command uses the local pacman database. The second additionally
verifies installed `/usr/bin` files without executing them. The third works on
any OS and compares every website entry with the live official x86_64
repository database. See [the health model](docs/HEALTH-MODEL.md) for exact
meanings.

## Reproducible tool sets

```bash
blackforge profile create web-lab.json amass nuclei httpx
blackforge profile show web-lab.json
blackforge --dry-run profile apply web-lab.json
blackforge profile apply web-lab.json
```

Profiles contain package names only. They are easy to review, share, and pin in
a lab repository.

## Safety

Use security tooling only on systems you own or are explicitly authorized to
test. BlackForge manages packages; it does not provide attack automation,
targets, payloads, or command execution.

Package names are checked against the official catalog and passed to
`subprocess` as a list, never through a shell. BlackForge asks before state
changes unless `--yes` is supplied.

## Development

```bash
python -m pip install -e .
python -m pip install pytest
python scripts/update_catalog.py
pytest
```

GitHub Actions includes an Arch Linux container test that enables the official
repository, installs `0trace` through BlackForge, verifies it with pacman, and
removes it again.

See [all commands](docs/COMMANDS.md), [contributing](CONTRIBUTING.md), and
[security](SECURITY.md).
