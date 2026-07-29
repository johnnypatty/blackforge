<p align="center">
  <img src="docs/assets/blackforge-logo.svg" width="132" alt="BlackForge logo">
</p>

<h1 align="center">BlackForge</h1>

<p align="center">
  A safety-minded Arch Linux command center for discovering, planning,
  installing, auditing, and reproducing security-tool environments.
</p>

<p align="center">
  <a href="https://github.com/johnnypatty/blackforge/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/johnnypatty/blackforge/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Arch Linux" src="https://img.shields.io/badge/platform-Arch%20Linux-1793D1?logo=archlinux&logoColor=white">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Runtime dependencies: zero" src="https://img.shields.io/badge/runtime%20dependencies-zero-22C55E">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-EA580C"></a>
</p>

> [!IMPORTANT]
> BlackForge is an **unofficial** project. It is not affiliated with or endorsed
> by BlackArch or Arch Linux. “BlackArch” and “Arch Linux” are used
> descriptively to identify the package repositories BlackForge works with.

> [!WARNING]
> Use security tools only on systems you own or are explicitly authorized to
> test. BlackForge manages packages; it does not provide targets, payloads,
> exploitation workflows, or automatic execution of catalog tools.

BlackForge keeps the official BlackArch website catalog searchable while
delegating package changes to `pacman`. It also includes a small reviewed list
of well-known security packages from official Arch repositories, such as
`nmap`, `masscan`, `hashcat`, `john`, `sqlmap`, and Wireshark.

It does **not** vendor thousands of upstream projects, run arbitrary project
installers, use the AUR, or claim that every listed program executes correctly.

## At a glance

| Evidence snapshot | Count | What it means |
| --- | ---: | --- |
| BlackArch website entries | **2,861** | Catalog rows bundled with BlackForge |
| Published in the audited x86-64 repository | **2,858** | Package appeared in the refreshed 2026-07-29 repository database check |
| Website-listed but absent from that snapshot | **3** | `rtl`, `sr`, and `vega`; not proof their upstreams are dead |
| BlackArch functional categories | **49** | Scanner, webapp, forensic, wireless, and other BlackArch categories |
| Curated official Arch packages | **9** | Reviewed package metadata from official `core`/`extra`/`multilib` only |

**Published is not the same as runtime-tested.** Repository presence proves that
a package was available in a specific snapshot. It does not prove compatibility
with every device, desktop, service, credential, target, or code path.

## Contents

