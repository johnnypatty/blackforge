# Changelog

All notable BlackForge changes are recorded here.

## Unreleased

## 0.4.0 - 2026-08-01

### Added

- Added `blackforge audit` with distinct outdated, vulnerable, unavailable,
  and keyring states plus optional official `arch-audit` advisory integration.
- Added exact package lockfiles, cross-machine drift comparison, cached archive
  checksums, and CycloneDX 1.5 / SPDX 2.3 SBOM export.
- Added Btrfs/Snapper readiness detection, opt-in pre-transaction snapshots,
  and non-executing exact-version rollback plans from pacman's cache.
- Added strict data-only community presets, three reviewed starter sets, CLI
  validation/planning, a JSON schema, contribution instructions, CI checks,
  and a guided GitHub issue form.
- Added opt-in, read-only AUR RPC metadata discovery. BlackForge deliberately
  does not download, build, or execute PKGBUILDs.
- Added portable maintenance dashboards with bounded historical observations,
  repository availability, catalog deltas, and maintenance evidence counts.
- Added a man page, optional disabled systemd user timer, PackageKit-style JSON
  status output, Turkish quick help, and `arch-audit` to the curated official
  Arch catalog.
- Added a responsive GitHub Pages project site, searchable wiki, live reviewed
  preset gallery, deterministic site builder, link checker, and deployment CI.

### Changed

- Install, upgrade, built-in collection, and community collection operations
  accept explicit `--snapshot` protection when Snapper is configured.
- The Linux installer installs the man page, while the native Arch recipe also
  ships the optional systemd units and documents optional integrations.
- README and command documentation now cover all v0.4 commands, verification
  boundaries, community review, project website, and safety behavior.

### Security

- Community presets reject executable fields, remote URLs, unknown packages,
  unqualified sources, duplicates, excessive sizes, and unreviewed release data.
- AUR requests are bounded to the official HTTPS RPC endpoint and expose
  metadata only.
- Rollback support never silently restores a filesystem or auto-downgrades a
  package; it creates snapshots or prints complete, reviewable package plans.
- The Pages workflow validates community data and local links before deploying,
  uses minimal permissions, and keeps page deployment in a separate job.

### Verification

- Added v0.4 unit and CLI tests for presets, SBOMs, lock drift, cache rollback,
  dashboards, AUR opt-in, localization, and read-only planning.
- Added desktop and mobile browser checks for layout, navigation, preset/wiki
  filtering, copy behavior, horizontal overflow, and console errors.

## 0.3.0 - 2026-07-29

### Added

- Added a full-screen searchable terminal interface plus the existing guided
  menu.
- Added non-mutating install/remove/upgrade plans with exact pacman arguments,
  resolved metadata, size estimates, conflicts, and disk-space warnings where
  pacman exposes them.
- Added transaction history, state-checked conservative undo, bounded automatic
  retries, and manual resume for recognized network/download failures.
- Added BlackArch mirror listing, reachability testing, HTTPS recommendation,
  and atomic selection with timestamped backups.
- Added on-demand catalog change reports and a checksum-verified GitHub release
  self-updater for installations owned by `install.sh`.
- Added environment export/import with source-qualified packages, plan-first
  reproduction, version-drift review, and no deletion of extra packages.
- Added nine curated security tools from official Arch repositories, including
  `nmap`, `masscan`, `aircrack-ng`, `hashcat`, `john`, `sqlmap`, `tcpdump`, and
  Wireshark packages.
- Added seven reviewed mixed-source collections for network discovery, web and
  wireless assessment, password auditing, packet analysis, forensics, and
  binary analysis.
- Added a complete maintenance-evidence snapshot for all 2,861 BlackArch
  catalog rows. The two primary views are Recently maintained and Needs
  attention, with explicit current/stale/archived/unknown evidence states and
  selectable three- or five-year cutoffs.
- Added `blackforge help [COMMAND [SUBCOMMAND]]`, `help --all`, clearer
  source-aware search/details, global-option placement after subcommands, and a
  complete command reference.

### Changed

- `--dry-run` now consistently prevents package and file mutations, including
  catalog/report/profile/environment exports.
