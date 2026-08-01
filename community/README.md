# BlackForge community presets

Community presets are small, reviewable JSON files that group packages for a purpose. They cannot run commands, add repositories, download files, or define hooks.

## Share a preset

1. Copy one file from `community/presets/`.
2. Choose a unique lowercase ID and use your GitHub handle in `authors`.
3. Use only source-qualified package references: `blackarch:NAME` or `arch:REPOSITORY/NAME`.
4. Keep `reviewed` set to `false` in a new contribution. A maintainer changes it after review.
5. Run `python scripts/validate_community.py` and open a pull request.

You can also start with the **Community preset** issue form. The project does not accept presets containing executable commands, scripts, remote URLs, credentials, offensive automation, or tools that are absent from BlackForge's trusted catalogs.

## Local validation

```bash
blackforge community validate community/presets/my-preset.json
python scripts/validate_community.py
```

Review is not an endorsement of any target or activity. Use security tools only on systems you own or have explicit authorization to test.
