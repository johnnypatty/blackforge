# BlackForge command reference

This reference matches the current BlackForge parser. For the shorter
installation and workflow guide, start with the [README](../README.md).

```text
blackforge [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

The CLI normalizes global options, so they may appear before or after a
subcommand. Putting them first is clearest in scripts.

```bash
blackforge --json --dry-run install amass
blackforge install amass --dry-run --json
```

## Global options

| Option | Meaning |
| --- | --- |
| `-h`, `--help` | Show argparse help for the current parser |
| `--version` | Print the installed version and exit |
| `--catalog PATH` | Use a specific BlackArch catalog JSON file instead of the cached/bundled catalog |
| `--json` | Emit structured JSON where the selected command supports it |
| `--dry-run` | Preview without changing packages, mirrors, saved reports, profiles, exports, catalogs, environments, history, or the application |
| `-y`, `--yes` | Skip BlackForge confirmation and pass `--noconfirm` to pacman operations |

`--yes` is intended for reviewed automation. Use `--dry-run` first.

## Package references and sources

BlackForge has two deliberately separate sources:

| Form | Meaning |
| --- | --- |
| `amass` | Bare package name; resolves against BlackArch first, then the curated official Arch list |
| `blackarch:amass` | Explicit BlackArch catalog reference |
| `arch:extra/nmap` | Explicit curated package from Arch's official `extra` repository |
| `extra/nmap` | Short repository-qualified official Arch form |

Explicit source-qualified references are recommended in scripts, profiles, and
shared environments. The curated Arch source is limited to reviewed packages
from official `core`, `extra`, and `multilib`; it is not an AUR interface.

## Command index

### Help and version

- [`help`](#blackforge-help)
- [`version`](#blackforge-version)

### Discovery and evidence

- [`list`](#blackforge-list)
- [`names`](#blackforge-names)
- [`search`](#blackforge-search)
- [`show` / `info`](#blackforge-show)
- [`categories`](#blackforge-categories)
- [`doctor`](#blackforge-doctor)
- [`status` / `check`](#blackforge-status)
- [`maintenance`](#blackforge-maintenance)
- [`audit`](#blackforge-audit)
- [`aur`](#blackforge-aur)

### Package and repository operations

- [`setup`](#blackforge-setup)
- [`sync` / `update-catalog`](#blackforge-sync)
- [`install` / `get` / `add`](#blackforge-install)
- [`remove` / `rm` / `uninstall`](#blackforge-remove)
- [`upgrade`](#blackforge-upgrade)
- [`repo`](#blackforge-repo)
- [`plan`](#blackforge-plan)

### Reproducibility and recovery

- [`profile`](#blackforge-profile)
- [`env`](#blackforge-env)
- [`collection`](#blackforge-collection)
- [`community`](#blackforge-community)
- [`lock`](#blackforge-lock)
- [`snapshot`](#blackforge-snapshot)
- [`history`](#blackforge-history)
- [`resume`](#blackforge-resume)

### Mirrors, updates, and interfaces

- [`mirror`](#blackforge-mirror)
- [`updates`](#blackforge-updates)
- [`self-update`](#blackforge-self-update)
- [`export`](#blackforge-export)
- [`completion`](#blackforge-completion)
- [`interactive`](#blackforge-interactive)
- [`tui`](#blackforge-tui)
- [`dashboard`](#blackforge-dashboard)
- [`integration`](#blackforge-integration)

## `blackforge help`

```text
blackforge help
blackforge help COMMAND
blackforge help COMMAND SUBCOMMAND
blackforge help --all
blackforge help --lang tr
```

| Argument/option | Meaning |
| --- | --- |
| `COMMAND [SUBCOMMAND ...]` | Print help for that exact parser path |
| `--all` | Print root help followed by every canonical command and subcommand |
| `--lang en\|tr` | Show the English parser help or Turkish quick guide |

Examples:

```bash
blackforge help install
blackforge help profile create
blackforge help mirror apply
```

`blackforge --help` is the standard argparse equivalent for root help.

## `blackforge version`

```text
blackforge version
blackforge --version
```

Both forms print the installed BlackForge version.

## `blackforge setup`

```text
blackforge setup [--strap-sha256 SHA256]
```

Enables and initializes the official BlackArch repository. BlackForge
downloads the official `strap.sh` over HTTPS, verifies the pinned checksum,
prints its SHA-256 and first 12 lines, asks for confirmation, and invokes it
with the required privileges.

| Option | Meaning |
| --- | --- |
| `--strap-sha256 SHA256` | Approve one manually reviewed changed script by its exact 64-hex SHA-256 |

The checksum override is a fail-closed review mechanism, not a convenience
flag. `blackforge --dry-run setup` downloads, verifies, and previews the script
without executing it.

Equivalent repository command:

```bash
blackforge repo enable
```

## `blackforge sync`

```text
blackforge sync [--url URL] [--output PATH]
```

Downloads and validates the BlackArch website catalog.

| Option | Default | Meaning |
| --- | --- | --- |
| `--url URL` | Official BlackArch tools URL | Override the catalog source |
| `--output PATH` | User catalog cache | Write the validated catalog elsewhere |

Alias: `update-catalog`.

This updates catalog metadata, not installed packages. Use `upgrade` for
packages and `updates check` for a change report.

## `blackforge list`

```text
blackforge list [--category CATEGORY] [--source all|blackarch|arch] [--limit N]
```

Lists tools from one or both sources.

| Option | Default | Meaning |
| --- | --- | --- |
| `--category CATEGORY` | All categories | Filter by one exact category |
| `--source all\|blackarch\|arch` | `all` | Select both, BlackArch only, or curated official Arch only |
| `--limit N` | `100` | Positive maximum row count |

Examples:

```bash
blackforge list --source arch
blackforge list --source blackarch --category blackarch-forensic --limit 30
blackforge --json list --source arch
```

## `blackforge names`

```text
blackforge names [--prefix TEXT] [--category CATEGORY] [--categories]
```

Prints newline-delimited package or category names for scripts and shell
completion.

| Option | Meaning |
| --- | --- |
| `--prefix TEXT` | Keep names starting with this case-insensitive prefix |
| `--category CATEGORY` | Print package names in one category |
| `--categories` | Print category names instead of package names |

This is intentionally plain text, even when the global `--json` flag is
present.

## `blackforge search`

```text
blackforge search WORDS... [--category CATEGORY]
                  [--source all|blackarch|arch] [--limit N]
