## What changed

Describe the user-visible behavior and why it is needed.

## Safety

- [ ] Package/file mutations remain explicit and reviewable.
- [ ] Inputs passed to pacman or the filesystem are validated.
- [ ] Dry-run performs no mutation.
- [ ] No catalog security tool is executed by tests.

## Verification

- [ ] `ruff check .`
- [ ] `pytest`
- [ ] `python scripts/check_release.py` when release data changed
- [ ] `python scripts/validate_community.py` when community data changed
- [ ] `python scripts/build_site.py _site` when site or wiki files changed
- [ ] Relevant Arch Linux behavior was tested or clearly marked unverified

## Documentation

- [ ] Built-in help and `docs/COMMANDS.md` match.
- [ ] README/changelog were updated for user-visible changes.
- [ ] Community presets contain only source-qualified package data—no commands, hooks, URLs, or scripts.
