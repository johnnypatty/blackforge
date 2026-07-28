# Command reference

```text
blackforge setup
blackforge sync [--output PATH]
blackforge list [--category CATEGORY] [--limit N]
blackforge names [--prefix TEXT] [--category CATEGORY] [--categories]
blackforge search WORDS... [--category CATEGORY] [--limit N]
blackforge show PACKAGE
blackforge categories
blackforge doctor
blackforge status PACKAGE... [--executables]
blackforge status --all [--executables] [--output REPORT.json]
blackforge status --all --remote [--output REPORT.json]
blackforge install PACKAGE...
blackforge install --setup-repo PACKAGE...
blackforge install --category blackarch-forensic
blackforge install --profile lab.json
blackforge remove PACKAGE... [--purge]
blackforge upgrade [PACKAGE...]
blackforge repo status
blackforge repo enable
blackforge profile create lab.json PACKAGE...
blackforge profile show lab.json
blackforge profile apply lab.json
blackforge export catalog.json --format json
blackforge export catalog.csv --format csv
blackforge completion bash|zsh|fish
blackforge interactive
```

Global controls:

- `--dry-run` previews package-changing operations.
- `--yes` skips BlackForge's prompt and passes `--noconfirm` to pacman.
- `--json` returns structured output where supported.
- `--catalog PATH` uses a pinned catalog snapshot.

Aliases:

- `get` and `add` → `install`
- `rm` and `uninstall` → `remove`
- `check` → `status`
- `update-catalog` → `sync`