- Official Arch and BlackArch identities remain source-qualified through
  planning, profiles, environments, history, and resume.
- The README now documents installation, sources, maintenance evidence,
  workflows, every feature area, the trust model, verification boundaries, and
  troubleshooting.
- Persistent history and transaction read/modify/write cycles are serialized
  with cross-process state locks and bounded by file/package safety limits.
- Pacman output is streamed to the terminal and retained for conservative
  transient-failure classification; successful resumed operations now create
  the same before/after history evidence as first-attempt operations.

### Security

- Self-update now restricts initial and redirected metadata/assets to explicit
  GitHub HTTPS host allowlists, rejects path-like or duplicate asset names,
  rejects ambiguous checksum entries, and installs verified wheels without an
  index.
- Environment imports, history undo, and transaction resume re-resolve claimed
  package sources before mutation, preventing a core Arch package from being
  disguised as a BlackArch package.
- Automatic undo refuses partial rollbacks and refuses removal when a package's
  installed version changed after the recorded transaction.
- Mirror approval requires the literal boolean value and a no-op selection no
  longer rewrites the file or creates a backup.
- Catalog, repository database, and setup-script downloads reject credentialed,
  non-HTTPS, and untrusted cross-host redirects.
- Persisted update reports now use strict shapes, types, timestamps, counts,
  duplicate checks, and size ceilings.
- Transaction state transitions now validate and write under one lock, so
  concurrent complete/fail/resume requests cannot overwrite a newer state.
- Release jobs use pinned GitHub Action commits, keep write permission out of
  the build/test job, bind the tag to the package version, and exhaustively
  validate the native source archive before publication.
- Self-update requires the exact versioned BlackForge wheel and verifies the
  installed distribution version after installation.
- The Linux installer and uninstaller reject relative XDG paths, unsafe
  canonical roots, and symbolic-link installation directories.

### Verification

- The complete local suite passes on Windows with one symlink-only case skipped
  when the host cannot create symlinks.
- GitHub Actions tests Python 3.10, 3.12, and 3.13 and runs a disposable Arch
  Linux package lifecycle smoke test. Exact run evidence is linked from the
  repository's Actions page.

## Bug-fix checkpoint - 2026-07-29

### Fixed

- Corrected the Arch Linux CI smoke test to use the installer's actual
  `$HOME/.local/bin` destination.
- Made the Linux installer check for conflicting launchers before changing the
  installation directory.
- Converted command-launch permission and operating-system failures into clear
  BlackForge errors instead of Python tracebacks.
- Rejected option-like package names and separated pacman options from package
  operands with `--`.
- Replaced the boolean `strap.sh` checksum bypass with approval of one exact,
  manually reviewed SHA-256, and stopped mismatches from being displayed as a
  successful checksum match.
- Made local status return a nonzero result when verification was unavailable
  or the BlackArch repository was not enabled.
- Stopped executable health checks from treating failed `pacman -Ql` queries as
  packages with no command-line program.
- Normalized local repository versions before comparing them with website
  versions, avoiding false mismatch notices caused by pacman epochs/pkgrel.
- Rejected malformed catalogs, case-insensitive duplicate package names,
  malformed profiles, empty profiles, and invalid list/search limits.
- Rejected ambiguous install selections instead of silently ignoring a
  simultaneously supplied category or profile.
- Allowed safe removal of locally installed packages that disappeared from the
  current website catalog.
- Followed active pacman `Include` files when detecting the BlackArch repository.
- Kept read-only package queries active during dry-run planning.
- Added clean handling for interrupted commands and closed output pipes.

### Security

- Deduplicated validated package arguments while preserving their order.
- Made profile writes atomic so interrupted writes do not leave truncated files.
- Added installation ownership checks so the uninstaller cannot recursively
  remove an unrelated directory that happens to use the same name.
- Added size ceilings for downloaded catalogs, repository databases, setup
  scripts, and repository metadata members.
- Made catalog, profile, audit, JSON export, and CSV export writes atomic.
- Centralized network User-Agent versions and restored the `update-catalog`
  shell-completion alias.
