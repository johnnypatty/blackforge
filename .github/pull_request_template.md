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
- [ ] Relevant Arch Linux behavior was tested or clearly marked unverified

## Documentation

- [ ] Built-in help and `docs/COMMANDS.md` match.
- [ ] README/changelog were updated for user-visible changes.