```

Searches names, descriptions, categories, and documented command names where
available.

| Option | Default | Meaning |
| --- | --- | --- |
| `--category CATEGORY` | All categories | Restrict the search |
| `--source all\|blackarch\|arch` | `all` | Select package source |
| `--limit N` | `50` | Positive maximum result count |

Examples:

```bash
blackforge search "network mapper"
blackforge search packet --source arch
blackforge --json search forensics --limit 10
```

No matches return exit status `1`.

## `blackforge show`

```text
blackforge show PACKAGE
blackforge info PACKAGE
```

Shows one package's description, source, repository, version, installed state,
category, website, and maintenance evidence.

Examples:

```bash
blackforge show blackarch:amass
blackforge show arch:extra/nmap
blackforge --json info amass
```

BlackArch maintenance evidence is an upstream-activity signal, not a runtime
test. Curated Arch entries use their official Arch package listing as source
evidence.

Alias: `info`.

## `blackforge categories`

```text
blackforge categories [--source all|blackarch|arch]
```

Lists category counts with their source label.

| Option | Default | Meaning |
| --- | --- | --- |
| `--source all\|blackarch\|arch` | `all` | Select source |

## `blackforge doctor`

```text
blackforge doctor
```

Reports OS, architecture, Python version, pacman/sudo presence, BlackArch
repository state, and catalog metadata.

- Exit `0`: Arch/pacman is supported and `[blackarch]` is configured.
- Exit `2`: one or more readiness conditions are missing.

An official Arch-only install may still work when `[blackarch]` is not enabled.

## `blackforge status`

```text
blackforge status PACKAGE...
blackforge status --all
blackforge status --all --executables
blackforge status --all --remote
blackforge status --all --repo-db PATH
blackforge status --all --output REPORT.json
```

Audits BlackArch catalog packages. Provide package names or `--all`.

| Option | Meaning |
| --- | --- |
| `--all` | Audit every bundled BlackArch catalog entry |
| `--executables` | For installed packages, check declared `/usr/bin` files without launching them |
| `--remote` | Compare with the live official x86-64 BlackArch repository database |
| `--repo-db PATH` | Compare with an already downloaded `blackarch.db` |
| `--output PATH` | Atomically save the complete JSON report |

`--remote` and `--repo-db` are mutually exclusive. `--executables` is local
only and cannot be combined with either repository-snapshot option.

Alias: `check`.

Status semantics:

| Status | Proven observation |
| --- | --- |
| `available` | Package appears in the selected repository database |
| `installed` | Pacman reports the package installed |
| `installed-files-ok` | Declared `/usr/bin` entries exist and are executable |
| `installed-no-cli` | Package declares no `/usr/bin` entry |
| `installed-files-missing` | At least one declared executable is absent/non-executable |
| `missing-from-repo` | Website catalog lists it but the selected repository database does not |
| `repo-not-enabled` | Pacman is present but `[blackarch]` is not configured |
| `unverified` | Local pacman evidence is unavailable |

No catalog program is launched.

## `blackforge install`

```text
blackforge install PACKAGE... [--retries N] [--setup-repo] [--snapshot]
blackforge install --category CATEGORY [--retries N] [--setup-repo] [--snapshot]
blackforge install --profile PATH [--retries N] [--setup-repo] [--snapshot]
```

Exactly one selector may be used: package names, `--category`, or `--profile`.
The command resolves source-qualified targets, prints a transaction plan, asks
for confirmation, and delegates installation to pacman.

| Option | Default | Meaning |
| --- | --- | --- |
| `--category CATEGORY` | — | Install every package in one category |
| `--profile PATH` | — | Install package references from a saved profile |
| `--retries N` | `2` | Automatically retry recognized network/download failures at most N times; accepted range is 0–9 |
| `--setup-repo` | Off | Enable BlackArch first when a selected BlackArch package needs it |
| `--snapshot` | Off | Require and create a configured Snapper `root` snapshot immediately before pacman |

Examples:

```bash
blackforge --dry-run install amass arch:extra/nmap
blackforge install --setup-repo amass
blackforge install arch:extra/nmap
blackforge install --profile web-lab.json
```

Official Arch repository-qualified targets do not require BlackArch setup.

Aliases: `get`, `add`.

## `blackforge remove`

```text
blackforge remove PACKAGE... [--purge]
```

Validates package references, confirms that each resolved package is installed,
prints a plan, asks for confirmation, and delegates to pacman.

| Option | Pacman operation | Meaning |
| --- | --- | --- |
| Default | `-R` | Remove selected packages conservatively |
| `--purge` | `-Rns` | Also remove now-unused dependencies and configuration backups |

Always review a `--purge` dry run:

```bash
blackforge --dry-run remove amass --purge
```

Aliases: `rm`, `uninstall`.

## `blackforge upgrade`

```text
blackforge upgrade [--snapshot] [PACKAGE...]
```

- With no names, performs a full `pacman -Syu`.
- With names, upgrades/reinstalls only the selected validated packages with
  `pacman -S --needed`.

The no-name form is the safe Arch full-system upgrade path.
`--snapshot` creates a configured Snapper `root` snapshot after confirmation
and immediately before pacman runs.

```bash
blackforge --dry-run upgrade
blackforge upgrade
blackforge upgrade amass
```

## `blackforge repo`

```text
blackforge repo status
blackforge repo enable [--strap-sha256 SHA256]
```

### `repo status`

Reports whether the BlackArch repository is configured and returns:

- `0` when enabled
- `2` when not enabled

JSON returns both `enabled` and `supported`.

### `repo enable`

Equivalent to `setup`. See [`blackforge setup`](#blackforge-setup).

## `blackforge plan`

```text
blackforge plan install PACKAGE...
blackforge plan remove PACKAGE... [--purge]
blackforge plan upgrade [PACKAGE...]
```

Creates a read-only plan and never executes the pacman command. Install and
remove require a package; an upgrade plan may omit names to preview `pacman
-Syu`.

Plans include:

- operation and requested packages
- exact pacman argument list
- package/repository metadata when available
- resolved dependency/conflict metadata when available
- download and installed-size totals when available
- free disk and warnings

Use `--json` for a structured plan.

## `blackforge profile`

```text
blackforge profile create PATH PACKAGE... [--name NAME]
blackforge profile show PATH
blackforge profile apply PATH
```

### `profile create`

Validates and saves a small reproducible package-reference list.

| Option | Meaning |
| --- | --- |
| `--name NAME` | Override the profile name; otherwise use the output filename stem |

### `profile show`

Validates and displays the saved package targets. Supports `--json`.

### `profile apply`

Plans, confirms, and installs the profile. Use global `--dry-run` to guarantee
no package change.

## `blackforge env`

```text
blackforge env export PATH
blackforge env import PATH [--apply] [--allow-newer]
```

Environment export and apply require Arch/pacman. A plan-only `env import`
can validate and inspect a manifest on any Python 3.10+ host.

### `env export`

Records explicitly installed BlackArch and curated official Arch packages,
including their installed versions, in an atomically written JSON manifest.

### `env import`

Validates a manifest and compares it with the current machine.

| Option | Meaning |
| --- | --- |
| `--apply` | Apply the reviewed install plan; without it import is plan-only |
| `--allow-newer` | Accept current rolling-repository versions when packages must be installed or recorded versions have drifted |

Import never removes extra packages. Exact historical versions are not
promised.

## `blackforge collection`

```text
blackforge collection list
blackforge collection show NAME
blackforge collection apply NAME [--apply] [--snapshot]
```

Built-in collection IDs:

- `binary-analysis`
- `digital-forensics`
- `network-discovery`
- `packet-analysis`
- `password-audit`
- `web-assessment`
- `wireless-audit`

`collection apply` is plan-only by default. `--apply` performs the reviewed
install after confirmation. Mixed collections require BlackArch to have been
enabled first. `--snapshot` creates a configured Snapper snapshot immediately
before installation.

## `blackforge maintenance`

```text
blackforge maintenance summary [--stale-years 3|5]
blackforge maintenance list [--group current|attention]
                                [--status current|stale|unknown|archived]
                                [--stale-years 3|5] [--limit N]