- [Why BlackForge](#why-blackforge)
- [Platform support](#platform-support)
- [Install on Arch Linux](#install-on-arch-linux)
- [Two package sources](#two-package-sources)
- [Maintenance evidence](#maintenance-evidence)
- [Twelve feature areas](#twelve-feature-areas)
- [Common workflows](#common-workflows)
- [Command map](#command-map)
- [Verification on Arch Linux](#verification-on-arch-linux)
- [Trust and safety model](#trust-and-safety-model)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Update and uninstall](#update-and-uninstall)
- [Development](#development)

## Why BlackForge

- **One searchable surface.** Search BlackArch and curated official Arch tools
  together, then inspect their source before installing.
- **Plan before changing the system.** Preview the exact pacman command and any
  available size, dependency, conflict, and disk-space metadata.
- **Keep sources honest.** BlackArch packages remain labeled BlackArch; official
  Arch packages remain explicitly repository-qualified.
- **Prefer reversible decisions.** Transaction history, conservative removal,
  plan-first imports, atomic mirror backups, and bounded resume rules reduce
  surprises.
- **Separate evidence from marketing.** Availability, installation, executable
  presence, and upstream activity are different checks and are reported as such.
- **Stay close to the distribution.** Package installation and removal use
  `pacman`; BlackForge does not replace Arch's package manager.

## Platform support

| Capability | Supported environment |
| --- | --- |
| Install/remove/upgrade/setup | Arch Linux or BlackArch with `pacman` |
| Full-screen TUI and guided menu | Linux terminal |
| Search, list, maintenance view, catalog export | Python 3.10+; package changes still require Arch |
| Remote BlackArch availability audit | Any environment that can run the CLI and reach the HTTPS repository database |
| `install.sh` user installation | Linux |

Arch Linux is the primary supported platform. BlackForge is not a Debian,
Ubuntu, Fedora, macOS, or Windows package manager.

## Install on Arch Linux

### 1. Install the small prerequisites

```bash
sudo pacman -S --needed git python
```

### 2. Install BlackForge for your user

```bash
git clone https://github.com/johnnypatty/blackforge.git
cd blackforge
bash install.sh
```

The installer creates an isolated environment at
`~/.local/share/blackforge`, places the launcher in `~/.local/bin`, and installs
bash, zsh, and fish completions. It does not modify the system Python.

If the command is not found, add the user bin directory to your shell profile
and open a new terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 3. Check the host

```bash
blackforge version
blackforge doctor
```

`doctor` returns a nonzero readiness status until Arch/pacman and, for
BlackArch operations, the BlackArch repository are available.

### 4. Enable BlackArch when you need BlackArch packages

```bash
blackforge setup
```

This downloads BlackArch's official `strap.sh` over HTTPS, verifies the pinned
checksum, shows its digest and first lines, asks for confirmation, and then runs
it with the required privileges. Read [Trust and safety](#trust-and-safety-model)
before overriding a changed checksum.

You can combine first-time setup with an install:

```bash
blackforge install --setup-repo amass
```

Official Arch packages do not require BlackArch setup:

```bash
blackforge --dry-run install arch:extra/nmap
blackforge install arch:extra/nmap
```

### Native package recipe

A self-contained [PKGBUILD guide](packaging/arch/README.md) is also included:

```bash
cd packaging/arch
makepkg -si
```

## Two package sources

BlackForge deliberately keeps the sources separate.

| Source | Scope | Example reference | Installed by |
| --- | --- | --- | --- |
| **BlackArch** | All 2,861 bundled website entries | `amass` or `blackarch:amass` | `pacman` from `[blackarch]` |
| **Official Arch, curated** | Nine reviewed packages from official repositories | `arch:extra/nmap` | `pacman` from `core`/`extra`/`multilib` |

The curated official Arch list currently contains:

`aircrack-ng`, `hashcat`, `john`, `masscan`, `nmap`, `sqlmap`, `tcpdump`,
`wireshark-cli`, and `wireshark-qt`.

Use explicit references in scripts and shared profiles:

```bash
blackforge show blackarch:amass
blackforge show arch:extra/nmap
blackforge install blackarch:amass arch:extra/nmap
blackforge search packet --source arch
blackforge list --source blackarch --category blackarch-forensic
```

The curated list is not a general repository browser and does not enable the
AUR. Its metadata is reviewed and bundled; `pacman` remains authoritative for
the version available on your machine.

## Maintenance evidence

BlackForge exposes two top-level maintenance groups for the **2,861 BlackArch
catalog entries**:

1. **Recently maintained** — verified upstream activity newer than the cutoff.
2. **Needs attention** — stale, archived, or unknown upstream evidence.

The bundled 2026-07-29 snapshot uses a three-year cutoff by default:

| Group | Status | Count | Meaning |
| --- | --- | ---: | --- |
| Recently maintained | `current` | **780** | Verified upstream activity after the three-year cutoff |
| Needs attention | `stale` | **680** | Last verified activity is at least three years old |
| Needs attention | `archived` | **143** | Upstream repository is archived |
| Needs attention | `unknown` | **1,258** | No supported or reachable activity source was verified |
| **Total** |  | **2,861** | Every BlackArch catalog entry is represented |

```bash
blackforge maintenance summary
blackforge maintenance list --group current --limit 30
blackforge maintenance list --group attention --status stale --limit 30
blackforge maintenance list --group attention --status archived
blackforge maintenance summary --stale-years 5
```

The default cutoff is **3 years**; `--stale-years 5` provides the requested
five-year view. `unknown` does not mean stale, broken, or unsafe—it means the
snapshot lacks sufficient supported evidence. “Recently maintained” likewise
does not prove that a tool runs correctly. The evidence uses upstream repository
activity/archive metadata, not automatic tool execution.

## Twelve feature areas

| Area | Commands | Current behavior |
| --- | --- | --- |
| 1. Full-screen terminal UI | `tui` | Search, inspect, select, and produce an install preview with keyboard navigation |
| 2. Installation planning | `plan`, `--dry-run` | Shows the exact pacman argv plus metadata available from pacman |
| 3. Transaction history and undo | `history` | Records completed package changes; undo removes newly installed packages only when that inverse is exact |
| 4. Health and maintenance evidence | `status`, `maintenance`, `show` | Separates repository/install/file checks from upstream activity |
| 5. Reviewed smart collections | `collection` | Mixed BlackArch + official Arch presets; apply is plan-only unless `--apply` is given |
| 6. Bounded retry and resume | `install --retries`, `resume` | Automatically retries only recognized network/download failures and journals any resumable failure within the recorded limit |
| 7. Mirror testing | `mirror` | Lists and probes mirrors, recommends responsive HTTPS, and backs up the list before an actual selection change |
| 8. Catalog update awareness | `updates` | Compares the bundled catalog with the live catalog and stores a report; no background daemon |
| 9. Detailed tool information | `show`, `info` | Displays source, repository, version, install state, website, category, and maintenance evidence |
| 10. Secure self-update | `self-update` | Checks GitHub releases; install-script users can apply a versioned wheel verified against `SHA256SUMS` |
| 11. Safe dry-run and JSON | `--dry-run`, `--json` | Previews state changes and exposes structured results where supported |
| 12. Environment export/import | `env` | Records explicitly installed security packages and plans reproduction without deleting extras |

### Built-in collections

```bash
blackforge collection list
blackforge collection show network-discovery
blackforge collection apply network-discovery
blackforge collection apply network-discovery --apply
```

Included collection IDs:

- `binary-analysis`
- `digital-forensics`
- `network-discovery`
- `packet-analysis`
- `password-audit`
- `web-assessment`
- `wireless-audit`

Collections are reviewed starter sets, not permission to use their tools against
third-party systems.

## Common workflows

### Discover, inspect, plan, install

```bash
blackforge search "subdomain enumeration"
blackforge show amass
blackforge plan install amass
blackforge install amass
```

Install commands also show a plan before prompting. To guarantee no package
change:

```bash
blackforge --dry-run install amass arch:extra/nmap
blackforge --json --dry-run install amass > install-plan.json
```

### Audit BlackArch package availability

```bash
blackforge status --all
blackforge status --all --executables
blackforge --json status --all --remote --output blackforge-audit.json
```

- Local status asks pacman about repository/install state.
- `--executables` checks declared `/usr/bin` files without launching programs.
- `--remote` compares the catalog with the live official x86-64 repository
  database and can run without local pacman.

See the exact [health model](docs/HEALTH-MODEL.md) and bundled
[availability report](reports/README.md).

### Save and reproduce a tool set

For a small reviewed list:

```bash
blackforge profile create web-lab.json amass nuclei arch:extra/nmap
blackforge profile show web-lab.json
blackforge --dry-run profile apply web-lab.json
blackforge profile apply web-lab.json
```

For the security packages explicitly installed on a whole Arch machine:

```bash
blackforge env export workstation.json
blackforge env import workstation.json
blackforge env import workstation.json --apply --allow-newer
```

An environment import ignores extra installed packages. Rolling repositories do
not guarantee historical versions; version drift must be reviewed and
explicitly accepted with `--allow-newer`.

### Recover from an interrupted download

```bash
blackforge resume
blackforge resume --apply
```

Install retries and resume are not blind. The journal allows another attempt
only for recognized network/download failures while attempts remain. Signature, keyring,
permission, dependency, conflict, and disk-space failures are not treated as
transient.

### Review or undo recorded changes

```bash
blackforge history list
blackforge history show TRANSACTION_ID
blackforge history undo TRANSACTION_ID
blackforge history undo TRANSACTION_ID --apply
```

BlackForge never guesses an old rolling-release version. The automatic undo path
is intentionally limited to exact, conservative inverses such as removing a
package newly installed by the selected transaction.

## Command map

| Task | Command |
| --- | --- |
| Clear built-in help | `blackforge help [COMMAND [SUBCOMMAND]]` |
| Search and browse | `search`, `list`, `show`, `categories`, `names` |
| Host/repository readiness | `doctor`, `setup`, `repo` |
| Install and manage packages | `install`, `remove`, `upgrade`, `plan` |
| Check availability/install state | `status` |
| Browse activity evidence | `maintenance` |
| Use reviewed presets | `collection` |
| Inspect/recover transactions | `history`, `resume` |
| Test/select mirrors | `mirror` |
| Compare catalog updates | `updates` |
| Save/reproduce environments | `profile`, `env`, `export` |
| Guided interfaces | `interactive`, `tui` |
| Shell integration and app update | `completion`, `self-update`, `version` |

Start with:

```bash
blackforge help
blackforge help install
blackforge help profile create
blackforge help --all
```

The exhaustive, option-by-option reference is in
**[docs/COMMANDS.md](docs/COMMANDS.md)**.

Aliases are available for convenience:

- `get`, `add` → `install`
- `rm`, `uninstall` → `remove`
- `check` → `status`
- `info` → `show`
- `update-catalog` → `sync`

## Verification on Arch Linux

The CI workflow has a dedicated `archlinux:base-devel` container job. Its live
status is shown by the CI badge at the top of this README.

| Arch smoke step | What it verifies |
| --- | --- |
| Run `bash install.sh` | The isolated Linux user installation completes |
| Run help, source-aware search, maintenance, planning, collections, and completion | The installed launcher and v0.3 read-only features are usable |
| Install official Arch `nmap`, run its version command, verify with `pacman -Q`, then remove it | A real curated official Arch package lifecycle works through BlackForge |
| Enable BlackArch through BlackForge | The official repository setup path completes in the disposable container |
| Install `0trace`, verify with `pacman -Q`, then remove it | One real package lifecycle works through pacman |

This is a focused integration smoke test, not a claim that all 2,861 programs
were launched. If the live CI badge is failing, treat the corresponding
integration result as unverified until the workflow is green again.

## Trust and safety model

### Package operations

- Package names and repository-qualified targets are validated.
- Commands are passed to `subprocess` as argument lists, never through
  `shell=True`.
- BlackForge delegates signed package resolution and transactions to `pacman`.
- Removal uses conservative `pacman -R` by default; `--purge` explicitly opts
  into `pacman -Rns`.
- A no-argument upgrade uses `pacman -Syu`, avoiding unsafe partial upgrades.
- State-changing commands prompt unless `--yes` is supplied.

### Repository setup

- `strap.sh` is downloaded from BlackArch over HTTPS with a size limit.
- BlackForge checks the pinned official checksum and previews the script.
- A changed script fails closed. `--strap-sha256` approves only one exact digest
  and should be used only after manual review.

### Mirrors

- Mirror tests use HTTPS by default and report unsupported schemes.
- Applying a mirror requires an exact listed URL and explicit confirmation.
- The exact mirror-list file is replaced atomically after a timestamped backup;
  an already-selected mirror is a no-op and creates no unnecessary backup.
- Mirror selection does not alter pacman `SigLevel`.

### Self-update

- Release metadata and assets must use HTTPS.
- Download hosts are allowlisted.
- Applying requires a versioned wheel plus `SHA256SUMS`.
- The wheel filename must exactly match the release and the installed
  distribution version is verified after installation.
- Only installations carrying the `install.sh` ownership marker can self-update.

### Data and execution

- Runtime code uses the Python standard library only.
- BlackForge does not launch catalog programs, even with `--help`.
- JSON/environment/profile input is validated and written atomically where
  applicable.
- The installer and uninstaller require absolute XDG paths and reject a
  symbolic-link application root.
- No arbitrary AUR helpers, upstream Git clones, or third-party install recipes
  are executed.

## Limitations

- No universal “working” test exists for this catalog. Tools may require
  hardware, a GUI, licensed data, root, credentials, services, or a dedicated
  authorized lab.
- `available` means published in a repository snapshot, not successfully run.
- `installed-files-ok` checks file presence/executable bits, not program
  behavior.
- Maintenance activity does not prove compatibility or security quality.
- `unknown` maintenance evidence must not be interpreted as stale.
- The curated official Arch list is intentionally small and not a complete
  inventory of Arch security packages.
- Exact downgrade/rollback is not promised on a rolling distribution.
- TUI selection produces a reviewable plan; installation remains an explicit
  command.
- Catalog updates and release checks are user-invoked; BlackForge does not run a
  background notification service.

## Troubleshooting

### `blackforge: command not found`

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to your shell profile and open a new terminal.

### `[blackarch] repository is not enabled`

```bash
blackforge setup
```

Or use `blackforge install --setup-repo PACKAGE` for a BlackArch package.
Official Arch references such as `arch:extra/nmap` do not need this setup.

### Setup reports a checksum change

Do not bypass it automatically. Inspect the downloaded official script and
compare BlackArch's published installation information. Only then may you pass
the exact displayed digest:

```bash
blackforge setup --strap-sha256 EXACT_64_HEX_DIGEST
```

### Confirmation requires a terminal

For automation, first inspect a dry run. Use `--yes` only when the reviewed
operation should proceed without an interactive prompt:

```bash
blackforge --json --dry-run install amass
blackforge --yes install amass
```

### Environment import reports version drift

Rolling repositories may have moved beyond the recorded version. Review the
plan; if current signed repository versions are acceptable:

```bash
blackforge env import workstation.json --apply --allow-newer
```

### Self-update says the installation is unsupported

`self-update --apply` supports only the isolated user installation created by
`install.sh`. Native package users should update through their PKGBUILD/pacman
workflow; development checkouts should update with Git.

### Mirror testing finds no responsive HTTPS mirror

Check connectivity and the selected mirror-list path:

```bash
blackforge mirror list
blackforge mirror test --timeout 10
```

BlackForge does not silently fall back to an insecure scheme.

## Update and uninstall

Check the latest release:

```bash
blackforge self-update --check
```

For an `install.sh` user installation:

```bash
blackforge self-update --apply
```

To remove BlackForge itself from the cloned source directory:

```bash
bash uninstall.sh
```

The uninstaller removes only application-owned files and shell completions. It
leaves every BlackArch and official Arch package you installed untouched.

## Development

```bash
python -m pip install -e .
python -m pip install pytest ruff build
python scripts/update_catalog.py
python scripts/check_release.py
ruff check .
pytest
```

Maintainers can refresh upstream-activity evidence with an authenticated GitHub
CLI session:

```bash
python scripts/update_maintenance.py
```

The maintenance generator uses repository `pushedAt`/archive metadata only; it
does not clone or execute the listed projects.

Useful project documents:

- [Full command reference](docs/COMMANDS.md)
- [Health model](docs/HEALTH-MODEL.md)
- [Package-availability evidence](reports/README.md)
- [Native Arch packaging](packaging/arch/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [MIT license](LICENSE)

BlackForge depends on the work of the
[BlackArch](https://www.blackarch.org/) and
[Arch Linux](https://archlinux.org/) communities. Please report BlackForge
bugs to this repository rather than to those upstream projects.
