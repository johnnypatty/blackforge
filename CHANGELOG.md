# Changelog

All notable BlackForge changes are recorded here.

## Unreleased

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