```

Maintenance commands cover the 2,861 BlackArch catalog entries.

### `maintenance summary`

Shows counts for:

- Recently maintained (`current`)
- Needs attention (`stale`, `archived`, and `unknown`)

### `maintenance list`

| Option | Default | Meaning |
| --- | --- | --- |
| `--group current\|attention` | `attention` | Select a top-level maintenance view |
| `--status current\|stale\|unknown\|archived` | All statuses in the selected group | Select an evidence status |
| `--stale-years 3\|5` | `3` | Reclassify date-based evidence with a three- or five-year cutoff |
| `--limit N` | `100` | Positive maximum result count |

Use `--group current --status current` or `--group attention` with
`stale`/`archived`/`unknown`. Incompatible group/status combinations fail with
a clear validation error. When `--group` is omitted, `--status current`
automatically selects the current group; the other statuses select attention.

The maintenance label is upstream activity evidence, not runtime validation.

## `blackforge audit`

```text
blackforge audit [--output REPORT.json]
```

Runs a read-only Arch host audit and keeps these states separate:

- `outdated`: pacman reports a newer configured-repository version.
- `vulnerable`: the optional official `arch-audit --json` helper reports an
  advisory from `security.archlinux.org`.
- `unavailable`: an installed package is absent from configured repository
  listings.
- keyring health: `archlinux-keyring` and `pacman-key --list-keys` are checked.

When `arch-audit` is absent, the report is still valid and includes the exact
optional install command. Upstream maintenance is deliberately reported by
`maintenance`, not relabeled as a vulnerability.

## `blackforge aur`

```text
blackforge aur --enable-aur search QUERY [--limit N]
blackforge aur --enable-aur info PACKAGE
```

Queries the official AUR RPC v5 for maintainer, age, votes, popularity,
version, and out-of-date metadata. `--enable-aur` is required on each request.
BlackForge has no AUR install command and never downloads, builds, or executes
a `PKGBUILD`.

## `blackforge community`

```text
blackforge community list
blackforge community show ID
blackforge community validate PATH
blackforge community apply ID [--apply] [--snapshot]
```

Release-reviewed community presets are data-only JSON with source-qualified
packages. `apply` is plan-only by default. `validate` accepts an unreviewed
local contribution but still rejects executable fields, unknown packages,
duplicates, oversize files, and unqualified sources.

## `blackforge lock`

```text
blackforge lock create PATH [PACKAGE...]
blackforge lock compare PATH
blackforge lock sbom PATH OUTPUT [--format cyclonedx|spdx]
```

`create` records exact installed versions for recognized security packages,
their trusted source/repository, and the exact package archive SHA-256 when
that archive remains in pacman's cache. With no package names, non-security
packages are counted and skipped. `compare` reports matched, missing, and
version-drift states. `sbom` exports CycloneDX 1.5 or SPDX 2.3 JSON.

## `blackforge snapshot`

```text
blackforge snapshot status
blackforge snapshot create [--description TEXT] [--apply]
blackforge snapshot rollback-plan LOCKFILE [--cache PATH]
```

`status` detects a Btrfs root, Snapper, and the `root` configuration. Snapshot
creation is plan-only unless `--apply` is supplied. `rollback-plan` locates
exact signed package archives in pacman's cache and prints a complete or
partial `pacman -U` plan; it never executes a downgrade.

## `blackforge dashboard`

```text
blackforge dashboard build REPORT.html [--record] [--history PATH]
```

Builds a portable, script-free HTML report with maintenance counts, current
BlackArch repository availability when it can be checked, catalog deltas, and
up to 365 recorded observations.

## `blackforge integration`

```text
blackforge integration systemd DIRECTORY
blackforge --json integration packagekit
```

`systemd` generates a disabled weekly user service/timer for review. It never
enables the timer. `packagekit` emits PackageKit-style package IDs and
installed/available states; BlackForge is not a PackageKit backend.

## `blackforge history`

```text
blackforge history list [--limit N]
blackforge history show TRANSACTION_ID
blackforge history undo TRANSACTION_ID [--apply]
```

### `history list`

Lists newest recorded package transactions first. `--limit` defaults to `25`
and must be positive.

### `history show`

Displays package-level before/after versions for one transaction.

### `history undo`

Produces a conservative inverse plan. Without `--apply`, no package is changed.
When applied, automatic undo is limited to newly installed packages that can be
removed exactly. BlackForge does not guess downgrade versions.

Global `--dry-run` overrides `--apply`; `--yes` skips the final confirmation.

## `blackforge resume`

```text
blackforge resume [TRANSACTION_ID] [--apply]
```

Inspects the newest failed transaction.

| Option | Default | Meaning |
| --- | --- | --- |
| `TRANSACTION_ID` | Newest failed transaction | Inspect a specific recorded transaction |
| `--apply` | Off | Resume the recorded remaining package operation when allowed |

Without `--apply`, the command reports whether the transaction can resume.
Only recognized network/download failures with attempts remaining are
resumable. Security, keyring, dependency, conflict, permission, and disk-space
failures are not automatically retried.

## `blackforge mirror`

Default mirror-list path:

```text
/etc/pacman.d/blackarch-mirrorlist
```

### `mirror list`

```text
blackforge mirror list [--path PATH]
```

Lists enabled state, scheme, support state, URL, and any reason a mirror cannot
be tested.

### `mirror test`

```text
blackforge mirror test [--path PATH] [--timeout N]
```

Tests mirror endpoints concurrently.

| Option | Default | Meaning |
| --- | --- | --- |
| `--path PATH` | Default BlackArch mirror list | Read another exact mirror-list file |
| `--timeout N` | `5` seconds | Positive per-request timeout |

HTTPS is required by default. Unsupported schemes remain visible in the result.
Exit `3` means no test succeeded.

### `mirror recommend`

```text
blackforge mirror recommend [--path PATH]
```

Tests the list with the default timeout and prints the fastest successful HTTPS
mirror. It does not modify the file.

### `mirror apply`

```text
blackforge mirror apply URL [--path PATH]
```

Enables one URL already present in the mirror list and comments the other
server entries. When a change is needed, it creates a timestamped atomic backup
and atomically replaces the exact file. An already-selected URL is a no-op.
The file must be named `blackarch-mirrorlist`; symbolic links are rejected.
`SigLevel` content is not changed.

Use a dry run first:

```bash
blackforge --dry-run mirror apply 'https://mirror.example/$repo/os/$arch'
```

Only HTTPS selections are applied by the CLI.

## `blackforge updates`

```text
blackforge updates check [--url URL]
blackforge updates show
```

### `updates check`

Downloads the live catalog, compares it with the bundled release catalog, saves
a report, and displays counts for added, removed, and version-changed entries.
With global `--dry-run`, it performs the comparison without saving the report.

| Option | Default | Meaning |
| --- | --- | --- |
| `--url URL` | Official BlackArch tools URL | Override the live catalog source |

### `updates show`

Displays the last saved change report without downloading a new catalog.

This is an on-demand report, not a background notification daemon.

## `blackforge self-update`

```text
blackforge self-update
blackforge self-update --check
blackforge self-update --apply
```

`--check` is the explicit check form; no mode flag also checks. The two mode
flags are mutually exclusive.

Applying an update:

- supports only user installations created by `install.sh`
- requires an update to be available
- requires a versioned wheel and `SHA256SUMS`
- restricts downloads to allowlisted GitHub HTTPS hosts
- verifies SHA-256 before invoking the installation's isolated Python

Native package and Git-checkout users should update through those workflows.
Global `--dry-run` makes `--apply` check-only.

## `blackforge export`

```text
blackforge export PATH [--format json|csv]
```

Exports the **BlackArch catalog**; it does not export the curated Arch list or
the machine's installed environment.

| Option | Default | Meaning |
| --- | --- | --- |
| `--format json\|csv` | `json` | Output encoding |

Use `env export` to record installed security packages.

## `blackforge completion`

```text
blackforge completion bash|zsh|fish
```

Prints a completion script to standard output. `install.sh` installs generated
completions automatically.

Manual examples:

```bash
blackforge completion bash > ~/.local/share/bash-completion/completions/blackforge
blackforge completion zsh > ~/.local/share/zsh/site-functions/_blackforge
blackforge completion fish > ~/.config/fish/completions/blackforge.fish
```

## `blackforge interactive`

```text
blackforge interactive
```

Opens the lightweight numbered menu for search, category browsing, doctor, and
repository status. Running `blackforge` with no command in a terminal opens
this menu; without a terminal it prints root help.

## `blackforge tui`

```text
blackforge tui
```

Opens the full-screen terminal browser across BlackArch and curated official
Arch entries.

| Key | Action |
| --- | --- |
| Arrow keys or `j`/`k` | Move |
| `/` | Search |
| Space | Select |
| `i` | Show details |
| Enter | Continue to a safe install preview |
| `q` | Quit |

The TUI returns a plan and a suggested explicit install command; it does not
silently install the selected packages.

## Aliases

| Alias | Canonical command |
| --- | --- |
| `get`, `add` | `install` |
| `rm`, `uninstall` | `remove` |
| `check` | `status` |
| `info` | `show` |
| `update-catalog` | `sync` |

## Exit statuses

| Status | General meaning |
| ---: | --- |
| `0` | Command completed or a plan/check completed without the command-specific problem state |
| `1` | User cancelled, or search returned no matches |
| `2` | Validation, unsupported platform, missing setup, configuration, or readiness error |
| `3` | Audit/recovery/probe completed but found a problem, such as missing packages, no successful mirror, or a non-resumable transaction |
| `130` | Interrupted with Ctrl+C |

Pacman-backed operations may return pacman's nonzero exit status when the
package manager itself fails.

## Safety reminders

- Repository availability is not proof that a program runs.
- Upstream activity is not a compatibility or security audit.
- `--executables` checks files without launching tools.
- `--purge`, `--apply`, and `--yes` deserve deliberate review.
- Use security tooling only on systems you own or are explicitly authorized to
  test.
